# Service `kaeyris-jdr`

Assistant de session de jeu de rôle — premier service métier de la plateforme (Jalon 5).
Réfs : [`specs/001-kaeyris-jdr/spec.md`](../../specs/001-kaeyris-jdr/spec.md), [`docs/adr/0006-jdr-service.md`](../adr/0006-jdr-service.md).

---

## 1. Ce que fait le service

Pipeline batch en 4 étapes, chacune asynchrone via RQ :

```
[M4A upload] -> transcription diarisée -> 3 familles d'artefacts -> /me/* (lecture joueur)
```

1. Le MJ crée une `Session`, uploade un M4A.
2. Un worker RQ transcrit l'audio (Whisper cloud ou local), purge le fichier source, écrit la `Transcription` (segments + speaker labels).
3. Le MJ déclare ses PJ, mappe `speaker_X → PJ`, déclenche à la demande la génération de 3 artefacts via le `LLMAdapter` (DeepInfra par défaut) :
   - `narrative` — récit chronologique global.
   - `elements` — fiche structurée `{npcs, locations, items, clues}`.
   - `pov:<pj_id>` — un résumé par PJ mappé, scoppé à ce qu'il pouvait percevoir.
4. Chaque joueur enrôlé (`role='player'`, lié à un `pj_id`) consulte `narrative` et **son** `pov` via `/me/*` — jamais ceux des autres (FR-014).

Le mode live (WebSocket Discord) est un **stub publié sans implémentation** (FR-015/016) — voir §5.

## 2. Architecture interne

```
app/services/jdr/
├── router.py         # routes /services/jdr/*, AppError pour chaque cas HTTP
├── logic.py          # orchestration métier (pas de SQL direct, pas d'imports vendor)
├── schemas.py        # Pydantic v2 — projections in/out de chaque entité
├── prompts.py        # NARRATIVE_/ELEMENTS_/POV_SYSTEM_PROMPT (centralisés, CLAUDE.md §2.4)
├── markdown.py       # rendu MD des artefacts (transcription, narrative, elements, pov)
├── audio.py          # chunking ffmpeg pour cap blast-radius hallucinations Whisper
├── batch/router.py   # sub-router /sessions/{id}/audio + reset
├── live/router.py    # sub-router stub /live/sessions (501) + /live/stream (WS 1011)
└── db/
    ├── models.py     # 8 tables jdr_*
    └── repositories.py  # une classe par entité (ADR 0006 §5)

app/jobs/jdr.py       # _transcribe_session / _generate_narrative / _generate_elements / _generate_povs
```

**Convention 3 couches** (CLAUDE.md §2.4) :
- `router.py` parle HTTP. Convertit les exceptions logic en AppError.
- `logic.py` parle métier. Lève des exceptions de domaine (`DuplicatePjError`, `InvalidMappingError`, `InvalidPlayerError`…).
- `repositories.py` parle SQL. Ne lève qu'une exception infra (`DuplicatePjNameError` qui mappe sur l'`IntegrityError` de la contrainte unique).

**Layered exceptions** : aucune couche ne connaît les types d'exception des couches voisines en aval — exemple : le router ne `catch IntegrityError` jamais ; il `catch DuplicatePjError` venue de `logic`.

## 3. Conventions de prompts

