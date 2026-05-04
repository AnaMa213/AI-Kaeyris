# ADR 0006 — Architecture du service `kaeyris-jdr` (Jalon 5)

- **Statut** : accepté
- **Date** : 2026-05-04
- **Décideur** : owner du projet (Kenan)
- **En lien avec** : ADR 0001 (architecture monolithe modulaire), ADR 0002 (services 3-fichiers), ADR 0003 (auth Bearer/Argon2), ADR 0004 (jobs RQ + rate limiting), ADR 0005 (LLMAdapter), CLAUDE.md §3 (stack lockée — ORM autorisé Jalon 5), CLAUDE.md §5 (roadmap)
- **Dérivé de** : `specs/001-kaeyris-jdr/` (spec, plan, research, data-model, contracts, tasks)

## Contexte

Le Jalon 5 livre le **premier vrai service métier** de la plateforme : `kaeyris-jdr`, un assistant pour des sessions de jeu de rôle. Il ingère un audio M4A de session (2-3h, 4-5 locuteurs), produit en asynchrone une transcription diarisée, puis trois familles d'artefacts à la demande du MJ : un résumé narratif, une fiche structurée d'éléments (PNJ/lieux/items/indices), et un résumé "point de vue" par PJ. Les joueurs y accèdent en lecture seule, scoppés à leur PJ. Un futur bot Discord (Jalon 6+) consommera un mode "live" dont le contrat est figé dès maintenant **sans implémentation** (FR-015/016, hors scope ce jalon).

C'est aussi le jalon le plus **structurant** depuis le Jalon 0 : il introduit deux briques nouvelles à l'archi (ORM, adapter de transcription) et étend deux autres (auth, jobs). Cinq décisions doivent être actées :

1. **Stockage métier** : où et comment on persiste les sessions, transcriptions, artefacts ?
2. **Adapter de transcription** : comment on parle aux moteurs de speech-to-text, sans coupler le service à un fournisseur ?
3. **Auth roles** : comment on distingue MJ et joueurs avec un fail-safe sur l'isolation ?
4. **Mode live** : implémentation MVP ou stub documenté ?
5. **Architecture interne** du service : comment on organise `app/services/jdr/` pour qu'il reste lisible alors que le service est plus gros que les précédents ?

Spec Kit a produit le détail technique dans `specs/001-kaeyris-jdr/`. Cet ADR consolide les choix structurants pour qu'ils soient relisibles à 6 mois sans avoir à plonger dans 100 KB de spec.

## Décision

### 1. Persistance via SQLAlchemy 2.x async + Alembic + aiosqlite

CLAUDE.md §3 autorise explicitement l'introduction d'un ORM **à partir du Jalon 5** ("Forbidden without discussion: switching framework, adding ORM (SQLAlchemy etc.) **before Jalon 5**, …"). On l'active maintenant.

**Stack persistance** :

| Couche | Choix | Rationale |
|---|---|---|
| ORM | **SQLAlchemy 2.x** (`sqlalchemy>=2.0`) en mode **async** (`AsyncSession`) | Standard Python, écosystème mature, type safety via mappers v2.x, async natif compatible FastAPI |
| Migrations | **Alembic** | Standard de fait avec SQLAlchemy, géré côté repo dans `migrations/` |
| Driver dev | **aiosqlite** + SQLite | Zéro dépendance externe en local, compatible bind-mount Docker |
| Driver prod | **asyncpg** + PostgreSQL (Jalon 8) | Verrouillé par CLAUDE.md §3 ; on bascule via env var `DATABASE_URL` sans changer le code |
| Engine | Construit dans **`app/core/db.py`** | Concern transverse au sens CLAUDE.md §2.4 (plusieurs services en consommeront à terme) |
| Dépendance FastAPI | **`get_db_session()`** yields `AsyncSession` (transaction par requête) | Pattern canonique FastAPI, overridable en tests via `dependency_overrides` |

**Schéma initial** : 8 tables `jdr_*` (`api_keys`, `pjs`, `sessions`, `audio_sources`, `transcriptions`, `session_pj_mappings`, `artifacts`, `jobs`) — détail dans `specs/001-kaeyris-jdr/data-model.md`.

