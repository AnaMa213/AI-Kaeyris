# Phase 0 — Research : kaeyris-jdr (Jalon 5)

**Spec** : [`spec.md`](./spec.md)
**Plan** : [`plan.md`](./plan.md)
**Created** : 2026-05-04

> But : résoudre toutes les inconnues techniques avant la phase de design (Phase 1). Chaque section suit le format **Decision / Rationale / Alternatives** (Spec Kit convention).

---

## R1 — Provider de transcription cloud

**Decision** : `OpenAITranscriptionAdapter` ciblant l'API `audio/transcriptions` d'OpenAI ou de tout fournisseur compatible (Groq, DeepInfra, Together) via `base_url`. Modèle par défaut : Whisper-large-v3 ou équivalent fourni par DeepInfra (cohérence avec le LLM cloud du Jalon 4, ADR 0005).

**Rationale** :
- Le SDK `openai>=1.50` est déjà installé (ADR 0005) et expose `client.audio.transcriptions.create(file=…, model=…, response_format="verbose_json")` qui rend timestamps et segments — utile pour la diarisation. Source : https://platform.openai.com/docs/api-reference/audio/createTranscription
- Groq propose Whisper-large-v3 OpenAI-compatible avec une latence très basse. Source : https://console.groq.com/docs/speech-text
- DeepInfra propose Whisper aussi, ce qui permet d'avoir un seul `LLM_API_KEY` mutualisé. Source : https://deepinfra.com/openai/whisper-large-v3
- **Limitation** : OpenAI Whisper API ne fait **pas** de diarisation native. La diarisation devra être faite localement après transcription (cf. R3) OU sur le provider local (cf. R2) qui peut intégrer pyannote.

