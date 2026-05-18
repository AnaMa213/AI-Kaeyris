# Jalon 5 — `kaeyris-jdr` : premier service métier complet

> Walkthrough pédagogique, focalisé sur les **décisions** et les **pièges**. Pour le détail technique ligne par ligne, voir les artefacts Spec Kit dans [`specs/001-kaeyris-jdr/`](../specs/001-kaeyris-jdr/) et l'[ADR 0006](./adr/0006-jdr-service.md).

---

## 1. Objectif et nouveauté

Le Jalon 5 livre le premier vrai service métier de la plateforme : un assistant pour des sessions de jeu de rôle qui ingère un audio M4A, produit une transcription diarisée, puis trois familles d'artefacts à la demande du MJ (`narrative`, `elements`, `pov:<pj_id>`) consultables par les joueurs en lecture seule.

C'est aussi le jalon le plus structurant depuis le Jalon 0 :
- premier ORM (autorisé par CLAUDE.md §3 spécifiquement à ce jalon)
- premier adapter externe non-LLM (transcription)
- premier auth à rôles (gm vs player)
- premier service à plus de 3 fichiers
- premier usage **pratique** de Spec Kit (vs. introduction documentaire au Jalon 4)

---

## 2. Spec Kit en pratique

Le flux exécuté :

```
/speckit.specify       → spec.md      (US 1-5, FR-001..017, scénarios d'acceptation)
/speckit.clarify       → 5 questions  (auth players, mapping manuel, format export,
                                       purge audio, posture transcription)
/speckit.plan          → plan.md      (stack, structure, risques)
                       → research.md, data-model.md, contracts/*.md, quickstart.md
/speckit.tasks         → tasks.md     (78 tâches groupées par US, ordonnées)
/speckit.implement     → exécution    (sub-lots 5a, 5b, sub-lot 6, sub-lot 7, sub-lot 8)
```

**Ce qui a marché** : le tasks.md ordonné par US (P1 MVP en premier) a permis 8 commits incrémentaux thématiques, chacun validable de bout en bout. Les questions de design en cours d'impl ont presque toujours leur réponse dans `data-model.md` ou `contracts/rest-api.md` (exemple : "le `pov:*` doit-il être invalidé sur changement de mapping ?" → oui, `data-model.md §6`).

**Ce qui a moins marché** : le tasks.md prescrit T064/T065 sur `live/router.py` "pas en parallèle" parce que même fichier, mais le découpage forcé fait tomber l'optimisation [P] à plat. Acceptable — la séquentialisation a un coût négligeable.

---

## 3. Architecture du service en 3 couches strictes

Voir [`docs/services/jdr.md` §2](./services/jdr.md) pour la structure de répertoire.

Le pattern clé : **layered exceptions**. Aucune couche ne connaît les exceptions des couches voisines en aval :

```
repositories.py   →  raise DuplicatePjNameError       (infra : mappe IntegrityError SQL)
       ↓
logic.py          →  catch DuplicatePjNameError → raise DuplicatePjError       (métier)
       ↓
router.py         →  catch DuplicatePjError → raise DuplicatePjConflictError   (HTTP 409)
```

Verbose mais immédiatement lisible à 6 mois : si un `IntegrityError` remonte jusqu'au router, c'est un bug.

---

## 4. Cinq décisions structurantes (ADR 0006)