Tous les `*_SYSTEM_PROMPT` sont en français, instruits pour **rester fidèles au transcript** (pas d'invention) et ignorer les labels techniques (`speaker_X`, `unknown`) dans la sortie.

| Prompt | Sortie attendue | Particularité |
|---|---|---|
| `NARRATIVE_SYSTEM_PROMPT` | Récit chronologique en prose, 3ème personne | Pas de conclusion bilan ; s'arrête au dernier événement exploitable |
| `ELEMENTS_SYSTEM_PROMPT` | **JSON strict** `{npcs, locations, items, clues}` | Listes vides plutôt qu'absentes (acceptance US2.3) ; parsing tolère bloc ```json``` ou `{…}` extrait |
| `POV_SYSTEM_PROMPT` | Récit centré sur un PJ donné | Limite l'omniscience : ne raconte que ce que ce PJ pouvait percevoir |

L'utilisateur prompt embarque la transcription formatée segment par segment, et optionnellement le `campaign_context` de la session comme bloc "CONTEXTE DE CAMPAGNE" séparé.

## 4. Instructions opérationnelles

```bash
# 1) Préparer la DB
alembic upgrade head

# 2) Lancer l'API + un worker RQ (deux processus)
uvicorn app.main:app --reload
rq worker default --url redis://localhost:6379/0

# OU tout-en-un via Docker Compose (api + worker + redis)
docker compose up --build
```

**Bootstrap d'une clé MJ** (au premier démarrage uniquement, voir ADR 0006 §3) :
```powershell
python scripts/generate_api_key.py owner   # imprime le token plaintext (à conserver) + le hash Argon2
# Coller dans .env :  API_KEYS='owner:$argon2id$...'
```
Au premier démarrage, l'app importe cette entrée dans `jdr_api_keys` avec `role='gm'`. Les démarrages suivants ignorent l'env var (la DB devient source de vérité).

**Authentification web** :
1. `GET /services/jdr/auth/setup/status` retourne `{"required": true}` tant que `core_users` est vide.
2. `POST /services/jdr/auth/setup` crée le premier compte administrateur (`system_role="admin"`) avec `username + password`, puis pose le cookie HTTP-only `session`.
3. Un administrateur connecté crée ensuite les autres comptes via `POST /services/jdr/users`. Les comptes exposent `system_role` (`admin` ou `user`) ; `profile` n'est plus un champ public.
4. `POST /services/jdr/auth/login` accepte `username + password` et pose un cookie `session` utilisable par les routes protégées.
5. `GET /services/jdr/auth/me` : le front relit le cookie `session` et reçoit l'identité publique (`id`, `username`, `system_role`) plus la campagne active (`gm` ou `pj`). Le premier setup crée aussi la campagne V1 par défaut et rattache le premier administrateur comme GM.

Les API keys historiques restent supportées pour les clients machine. Pour compatibilité avec les tables JDR existantes, chaque compte web reçoit aussi une clé JDR interne non exposée : les ownership FKs continuent donc de pointer vers `jdr_api_keys`. Le rôle API-key legacy `player` reste réservé aux tokens joueur `/me/*` ; les memberships web de campagne utilisent `gm|pj`.

**Reseed local/staging BD-7 après purge** :
1. Purger la base locale/staging, puis appliquer `alembic upgrade head`.
2. Appeler `POST /services/jdr/auth/setup` pour créer le premier administrateur.
3. Vérifier `GET /services/jdr/auth/me` : l'utilisateur doit avoir `system_role="admin"`, une campagne active, et `role="gm"`.
4. Ne jamais activer de credential universel ou silencieux en production ; le mot de passe est toujours choisi explicitement au setup.

**Contrat datetime JSON** :
- Tous les champs datetime publics (`recorded_at`, `created_at`, `updated_at`, `uploaded_at`, `generated_at`, etc.) sont sérialisés avec un fuseau explicite.
- Le suffixe UTC peut être `+00:00` ou `Z`; une valeur sans suffixe timezone est une régression de contrat.
- Les inputs datetime historiques restent acceptés : `Z`, offset numérique, ou valeur naïve interprétée comme UTC.

**Campagnes BD-6/BD-7** :
- `GET /services/jdr/campaigns` liste les campagnes dont l'utilisateur web connecté est membre, avec `role`, `session_count`, `last_session_at` et `created_at`.
- `POST /services/jdr/campaigns` crée une campagne pour tout utilisateur web authentifié et rattache automatiquement le créateur avec le rôle `gm`.
- `GET/PATCH/DELETE /services/jdr/campaigns/{campaign_id}` exigent l'appartenance à la campagne ; `PATCH` et `DELETE` exigent le rôle `gm`.
- La suppression est volontairement prudente : une campagne contenant au moins une session retourne `409` et n'est pas supprimée.
- `POST /services/jdr/sessions` exige maintenant `campaign_id`. `GET /services/jdr/sessions?campaign_id=...` filtre explicitement par campagne ; sans query param, la liste non filtrée reste disponible pour compatibilité.
- Les PJ sont scoppés par campagne depuis BD-7. `POST /services/jdr/pjs` accepte un `campaign_id` optionnel, retombe sur la campagne par défaut du GM web si absent, et accepte `user_id` optionnel pour lier le PJ à un compte. `GET /services/jdr/pjs?campaign_id=...` filtre une campagne après contrôle de membership ; sans filtre, il retourne les PJ des campagnes visibles par l'utilisateur. `PATCH /services/jdr/pjs/{pj_id}` renomme un PJ et met à jour son lien `user_id` ; un `user_id: null` explicite délie le PJ, tandis qu'un champ absent ne modifie pas le lien.

**Bascule transcription cloud → local** (sans modifier le code) :
```ini
TRANSCRIPTION_PROVIDER=local
TRANSCRIPTION_BASE_URL=http://gpu-host.lan:8001/v1
```
Redémarrer le worker. Aucun fichier de `app/services/jdr/` n'a besoin de changer (SC-009).

## 4bis. Mode `non_diarised` (sub-jalon 5.5)

Pipeline alternatif opt-in, posé via `transcription_mode: "non_diarised"` à la création de session (immuable ensuite). Conçu pour les cas où le provider de transcription ne diarise pas — typiquement le cloud Whisper par défaut.

### Pipeline forké

```
[POST /sessions transcription_mode=non_diarised]
  ↓
[POST /sessions/{id}/audio] -> transcrit puis chunké
  ↓
[jdr_chunks: rows (ordre, text, summary_text=NULL)]
  ↓
[POST /sessions/{id}/players] -> liste de pj_ids (équivalent /mapping sans speaker)
  ↓
[POST /artifacts/summary] -> map-reduce LLM :
    1) map: 1 LLM call par chunk -> chunks.summary_text peuplé
    2) reduce: 1 LLM call sur les résumés partiels -> Artifact(kind=summary)
    (cascade: NULL'ifie summary_text + DELETE narrative/elements/pov:*
     dans une transaction unique AVANT les LLM calls — FR-011)
  ↓
[POST /artifacts/{narrative|elements|povs}] -> consomment chunks.summary_text
    (refusé 409 no-summary si summary pas généré au préalable — FR-010)
```

Le mode `diarised` (défaut) reste strictement inchangé Jalon 5 (`/mapping`, `/transcription`, `/artifacts/*` historiques).

### Endpoints additifs (mode non_diarised uniquement)

| Méthode | Path | Description |
|---|---|---|
| `GET` | `/sessions/{id}/chunks` | Liste des chunks ordonnés (`chunk_id`, `ordre`, `text`). `summary_text` non exposé (interne pipeline LLM). |
| `POST`/`GET` | `/sessions/{id}/players` | Déclaration des PJ présents (équivalent `/mapping` sans `speaker_label`). |
| `POST`/`GET`/`GET.md` | `/sessions/{id}/artifacts/summary` | Résumé global map-reduce. |

### Cross-mode isolation (raccourci)

| Endpoint | mode diarised | mode non_diarised |
|---|---|---|
| `GET /transcription[.md]` | 200 | **409 wrong-mode** → utiliser `/chunks` |
| `PUT/GET /mapping` | 200 | **409 wrong-mode** → utiliser `/players` |
| `GET /chunks` | **409 wrong-mode** → utiliser `/transcription` | 200 |
| `POST/GET /players` | **409 wrong-mode** → utiliser `/mapping` | 200 |
| `POST/GET /artifacts/summary[.md]` | **409 wrong-mode** (hors scope sub-jalon) | 200 |
| `POST /artifacts/{narrative,elements,povs}` | 200 (Jalon 5) | 200 si `summary` existe, sinon **409 no-summary** |

### Configuration

- `KAEYRIS_CHUNK_MAX_CHARS` (env var, default `30000`) : taille max d'un chunk de transcription. Affecte le découpage post-transcription en mode non_diarised. À affiner par benchmarks empiriques.
- Prompts système nouveaux : `SUMMARY_MAP_SYSTEM_PROMPT`, `SUMMARY_REDUCE_SYSTEM_PROMPT`. Les prompts existants `NARRATIVE_/ELEMENTS_/POV_SYSTEM_PROMPT` sont réutilisés tels quels (le user prompt est adapté côté job pour passer les résumés chunked au lieu des segments).

### Limites assumées

- **POV qualitativement limités** : sans speaker labels, le LLM doit "deviner" qui agit depuis le contexte narratif. À ré-évaluer post-Jalon 9 (diarisation locale).
- **`/me/*` joueur reste réservé aux sessions `diarised`** au sub-jalon courant. Un joueur dont le MJ a opté pour non_diarised verra `409 wrong-mode` (à reconsidérer si la première vraie session révèle un besoin).
- **Mode immuable** : un MJ qui s'est trompé doit recréer une nouvelle session.

Voir [ADR 0007](../adr/0007-non-diarised-mode.md) pour le détail des décisions et alternatives rejetées.

## 4ter. Suivi de progression des jobs (BD-10)

`GET /services/jdr/jobs/{job_id}` reste l'unique surface de polling du front
(pas de SSE/WebSocket en v1). La projection `JobOut` est enrichie de deux
champs **best-effort** lus depuis la métadonnée du job RQ
(`job.meta` / `save_meta()`, [doc RQ](https://python-rq.org/docs/jobs/)) :

| Champ | Type | Valeurs |
|---|---|---|
| `phase` | enum nullable | `reducing` (préparation/segmentation audio), `transcribing`, `done`, `failed`, ou `null` |
| `progress_percent` | entier nullable | `0..99` en cours, `100` succès terminal uniquement, ou `null` |

Règles de contrat :

- **`status` reste la source de vérité** du cycle de vie ; `phase` ne pilote
  jamais la complétion. `queued` n'est volontairement pas une `phase` —
  c'est déjà un `status`.
- **Best-effort** : métadonnée absente, expirée, malformée, non-entière ou
  hors domaine ⇒ les deux champs retombent à `null`, jamais un `500`. Un job
  fraîchement enfilé renvoie `phase=null` / `progress_percent=null` (aucune
  synthèse de `phase="queued"` ni `progress_percent=0`).
- **`100` réservé au succès** : la boucle de chunks plafonne à `99` ;
  `progress_percent=100` n'est émis qu'après persistance + transition d'état
  réussies, avec `phase="done"`.
- **Échec non destructif** : sur erreur, le worker émet `phase="failed"`
  *sans* percent, ce qui **préserve la dernière progression connue** au lieu
  de la remettre à zéro.

Découplage des couches : `app/jobs/jdr.py` écrit la métadonnée au niveau du
job RQ (`_ProgressReporter` + callback `(chunks_done, chunks_total)` passé à
`_transcribe_with_optional_chunking`) ; `router.py` ne fait que projeter et
valider la métadonnée via `_project_progress_meta`. Le contrat OpenAPI public
est régénéré dans [`docs/context/api/openapi.json`](../context/api/openapi.json).

Voir [`specs/010-job-progress-phase/`](../../specs/010-job-progress-phase/)
pour la spec, le plan et les décisions de recherche complètes.

## 5. Hôte GPU LAN (transcription locale)

Topologie cible (mémoire `infrastructure_topology.md`) :
- **Pi 5** : orchestrateur uniquement — héberge l'API, le worker RQ, Redis, la DB.
- **PC RTX 4090 sur le même LAN** : héberge le moteur de transcription lourd.

**API attendue côté hôte GPU**, format compatible OpenAI Whisper :

```
POST {TRANSCRIPTION_BASE_URL}/audio/transcriptions
Headers: Authorization: Bearer {TRANSCRIPTION_API_KEY}
Body (multipart):
  file=<audio.m4a>
  model=<TRANSCRIPTION_MODEL>            # informatif
  response_format=verbose_json
  language=fr                            # quand fourni
```

Réponse attendue (enrichie par rapport à l'API OpenAI officielle d'un champ `speaker` par segment) :

```json
{
  "language": "fr",
  "segments": [
    { "speaker": "speaker_1", "start": 0.0, "end": 3.2, "text": "..." },
    { "speaker": "speaker_2", "start": 3.2, "end": 5.1, "text": "..." }
  ]
}
```

**Stack recommandée côté GPU host** (hors scope `ai-kaeyris`) :
- [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) pour la transcription (CTranslate2, bien plus rapide qu'openai-whisper).
- [`pyannote.audio`](https://github.com/pyannote/pyannote-audio) pour la diarisation, fusion des speakers dans la réponse.
- Wrapper FastAPI minimal exposant l'endpoint ci-dessus.
- À écrire dans un repo séparé ; pas de dépendance au repo `ai-kaeyris`.

**Limite assumée du provider cloud** (par défaut Jalon 5) : OpenAI Whisper API ne diarise pas → tous les segments arrivent avec `speaker_label="unknown"` → les résumés POV resteront pauvres tant que l'hôte GPU local n'est pas branché.

## 6. Repères opérationnels

| Question | Endroit |
|---|---|
| Modifier un prompt | `app/services/jdr/prompts.py` |
| Ajouter une nouvelle table | Modèle dans `db/models.py`, repository dans `db/repositories.py`, migration `alembic revision -m "..."` |
| Ajouter un nouvel artefact `kind=...` | `prompts.py` (system prompt) + `jobs/jdr.py` (_generate_xxx) + `schemas.py` + `markdown.py` (rendu) + `router.py` (POST/GET/MD) |
| Voir le contrat REST complet | [`specs/001-kaeyris-jdr/contracts/rest-api.md`](../../specs/001-kaeyris-jdr/contracts/rest-api.md) |
| Vérifier les FRs côté tests | `tests/services/jdr/test_player_access.py` (FR-014), `test_mapping.py` (FR-010/011), `test_audio_*.py` (FR-017, purge) |