**Alternatives écartées** :
- **Deepgram** (https://deepgram.com) : excellent en transcription + diarisation native, mais SDK et auth dédiés, pas OpenAI-compatible → casse l'unicité du SDK.
- **AssemblyAI** (https://www.assemblyai.com) : idem Deepgram. À reconsidérer si la qualité de diarisation est insuffisante avec la chaîne Whisper+pyannote.
- **Anthropic / Gemini** : pas d'API audio-to-text à ce jour.

**Conséquences** :
- Le contrat `TranscriptionAdapter.transcribe(audio_bytes, …)` renvoie texte + segments avec timestamps, **mais pas** de labels de locuteur. La diarisation est une étape distincte.
- Le `TranscriptionAdapter` cloud paramètre `provider`, `model`, `base_url`, `api_key`, `language`, sur le même modèle que `OpenAICompatibleLLMAdapter`.

---

## R2 — Provider de transcription local (hôte GPU LAN)

**Decision** : `LocalWhisperTranscriptionAdapter` qui parle à un service HTTP exposé sur l'hôte GPU RTX 4090 du LAN. Backend recommandé : **`faster-whisper`** (CTranslate2) packagé derrière un mince wrapper FastAPI maison sur l'hôte GPU, exposant un endpoint OpenAI-compatible `/v1/audio/transcriptions`. La diarisation est faite côté hôte GPU avec **`pyannote.audio`** (modèle `pyannote/speaker-diarization-3.1`).

**Rationale** :
- `faster-whisper` est 4× plus rapide que `openai-whisper` original avec une qualité égale, supporte `large-v3`, tient bien en VRAM 24 Go (RTX 4090). Source : https://github.com/SYSTRAN/faster-whisper
- `pyannote.audio` est le standard de fait pour la diarisation, fournit des résultats compatibles avec un alignement Whisper (segments timestampés). Source : https://github.com/pyannote/pyannote-audio
- L'option `WhisperX` (https://github.com/m-bain/whisperX) combine Whisper + alignement forcé + diarisation pyannote en une seule chaîne et produit du `verbose_json` directement annoté `speaker:`. À évaluer concrètement au moment de l'implémentation.
- Exposer un endpoint OpenAI-compatible sur l'hôte GPU permet de réutiliser le `OpenAITranscriptionAdapter` avec un simple `base_url=http://gpu-host:8001/v1` — **pas besoin d'écrire un adaptateur séparé** au sens code, juste une configuration.
- Le Pi 5 reste orchestrateur (FR-022) : il pousse le M4A vers l'hôte GPU sur le LAN.

**Alternatives écartées** :
- **Whisper sur Pi 5 directement** (Whisper-tiny/base) : qualité insuffisante en français à 4-5 locuteurs, et pyannote ne tient pas en RAM Pi 5. Cohérent avec le memory pinning (Jalon 9 du roadmap a été invalidé pour la transcription, voir mémoire `infrastructure_topology.md`).
- **Ollama-style** : pas de chemin natif pour Whisper côté Ollama à ce jour.
- **gRPC custom** Pi↔GPU-host : surcomplique pour rien, HTTP+JSON suffit à l'échelle 1 session/semaine.

**Conséquences** :
- Le `TranscriptionAdapter` est en réalité **une seule implémentation** OpenAI-compatible, paramétrée par `base_url`. Bascule cloud↔local = changement d'env var (cf. SC-009).
- Une étape "stand up the GPU host wrapper" est nécessaire — documentée dans `quickstart.md`. Hors scope du repo `ai-kaeyris` lui-même au Jalon 5 ; un repo annexe ou un README de démarrage suffit.
- Si la diarisation pyannote nécessite un agrément Hugging Face (acceptation des CLA pour `pyannote/speaker-diarization-3.1`), à documenter.

---

## R3 — Stratégie de chunking pour les audios 2-3h

**Decision** : pas de chunking explicite côté client/Pi. Le M4A entier (≤ ~200 Mo) est uploadé une seule fois et transmis au worker. Le worker passe le fichier entier au backend de transcription, qui gère lui-même son découpage interne.

**Rationale** :
- Whisper-large via faster-whisper traite un fichier 3h directement sur RTX 4090 (VRAM ≈ 6-10 Go en mode `compute_type=float16`).
- L'API OpenAI accepte jusqu'à 25 Mo / fichier — un M4A 3h dépasse ce seuil. **Limite cloud à gérer** : si le provider cloud est sélectionné, le worker DOIT découper le fichier en segments ≤ 24 Mo (par minute audio) et concaténer les transcriptions. Reporté en TODO d'implémentation. Source : https://platform.openai.com/docs/guides/speech-to-text
- Garder l'upload monolithique au niveau de l'API préserve la simplicité contrat (un seul `POST` multipart).

**Alternatives écartées** :
- **Upload chunké côté client** (TUS, multipart upload S3-style) : sur-ingénierie pour 1 session/semaine en LAN.
- **Streaming HTTP de l'audio vers Whisper** : pas supporté par les SDK actuels.

---

## R4 — Stockage : ORM et persistance

**Decision** : **SQLAlchemy 2.x** ORM avec **SQLite** en dev et **PostgreSQL** en prod (Pi 5, Jalon 8). Migrations gérées par **Alembic**. Tables business dans `app/services/jdr/db/` ; les modèles SQLAlchemy ne fuient PAS hors du service (cf. constitution §2.4).

**Rationale** :
- Jalon 5 est explicitement le "premier vrai service" et CLAUDE.md §3 autorise l'introduction d'un ORM à partir de ce jalon ("Forbidden without discussion: adding ORM before Jalon 5"). Source : `CLAUDE.md`.
- SQLAlchemy 2.x a un mode `Mapped[]` typé, asyncio natif, large communauté. Source : https://docs.sqlalchemy.org/en/20/
- Alembic est le standard de fait pour les migrations SQLAlchemy. Source : https://alembic.sqlalchemy.org
- SQLite reste utilisable en prod si le déploiement Pi reste mono-utilisateur — mais Postgres est prévu (CLAUDE.md §3 "PostgreSQL (target)").
- Stockage de l'audio brut sur le **système de fichiers** (volume Docker), référencé par chemin dans la table `audio_sources`. Pas de blob SQL (gros fichiers = mauvaise idée). Purge = `os.unlink` + UPDATE de la row.

**Alternatives écartées** :
- **SQLModel** : surcouche pédagogique de SQLAlchemy, mais figée sur 0.0.x avec une roadmap incertaine. Préférable d'apprendre SQLAlchemy directement.
- **Tortoise ORM, Peewee, Pony** : moins maintenus / écosystème plus restreint.
- **Raw `sqlite3` sans ORM** : faisable, mais coût en boilerplate + pas de migration tooling. Reporté à un cas où l'ORM serait surdimensionné.
- **Pas de DB du tout, JSON files** : casserait l'intégrité référentielle (sessions ↔ PJs ↔ players ↔ artifacts) et serait incohérent avec CLAUDE.md §3.

**Conséquences** :
- Nouvelle dépendance `sqlalchemy>=2.0` + `alembic` + `aiosqlite` (driver async SQLite) + `asyncpg` en option pour Postgres.
- Nouveau dossier `migrations/` à la racine (alembic env). À cadrer dans le plan.
- Décision à arbitrer dans Tasks : faut-il migrer la table `api_keys` (aujourd'hui dans l'env var `API_KEYS` parsée par `app/core/auth.py`) vers la DB ? Voir R7.

---

## R5 — Endpoint de statut des jobs

**Decision** : Un endpoint **central** `GET /services/jdr/jobs/{job_id}` (pas un endpoint par type de job). Il interroge directement `rq.job.Job.fetch(job_id, connection=redis)` et renvoie le statut RQ projeté dans un format stable propre au service. Le `job_id` qui en sort est le `id` interne RQ.

**Rationale** :
- ADR 0004 §6 a explicitement reporté la décision de "centralisé vs par service" au "premier vrai service async" — c'est maintenant.
- Centraliser dans `services/jdr/router.py` (et **pas** dans `app/core/`) respecte la règle de séparation : c'est ce service qui produit ces jobs, c'est lui qui en expose le statut. Si un autre service (futur) introduit ses propres jobs, il aura son propre endpoint statut.
- Le format projeté découple le contrat REST de la lib RQ : si demain on change pour Celery, le contrat ne bouge pas.

**Format projeté** (cf. `contracts/`) :

```json
{
  "job_id": "string",
  "kind": "transcription | narrative | elements | povs",
  "session_id": "string",
  "status": "queued | running | succeeded | failed",
  "failure_reason": "string | null",
  "queued_at": "iso8601",
  "started_at": "iso8601 | null",
  "ended_at": "iso8601 | null"
}
```

**Alternatives écartées** :
- **Endpoint global `/jobs/{id}` dans `app/core/`** : viole la séparation services/core (un endpoint métier dans `core/` n'a rien à y faire).
- **Webhooks de complétion** : surcomplique côté client (pas de moyen simple côté Pi de joindre un webhook depuis un client de bureau). Polling simple suffit à 1 session/semaine.
- **WebSocket "subscribe to job"** : sur-ingénierie pour 1 session/semaine.

---

## R6 — Format Markdown des artefacts narratifs

**Decision** : Markdown généré côté serveur, sans dépendance externe. Templates simples en f-strings ou `str.join`. Un endpoint dédié par artefact :
- `GET /services/jdr/sessions/{id}/transcription.md`
- `GET /services/jdr/sessions/{id}/artifacts/narrative.md`
- `GET /services/jdr/sessions/{id}/artifacts/elements.md`
- `GET /services/jdr/sessions/{id}/artifacts/povs/{pj_id}.md`

Content-type `text/markdown; charset=utf-8`.

**Rationale** :
- FR-009a contraint le service à exposer l'export Markdown sans dégrader l'information (cf. spec Q3).
- Aucune dépendance lourde (pas de `markdownify`, pas de `pandoc`) — un Markdown simple se génère trivialement.
- Endpoints dédiés `.md` plutôt que query param `?format=md` : plus REST-friendly et plus simple à documenter dans OpenAPI (deux response models distincts).

**Alternatives écartées** :
- **Query param `?format=md`** : ajoute du conditional dans le handler, OpenAPI peine à le décrire proprement.
- **Génération Markdown côté client** : casse FR-009a (le service doit fournir).

**Format choisi** (résumé indicatif, à raffiner en Tasks) :

```markdown
# Session — {{title}} ({{recorded_at}})

## Résumé narratif
{{texte_narratif}}

---
Généré par kaeyris-jdr le {{generated_at}}.
```

---

## R7 — Auth roles `gm` / `player` et stockage des clés

**Decision** : Faire évoluer l'auth pour supporter les rôles. Les clés API sont **migrées vers la DB** (table `api_keys`), gérées par `app/core/auth.py` (qui lit désormais la DB au lieu de `settings.API_KEYS`). L'env var `API_KEYS` est conservée comme **bootstrap** — au premier démarrage, les clés présentes dans l'env var sont importées en DB avec rôle `gm` par défaut.

Les endpoints d'enrôlement de joueur (`POST /services/jdr/players`) génèrent une nouvelle clé en clair une seule fois (réponse), stockent l'Argon2 hash en DB avec rôle `player` et `pj_id` lié.

**Rationale** :
- FR-014a impose un lien `player → PJ` côté serveur, ce que ne permet pas le format env var actuel `name1:hash1;name2:hash2` (pas de role, pas de FK).
- Garder l'env var pour bootstrap permet de ne pas casser l'expérience dev existante (`API_KEYS=...` continue de fonctionner pour la première clé `gm`).
- Les rôles vivent dans une enum SQL (`gm`, `player`) plutôt qu'un champ libre.
- Pattern emprunté à FastAPI `dependencies=[Depends(require_role("gm"))]` qui s'enchaîne après `require_api_key`.

**Alternatives écartées** :
- **Garder l'env var et y ajouter le rôle/PJ** : déjà compliqué à parser ; explose vite avec 4-5 joueurs.
- **JWT** : surcomplique pour rien à l'échelle de cette plateforme.

**Conséquences** :
- `app/core/auth.py` doit migrer en lecture DB → impacte légèrement le test pyramid (un mock de session DB devient nécessaire pour les tests d'auth).
- Une dépendance FastAPI `require_role("gm" | "player")` à factoriser dans `app/core/auth.py` (toujours dans `core/` car concern transverse).

---

## R8 — Contrat du mode live (stub Jalon 5)

**Decision** : exposer dans l'OpenAPI :

- `POST /services/jdr/live/sessions` — initie une session live ; renvoie 501 + `WWW-Authenticate` + Problem Details `type=…/live-not-implemented`. Body de doc : `{title: str, expected_speakers: int}`.
- `WS /services/jdr/live/stream` — endpoint WebSocket documenté ; à la connexion réelle, ferme immédiatement avec code de fermeture WS `1011 Internal Error` et raison "stub — not yet implemented at Jalon 5".

Le **schéma WS** (events `audio.chunk`, `session.end`, `error`) est **documenté en commentaire** dans le fichier `app/services/jdr/live/router.py` mais sans implémentation.

**Rationale** :
- FR-015/FR-016 exigent que le contrat soit consultable (OpenAPI) sans simuler de comportement partiel (FR-016).
- 501 + Problem Details (RFC 9457) est cohérent avec la stratégie d'erreur du projet (ADR 0002).

**Alternatives écartées** :
- **Endpoint absent du tout** : casse FR-015 ("documentation").
- **404** : trompeur (suggère "n'existe pas" alors qu'il "existe mais pas implémenté").

---

## R9 — Versioning et idempotence des artefacts

**Decision** : Un seul artefact actif par `(session, kind, pj_id?)` à la fois. Une nouvelle production écrase l'ancienne mais conserve un horodatage `generated_at` et `model_used`. Pas d'historique multi-version au Jalon 5.

**Rationale** :
- YAGNI : 1 session/semaine, le besoin de comparer "le résumé narratif d'hier vs celui d'aujourd'hui" n'est pas exprimé.
- L'écrasement préserve la simplicité du modèle (`UPSERT` SQL).

**Alternatives écartées** :
- **Versionnement complet** (table `artifact_versions`) : sur-ingénierie pour ce volume.

---

## R10 — Périmètre fonctionnel laissé à `tasks.md`

Les points suivants sont **identifiés** mais leur implémentation détaillée est laissée à `/speckit-tasks` :

- Stratégie de timeout par job (transcription = 60 min ; LLM = 5 min). Cf. FR-018.
- Stratégie de retry RQ pour la transcription (déjà encadrée par ADR 0004).
- Métriques observabilité (nombre de sessions traitées, durée moyenne, taux d'échec). Reporté Jalon 6 (cf. CLAUDE.md §5).
- Tests d'intégration end-to-end nécessitant un fichier audio de démonstration (sous `tests/fixtures/`).
- Documentation utilisateur du démarrage de l'hôte GPU LAN (script de wrapper `faster-whisper` + pyannote).