| # | Décision | Pourquoi pas l'alternative |
|---|---|---|
| 1 | SQLAlchemy 2.x async + Alembic + aiosqlite | Standard mature, type-safe v2.x. Pas peewee (moins async-friendly), pas SQLModel (refusé par CLAUDE.md §3 — couche en plus). |
| 2 | `TranscriptionAdapter` agnostique, **une seule classe** paramétrée `OpenAICompatibleTranscriptionAdapter` | Couvre cloud (OpenAI/Groq) ET local (faster-whisper + pyannote sur LAN) via `base_url`. Pas deux classes séparées (DRY, plus de code à maintenir). |
| 3 | Auth roles **DB-backed** + bootstrap depuis env var | Continuité ergonomique du Jalon 2 (l'env var continue de marcher). Nouvelle row par joueur sans toucher au fichier `.env`. |
| 4 | Mode live = **stub publié** (501 + WS 1011) | YAGNI : publier le contrat sans payer l'impl. Le bot Discord viendra Jalon 6+. |
| 5 | Structure interne 3 couches (`router/logic/repositories`) + sub-routers (`batch/`, `live/`) | Lisible, testable couche par couche. Pas de "service objects" à la DDD (over-engineering pour ce scale). |

---

## 5. Pièges rencontrés

### 5.1. `{pj_id}.md` ne marche pas comme path param FastAPI

Naïvement on écrirait :
```python
@router.get("/povs/{pj_id}.md")
async def get_pov_md(pj_id: UUID, ...): ...
```
Starlette match `{pj_id}` avec la regex `[^/]+`, donc avale `<uuid>.md` en entier. Conversion `UUID` échoue → **422**. Pire : la route `GET /povs/{pj_id}` (JSON) sans `.md` est elle aussi affectée si déclarée après, car Starlette ne fallback pas.

**Solution adoptée** : un seul handler `GET /povs/{pj_id_str}` qui dispatche selon `pj_id_str.endswith(".md")` puis parse l'UUID à la main. Trade-off documenté dans le code.

### 5.2. Cycle de FK `api_keys.pj_id ↔ pjs.owner_gm_key_id`

Un MJ a une `ApiKey` qui pointe optionnellement vers une `Pj` (chaque joueur a `pj_id=<son_pj>`), et chaque `Pj` pointe vers la `ApiKey` du MJ propriétaire. Cycle → Alembic ne peut pas créer les deux tables dans le bon ordre.

**Fix** : `use_alter=True` sur la FK côté `api_keys.pj_id`. SQLAlchemy émet alors un `ALTER TABLE … ADD CONSTRAINT` après la création des deux tables. Voir [`app/services/jdr/db/models.py`](../app/services/jdr/db/models.py) ligne ~120.

### 5.3. Whisper "repetition loop" sur sessions longues

Sur un M4A de 2h, Whisper peut se bloquer à répéter la même phrase sur plusieurs minutes (hallucination connue, https://github.com/openai/whisper/discussions/2608). 

**Mitigation** : audio chunking client-side (ffmpeg, 30s par défaut) AVANT envoi à l'API. Une boucle stuck ne peut contaminer qu'un chunk au lieu de toute la transcription. Réalignement des timestamps par offset à la concaténation des résultats. Voir [`app/services/jdr/audio.py`](../app/services/jdr/audio.py) et `TRANSCRIPTION_CHUNK_DURATION_SECONDS` dans `.env.example`.

### 5.4. Diarisation absente avec le provider cloud

OpenAI Whisper API ne sépare pas les locuteurs → tous les segments arrivent avec `speaker_label="unknown"` → résumés POV pauvres (un seul "speaker_unknown" mappé à un seul PJ).

**Compromis assumé** : on **publie** quand même la posture hybride avec le provider local prêt à brancher (`TRANSCRIPTION_PROVIDER=local`, `TRANSCRIPTION_BASE_URL=http://gpu-host.lan:8001/v1`). Le wrapper côté GPU host (faster-whisper + pyannote) est documenté dans [`docs/services/jdr.md §5`](./services/jdr.md#5-hôte-gpu-lan-transcription-locale) mais hors scope `ai-kaeyris` (repo séparé). Le code métier n'a aucun coupling vendor.

---

## 6. Limites acceptées au Jalon 5

- **Single-shot summarisation** : pour 2h+ de session, le prompt user peut dépasser ~30-45k tokens. Stratégie map-reduce repoussée à un sous-lot dédié quand une session réelle montrera la limite.
- **Validation E2E avec une vraie clé DeepInfra non automatisée** : la suite pytest tourne sans appel LLM réel. La validation `quickstart.md §5-6` doit être exécutée manuellement avant la clôture formelle du jalon (T076).
- **SQLite uniquement en dev** : PostgreSQL + asyncpg verrouillés Jalon 8 (déploiement Pi).
- **Mode live = stub** : aucun chunk audio streamé. Impl Jalon 6+ avec bot Discord.

---

## 7. Ce que ce jalon prépare

- **Jalon 6 — Observability** : la table `jdr_jobs` (projection RQ) est déjà en place, alimente les futures métriques Prometheus (jobs queued / running / failed / latence). Logs structlog présents partout.
- **Jalon 6+ — Discord live** : surface `/live/*` publiée, schéma futur documenté en commentaires, ne reste qu'à brancher le routeur WS et l'orchestration `audio.chunk → faster-whisper streaming`.
- **Jalon 8 — Pi deployment** : SQLite → PostgreSQL via un seul changement `DATABASE_URL`. Migrations Alembic identiques.
- **Jalon 9 (opt) — Inférence locale** : déjà testable en pratique en pointant `TRANSCRIPTION_BASE_URL` vers le futur hôte GPU LAN. Pas de code à réécrire.