**Bootstrap** : Alembic démarre vide. Au premier démarrage de l'app, un hook `on_startup` lit `settings.API_KEYS` (legacy env var Jalon 2) et, si la table `jdr_api_keys` est vide, importe ces clés en DB avec `role='gm'`. Ainsi la migration env-var → DB est transparente pour l'utilisateur.

**Préfixe `jdr_*` sur les tables** : on prépare l'évolution vers d'autres services qui auront leurs propres préfixes (`meeting_*`, `notes_*`...) dans la même DB. Évite les collisions sans nécessiter un schéma SQL distinct.

### 2. `TranscriptionAdapter` agnostique avec **une seule implémentation paramétrée**

Cohérent avec ADR 0005 (`LLMAdapter`), on définit dans `app/adapters/transcription.py` :

```python
class TranscriptionAdapter(Protocol):
    async def transcribe(self, *, audio_path: str, language_hint: str | None = None) -> TranscriptionResult: ...
```

Une **seule classe concrète** `OpenAICompatibleTranscriptionAdapter` couvre les deux postures :

- **Cloud** : `base_url=https://api.openai.com/v1` (ou Groq/DeepInfra/Together compatible). Whisper API officielle. Limite 25 Mo → l'adapter découpe les fichiers plus gros et concatène les segments en réalignant les timestamps.
- **Local** : `base_url=http://gpu-host.lan:8001/v1`. L'hôte GPU expose un wrapper minimal autour de `faster-whisper` + `pyannote.audio` qui répond avec l'API OpenAI Whisper enrichie d'un champ `speaker` par segment (diarisation). Le wrapper lui-même est **hors scope du repo `ai-kaeyris`** — repo annexe ou README dans `docs/services/jdr.md`.

**Limite assumée du cloud** : OpenAI Whisper API ne diarise pas → tous les segments arrivent avec `speaker_label="unknown"` → les résumés POV resteront pauvres. Documenté dans le README du service. **Bascule explicite vers local** quand le owner veut activer la diarisation, en changeant 3 lignes de `.env`.

**Pi 5 = orchestrateur uniquement** (FR-022) — il n'exécute jamais le modèle Whisper. Soit cloud, soit hôte GPU LAN. Le Pi a 8 Go RAM et pas de GPU dédié, faire tourner Whisper large-v3 dessus prendrait 6h pour 1h d'audio.

**Mock pour tests** : `MockTranscriptionAdapter` retourne 3 segments déterministes en fonction de la longueur du fichier — pas d'appel réseau, ~50ms par test.

**Erreurs** : trois classes (`TranscriptionError` racine, `TransientTranscriptionError`, `PermanentTranscriptionError`) avec mapping HTTP identique à `LLMAdapter` (ADR 0005 §6). Remappage vers `TransientJobError`/`PermanentJobError` côté job pour que la retry policy de RQ s'applique (ADR 0004 §4).

### 3. Auth roles `gm` / `player` avec stockage DB

Extension de `app/core/auth.py` :

```python
class Role(StrEnum):
    GM = "gm"
    PLAYER = "player"

@dataclass(frozen=True, slots=True)
class AuthenticatedKey:
    name: str
    role: Role
    pj_id: str | None      # obligatoire si role='player', None si role='gm'
```

**Lookup** : `_verify_against_registry()` charge les clés depuis la table `jdr_api_keys` (au lieu de `settings.API_KEYS`). La fonction `get_registered_keys()` du Jalon 2 devient une **dépendance FastAPI** qui prend `AsyncSession` en argument.

**Nouvelle dépendance** : `require_role(role: Role)` chaîne après `require_api_key`. Lève `ForbiddenError` (403) si le rôle ne correspond pas. Raccourcis `require_gm`, `require_player` exposés.

**Énrolement de joueur** : `POST /services/jdr/players` (réservé MJ) génère un token aléatoire 32 octets, INSERT dans `jdr_api_keys` avec `role='player'` + `pj_id`, **renvoie le token plaintext une seule fois**. Le hash Argon2 reste en DB. Identique au pattern Jalon 2, scoppé au service JDR.

**Isolation stricte joueur (FR-014)** : un joueur ne peut accéder **qu'à** :
- `GET /services/jdr/me` (sa propre identité)
- `GET /services/jdr/me/sessions` (sessions où son PJ est mappé)
- `GET /services/jdr/me/sessions/{id}/narrative(.md)?` (résumé narratif partagé)
- `GET /services/jdr/me/sessions/{id}/pov(.md)?` (son propre POV uniquement)

Tout le reste (POST, DELETE, lecture des POV des autres, listing des PJ d'autres MJ) → 403. Tests dédiés (T056) pour verrouiller ce comportement.

### 4. Mode live = **stub documenté** (501 + WS fermé immédiatement)

Pourquoi stub :
- Bot Discord ciblé en futur lointain par le owner
- Diarisation streaming complexe (`pyannote.audio` n'est pas streaming-friendly nativement)
- Whisper streaming fonctionnel mais qualité < batch
- Vaut mieux poser le contrat REST/WS dès maintenant pour qu'un futur bot ait sa cible API stable

Implémentation Jalon 5 :
- `POST /services/jdr/live/sessions` lève `LiveNotImplementedError` (sous-classe `AppError`, status 501, type=`errors/live-not-implemented`). Schéma de requête `LiveSessionInit` documenté en Pydantic mais jamais traité — visible dans `/docs`.
- `WS /services/jdr/live/stream` accepte la connexion puis ferme immédiatement avec code 1011 et `reason="stub — not yet implemented at Jalon 5"`. Les futurs events (`audio.chunk`, `session.end`, `error`) sont documentés en commentaires Python visibles dans la description OpenAPI du WS.

Coût d'implémentation Jalon 5 : ~30 lignes. Coût d'omission : repousser la décision de contrat à l'arrachée quand le bot Discord arrivera.

### 5. Architecture interne de `app/services/jdr/`

Le pattern Jalon 1 (`router.py`/`schemas.py`/`logic.py`) ne suffit plus pour un service de cette taille. Structure adoptée :

```text
app/services/jdr/
├── __init__.py
├── router.py         # routes communes (/sessions, /pjs, /players, /jobs, /me)
├── schemas.py        # Pydantic v2 (cf. contracts/rest-api.md)
├── logic.py          # règles métier (validation, autorisation, mappings)
├── prompts.py        # prompts système narrative/elements/POV (centralisés ici)
├── markdown.py       # rendu MD des artefacts
├── batch/
│   └── router.py     # POST /sessions/{id}/audio (mode batch)
├── live/
│   └── router.py     # stub live (501 + WS fermé)
└── db/
    ├── models.py     # SQLAlchemy (api_keys, pjs, sessions, …)
    └── repositories.py  # encapsulation des requêtes DB par entité
```

**Justification** :
- **`prompts.py` séparé** : centralise les prompts système, les rend modifiables sans toucher la logique. Conforme à la discussion Jalon 4 ("prompt = logique métier, pas adapter").
- **`markdown.py` séparé** : rendu Markdown isolé, testable unitairement, ne pollue pas `logic.py`.
- **`batch/` et `live/` en sous-routers** : chaque mode a son cycle de vie indépendant (batch va évoluer rapidement, live restera stub plusieurs mois). Évite que `router.py` devienne un fichier de 800 lignes.
- **`db/repositories.py`** : encapsule les requêtes SQLAlchemy par entité. `logic.py` appelle des méthodes lisibles (`SessionRepository.list_for_gm(gm_key_id)`) au lieu de construire des `select(Session).where(...)` partout. Pattern Repository (Fowler, *Patterns of Enterprise Application Architecture*, 2002).
- **Pas de cross-imports avec d'autres services** (CLAUDE.md §2.4). `app/services/jdr/db/` est interne au service.

Cette structure reste **dans les frontières** du pattern défini par ADR 0002 — c'est une élaboration justifiée par la taille du service, pas une dérive vers du Clean Architecture façon Robert C. Martin.

## Alternatives écartées

### Pour le stockage

| Alternative | Raison du rejet |
|---|---|
| **Fichiers JSON** dans `data/` | OK pour quelques sessions, casse à plusieurs dizaines (concurrence d'écriture, recherche, intégrité référentielle inexistante). Non extensible. |
| **MongoDB** | Document-oriented, pas justifié par le data model (qui a des FKs claires : sessions ↔ pjs ↔ mappings ↔ artifacts). Ajout de complexité sans bénéfice. |
| **Redis comme store primaire** | Redis est déjà là (Jalon 3) mais c'est un cache/queue, pas une DB durable au sens transactionnel. Risque de perte au restart. |
| **SQLAlchemy sync** (`Session` au lieu d'`AsyncSession`) | Casse l'async des handlers FastAPI, force un pool de threads. Performance inférieure. |
| **Tortoise ORM / SQLModel / Edgy** | Alternatives async modernes, mais plus jeunes et moins documentées. SQLAlchemy 2.x est le standard de l'industrie Python. |
| **Postgres en dev (Compose)** | Ajoute un service Compose pour 1 dev solo. SQLite en dev / Postgres en prod via la même URL est bien plus léger. Bascule au Jalon 8. |
| **Pas de migrations (just create_all())** | Casse dès qu'on doit modifier le schéma en prod. Alembic est cheap à mettre en place et indispensable plus tard. |

### Pour la transcription

| Alternative | Raison du rejet |
|---|---|
| **Whisper en local sur Pi 5** | 6h pour transcrire 1h d'audio avec `whisper-tiny`. Inutilisable pour des sessions de 2-3h. |
| **Plusieurs sous-classes** (`OpenAITranscriptionAdapter`, `GroqTranscriptionAdapter`, `LocalTranscriptionAdapter`) | Tous parlent l'API OpenAI compatible. Une seule classe paramétrée par `base_url` suffit (cohérent avec choix LLMAdapter, ADR 0005). |
| **AssemblyAI / Rev.ai / Speechmatics natifs** | APIs propriétaires, casse la portabilité. Si on en a besoin, on créera un adapter dédié à ce moment-là. |
| **`pyannote.audio` + `whisper` directement dans le service** | Met du modèle ML lourd dans `app/services/jdr/`. Casse la séparation services/adapters. Force le déploiement avec GPU. |
| **`whisperx` ou `whispercpp`** (autres clients) | Possibles côté hôte GPU, mais c'est un détail d'implémentation interne au wrapper local — invisible côté adapter. |

### Pour l'auth

| Alternative | Raison du rejet |
|---|---|
| **Garder uniquement env var pour les clés** | Casse la rotation de joueurs (un MJ enrôle 5 joueurs sur 3 mois, on ne va pas redémarrer le conteneur 5 fois). |
| **JWT avec claims `role` et `pj_id`** | Inutile : pas de session multi-service à signer, pas de SSO. Coût (révocation, rotation de clé) > bénéfice. |
| **Auth par email/mot de passe utilisateur** | Hors scope mono-utilisateur. À reconsidérer si l'API devient publique. |
| **Permission scopes par clé** (ex : `read:sessions`, `write:players`) | YAGNI. 2 rôles suffisent ; on ajoutera des scopes si on a un service "dangereux" à isoler. |

### Pour le live

| Alternative | Raison du rejet |
|---|---|
| **MVP fonctionnel** (stream → texte, sans diarisation ni résumés temps réel) | Coût de dev sérieux pour une fonctionnalité dont la priorité est lointaine. Le bot Discord n'est pas dans la roadmap proche. |
| **Implémentation complète** | Diarisation streaming = sujet de R&D, pas de feature de jalon. Whisper streaming dégrade la qualité. |
| **Pas d'endpoint du tout** | Force à reconsidérer le contrat plus tard sans pression. Mieux de poser un stub qui montre le contrat figé. |

### Pour la structure interne

| Alternative | Raison du rejet |
|---|---|
| **Pattern Jalon 1 strict** (3 fichiers seulement) | `router.py` deviendrait > 800 lignes pour un service multi-routes. Illisible. |
| **Clean Architecture complet** (use_case, entities, controller, repository, presenter) | Sur-ingénierie pour un dev solo. CLAUDE.md §9 : "Pragmatique : DDD strict only if needed". |
| **Vertical slice par feature** (un dossier par US) | Fragmente la base de données et les schémas. Repository pattern + sub-routers couvre le besoin. |
| **`prompts.py` mixé dans `logic.py`** | Les prompts deviennent invisibles dans 600 lignes. Séparé, ils sont editables sans grep. |
| **Repositories dans `logic.py` directement** | Couple le métier au SQL. Cas typique où l'indirection paie : on testera `logic.py` avec des mocks de repositories. |

## Conséquences

**Positives**

- Plateforme prête à scaler : SQLAlchemy + Alembic = changement de modèle sans réécriture, prod Postgres au Jalon 8 = un changement de DSN
- Code métier vendor-neutral préservé : aucune référence à OpenAI/DeepInfra/faster-whisper dans `app/services/jdr/`
- Migration auth env→DB transparente pour l'utilisateur grâce au bootstrap au démarrage
- Bascule cloud → local en 3 lignes de `.env` + redémarrage worker, validable comme critère de succès SC-009
- Mode live posé comme contrat, prêt pour le bot Discord futur sans dette technique
- Structure interne lisible : un nouveau contributeur trouve les prompts dans `prompts.py`, le rendu MD dans `markdown.py`, etc.
- Repository pattern facilite le mock des accès DB en tests unitaires de la logique

**Négatives / acceptées**

- Premier ORM dans le projet → courbe d'apprentissage SQLAlchemy 2.x async + Alembic + asyncio non triviale (admise au Jalon 5 par CLAUDE.md §3)
- 8 tables ajoutées d'un coup → migration `0001_initial` est dense ; risque d'oubli de constraint ou d'index. Mitigation : tests d'intégration sur fixtures SQLite + revue avant commit.
- Le hôte GPU LAN pour la diarisation est **hors-repo** → procédure de stand-up à documenter, sans ça les POV resteront pauvres (avec le provider cloud par défaut)
- Coût LLM pour 4-5 POV par session peut atteindre des dizaines de milliers de tokens → à monitorer dès la première vraie session, à reporter au Jalon 6
- `MockTranscriptionAdapter` peut diverger du comportement réel (un test passe en mock, casse en prod) → mitigation : test d'intégration manuel après chaque changement structurant de l'adapter
- Le stub live introduit un endpoint qui retourne 501 — visible dans la doc, peut surprendre. Documenté comme tel.

**Conditions de re-évaluation** (cet ADR sera "superseded" si)

- On a besoin de **plusieurs services métier qui partagent des entités** → introduire un layer "domain" partagé dans `app/core/`
- Le service grossit au point que `router.py` ou `logic.py` dépasse 800 lignes → splitter par sous-feature (`sessions/`, `artifacts/`, `players/`)
- Le mode live devient une priorité (bot Discord à livrer à date) → ADR dédié sur l'archi streaming
- Postgres en prod (Jalon 8) révèle des limites du modèle SQLAlchemy actuel (perfs, types JSON, full-text search...) → ADR de migration ciblée
- On veut **isoler les services entre eux côté DB** (schémas SQL distincts ou DBs distinctes) → revue du choix "1 DB partagée avec préfixes"

## Références

- SQLAlchemy 2.x documentation — https://docs.sqlalchemy.org/en/20/
- SQLAlchemy 2.x Async ORM — https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- Alembic documentation — https://alembic.sqlalchemy.org/
- Martin Fowler, *Patterns of Enterprise Application Architecture* (2002) — pattern Repository
- Robert C. Martin, *Clean Architecture* (2017) — référence pour la non-adoption (sur-ingénierie pour ce contexte)
- OpenAI Audio API (Whisper) — https://platform.openai.com/docs/guides/speech-to-text
- `faster-whisper` (CTranslate2) — https://github.com/SYSTRAN/faster-whisper
- `pyannote.audio` (diarisation) — https://github.com/pyannote/pyannote-audio
- `aiosqlite` — https://github.com/omnilib/aiosqlite
- Spec Kit artefacts du Jalon 5 — `specs/001-kaeyris-jdr/{spec,plan,research,data-model,contracts,quickstart,tasks}.md`
- ADR 0001 (architecture monolithe modulaire — base de cet ADR)
- ADR 0002 (services 3-fichiers — pattern étendu ici)
- ADR 0003 (auth Bearer/Argon2 — étendu avec rôles)
- ADR 0004 (jobs RQ + retry policy — réutilisé tel quel pour les jobs JDR)
- ADR 0005 (LLMAdapter — pattern reproduit pour TranscriptionAdapter)
