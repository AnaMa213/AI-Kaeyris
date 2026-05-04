---
description: "Tasks for kaeyris-jdr — Assistant de session de jeu de rôle (Jalon 5)"
---

# Tasks: kaeyris-jdr — Assistant de session de jeu de rôle

**Input**: Design documents from `D:\Projets\dev\AI-Kaeyris\specs\001-kaeyris-jdr\`
**Prerequisites** : `plan.md` ✅, `spec.md` ✅, `research.md` ✅, `data-model.md` ✅, `contracts/` ✅, `quickstart.md` ✅

**Tests** : INCLUS PAR DÉFAUT pour ce projet — la constitution (`CLAUDE.md` §2.5) impose un test au minimum par endpoint public et la pyramide de tests classique. Les tests ne sont pas optionnels ici.

**Organization** : tasks groupées par user story pour permettre une livraison MVP-first puis incrémentale.

## Format: `[ID] [P?] [Story] Description`

- **[P]** : peut tourner en parallèle (fichier différent, pas de dépendance sur une tâche en cours).
- **[Story]** : à quelle user story la tâche appartient (US1, US2, US3, US4, US5). Setup/Foundational/Polish n'ont pas de label de story.
- Chaque tâche cite le **chemin de fichier exact** dans le repo.

## Path Conventions

Convention monolithe modulaire AI-Kaeyris (cf. `plan.md` §Project Structure et ADR 0001) :

- Code : `app/` (sous-dossiers `core/`, `adapters/`, `jobs/`, `services/jdr/`).
- Tests : `tests/` miroir de `app/`.
- Migrations DB : `migrations/` à la racine (Alembic).
- Documentation : `docs/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose** : pré-requis techniques au niveau du repo (deps, config, scaffolding DB) avant toute écriture de code métier.

- [ ] T001 Ajouter les dépendances Jalon 5 (`sqlalchemy>=2.0`, `alembic`, `aiosqlite`) dans `D:\Projets\dev\AI-Kaeyris\pyproject.toml` (section `[project] dependencies`) et dans le verrou de versions si présent ; documenter dans le commit le pourquoi (ORM autorisé Jalon 5 par CLAUDE.md §3).
- [ ] T002 [P] Créer `D:\Projets\dev\AI-Kaeyris\app\core\db.py` exposant un engine async SQLAlchemy 2.x basé sur `settings.DATABASE_URL` + une dépendance FastAPI `get_db_session()` qui yield une `AsyncSession` (transaction scope par requête).
- [ ] T003 [P] Initialiser Alembic à la racine du repo : créer `D:\Projets\dev\AI-Kaeyris\migrations\env.py` câblé sur `settings.DATABASE_URL` (lecture via pydantic-settings) ; créer `D:\Projets\dev\AI-Kaeyris\alembic.ini` ; vérifier que `alembic upgrade head` est invocable (sans migration encore).
- [ ] T004 [P] Ajouter dans `D:\Projets\dev\AI-Kaeyris\app\core\config.py` les nouveaux settings : `DATABASE_URL`, `KAEYRIS_DATA_DIR`, `TRANSCRIPTION_PROVIDER`, `TRANSCRIPTION_BASE_URL`, `TRANSCRIPTION_API_KEY`, `TRANSCRIPTION_MODEL`, `TRANSCRIPTION_TIMEOUT_SECONDS`, `TRANSCRIPTION_LANGUAGE_HINT` (cf. `contracts/transcription-adapter.md` §"Sélection au runtime").
- [ ] T005 [P] Mettre à jour `D:\Projets\dev\AI-Kaeyris\.env.example` avec les nouvelles variables d'environnement (sans valeurs réelles), commenter chaque bloc (`# Storage / DB`, `# Transcription`).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose** : bloc structurant qui doit être livré avant toute user story (modèles DB, adapter, auth roles, scaffolding service + tests). 

⚠️ **CRITICAL** : aucune US ne peut démarrer tant que cette phase n'est pas verte.

- [ ] T006 Définir tous les modèles SQLAlchemy 2.x dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\models.py` (entités : `ApiKey`, `Pj`, `Session`, `AudioSource`, `Transcription`, `SessionPjMapping`, `Artifact`, `Job`) en respectant strictement les schémas, FKs et invariants de `data-model.md`. Préfixer les noms de tables `jdr_*`.
- [ ] T007 Écrire la première migration Alembic `D:\Projets\dev\AI-Kaeyris\migrations\versions\0001_initial.py` qui crée toutes les tables de T006 + index utiles (FK indexes, `sessions(gm_key_id)`, `artifacts(session_id, kind)`). Vérifier `alembic upgrade head && alembic downgrade base` aller-retour.
- [ ] T008 [P] Créer `D:\Projets\dev\AI-Kaeyris\app\adapters\transcription.py` : interface `TranscriptionAdapter` (`typing.Protocol`), dataclasses `TranscriptionSegment` / `TranscriptionResult`, exceptions `TranscriptionError` / `TransientTranscriptionError` / `PermanentTranscriptionError`, `OpenAICompatibleTranscriptionAdapter`, `MockTranscriptionAdapter`, factory `build_transcription_adapter()` + cache `get_transcription_adapter()`. Reprendre le pattern et la mappage d'erreurs de `app/adapters/llm.py`.
- [ ] T009 Étendre `D:\Projets\dev\AI-Kaeyris\app\core\auth.py` pour migrer la lecture des clés du parsing `settings.API_KEYS` vers la table DB `jdr_api_keys` : ajouter l'enum `Role` (`gm` / `player`), `AuthenticatedKey` reçoit désormais `role` et `pj_id`, `_verify_against_registry` compare contre les clés DB actives, et `require_api_key` charge la session DB via la dépendance `get_db_session`. Conserver l'env var `API_KEYS` en mode bootstrap : au premier démarrage, importer ses entrées en DB avec `role='gm'`.
- [ ] T010 Ajouter dans `D:\Projets\dev\AI-Kaeyris\app\core\auth.py` la dépendance FastAPI `require_role(role: Role)` qui chaîne après `require_api_key` et lève `ForbiddenError` si le rôle ne correspond pas. Exposer aussi `require_gm` et `require_player` comme raccourcis.
- [ ] T011 [P] Créer `D:\Projets\dev\AI-Kaeyris\app\services\jdr\__init__.py` (vide) et `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` avec un `APIRouter(prefix="/services/jdr", tags=["jdr"])` et la dépendance par défaut `[Depends(require_api_key), Depends(enforce_rate_limit)]`. Pas encore de routes.
- [ ] T012 [P] Créer `D:\Projets\dev\AI-Kaeyris\app\services\jdr\schemas.py` avec les types Pydantic v2 communs (`ProblemDetail`-compatible, `JobOut`, `Page[T]` générique pour les listes).
- [ ] T013 [P] Créer `D:\Projets\dev\AI-Kaeyris\app\services\jdr\markdown.py` avec un en-tête utilitaire `render_session_header(session)` réutilisable par tous les exports MD, et la signature des fonctions `render_transcription_md`, `render_narrative_md`, `render_elements_md`, `render_pov_md` (corps : `raise NotImplementedError` à ce stade — implémenté par US).
- [ ] T014 [P] Créer `D:\Projets\dev\AI-Kaeyris\app\services\jdr\prompts.py` avec un docstring expliquant le rôle (centralisation des prompts système narrative/elements/POV per CLAUDE.md §2.4 et ADR 0005 §2). Constantes vides à compléter par US.
- [ ] T015 [P] Créer `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\__init__.py` et `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\repositories.py` avec une classe par entité (`ApiKeyRepository`, `PjRepository`, `SessionRepository`, `TranscriptionRepository`, `MappingRepository`, `ArtifactRepository`, `JobRepository`). Chaque repository prend une `AsyncSession` et expose les opérations utilisées dans les US (signatures uniquement, corps minimal).
- [ ] T016 Monter le router jdr dans `D:\Projets\dev\AI-Kaeyris\app\main.py` (`app.include_router(jdr_router)`) et brancher l'événement `startup` qui exécute le bootstrap auth (import depuis env var si DB vide).
- [ ] T017 [P] Créer `D:\Projets\dev\AI-Kaeyris\tests\conftest.py` (ou étendre l'existant) avec : fixture `db_session` (SQLite en mémoire + `Base.metadata.create_all`), fixture `fake_redis` (fakeredis.aioredis), fixture `client` (TestClient FastAPI), fixture `mock_transcription_adapter` (override de la factory), fixture `mock_llm_adapter`.
- [ ] T018 [P] Créer `D:\Projets\dev\AI-Kaeyris\tests\adapters\test_transcription.py` : tests unitaires pour `MockTranscriptionAdapter` (renvoie segments déterministes), pour `build_transcription_adapter` (sélection cloud/local par env var), pour la mappage d'erreurs.
- [ ] T019 [P] Créer `D:\Projets\dev\AI-Kaeyris\tests\core\test_auth_roles.py` : (a) bootstrap depuis env var importe une clé `gm`, (b) une clé `player` sans `pj_id` est refusée par l'auth, (c) `require_role('gm')` rejette une clé `player` avec 403, (d) lookup DB-backed après bootstrap.

**Checkpoint** : foundation prête, les user stories peuvent démarrer.

---

## Phase 3: User Story 1 — MJ archive et résume une session enregistrée (Priority: P1) 🎯 MVP

**Goal** : permettre à un MJ d'uploader un M4A, déclencher la chaîne transcription→résumé narratif en asynchrone, et récupérer transcription diarisée + résumé via l'API. C'est le scénario fondateur du service.

**Independent Test** : déposer une fixture audio courte (~30s, 2-3 locuteurs simulés), poller le job jusqu'à `succeeded`, lire `GET /sessions/{id}/transcription` (segments) puis demander+lire `narrative`. Vérifier que l'audio source a été purgé.

### Tests for User Story 1 (CLAUDE.md §2.5 — required)

> Écrire les tests AVANT l'implémentation des handlers.

- [ ] T020 [P] [US1] `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_sessions.py` : `POST /sessions` créé, `GET /sessions` liste les sessions du MJ courant uniquement, `GET /sessions/{id}` 404 si autre MJ, transitions d'état (`created` → `audio_uploaded`).
- [ ] T021 [P] [US1] `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_audio_upload.py` : multipart upload accepté pour M4A valide (sha256/duration calculés), refusé pour MIME ≠ M4A (FR-017), refusé en double upload (409). Vérifie que le fichier est écrit sous `KAEYRIS_DATA_DIR/audios/<session_id>.m4a` et qu'un job est enqueué.
- [ ] T022 [P] [US1] `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_transcription_flow.py` : avec `MockTranscriptionAdapter`, le job `transcribe_session_job` (a) écrit la row `transcriptions`, (b) supprime le fichier audio, (c) pose `audio_sources.purged_at`, (d) passe `sessions.state` à `transcribed`.
- [ ] T023 [P] [US1] `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_narrative.py` : avec `MockLLMAdapter`, `POST /artifacts/narrative` enqueue un job, le job UPSERT la row `artifacts` (`kind='narrative'`), `GET /artifacts/narrative` renvoie le contenu, deuxième `POST` écrase la précédente (FR-009 + R9).
- [ ] T024 [P] [US1] `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_markdown_transcription.py` : `GET /transcription.md` renvoie `Content-Type: text/markdown`, contient l'en-tête de session, un paragraphe par locuteur. `GET /artifacts/narrative.md` idem.
- [ ] T025 [P] [US1] `D:\Projets\dev\AI-Kaeyris\tests\jobs\test_jdr.py` : test unitaire du chunking d'un audio > 25 Mo (R3) avec `OpenAICompatibleTranscriptionAdapter` cloud — segments concaténés, timestamps réalignés ; test du re-mapping `TransientTranscriptionError`/`PermanentTranscriptionError` → `TransientJobError`/`PermanentJobError`.

### Implementation for User Story 1

- [ ] T026 [P] [US1] Compléter `D:\Projets\dev\AI-Kaeyris\app\services\jdr\prompts.py` avec `NARRATIVE_SYSTEM_PROMPT` (instructions : reconstituer l'ordre chronologique de la séance, prose neutre, pas d'inventions ; en français).
- [ ] T027 [P] [US1] Compléter `D:\Projets\dev\AI-Kaeyris\app\services\jdr\schemas.py` avec `SessionCreate`, `SessionOut`, `AudioUploadOut`, `TranscriptionSegmentOut`, `TranscriptionOut`, `NarrativeArtifactOut` (cf. `contracts/rest-api.md`).
- [ ] T028 [US1] Implémenter dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\logic.py` : `create_session(gm_key, payload)`, `list_sessions(gm_key)`, `get_session(gm_key, id)`, `store_audio_source(session, upload_file)` (calcule sha256, durée via subprocess `ffprobe`, écrit sur `KAEYRIS_DATA_DIR/audios/<session_id>.m4a`), transitions d'état.
- [ ] T029 [US1] Implémenter `D:\Projets\dev\AI-Kaeyris\app\jobs\jdr.py::transcribe_session_job(session_id: str)` : charge la session, appelle `get_transcription_adapter().transcribe(...)`, gère le découpage > 25 Mo (R3) si provider cloud, persiste `transcriptions`, supprime le fichier audio + UPDATE `audio_sources.purged_at`, met `sessions.state = transcribed`. Remap des erreurs adapter → erreurs job.
- [ ] T030 [US1] Implémenter `D:\Projets\dev\AI-Kaeyris\app\jobs\jdr.py::generate_narrative_job(session_id: str)` : charge la transcription, construit le `user` prompt à partir des segments, appelle `llm_complete(system=NARRATIVE_SYSTEM_PROMPT, user=…)`, UPSERT `artifacts(session_id, kind='narrative')`, met à jour la row `jobs`.
- [ ] T031 [US1] Compléter `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` avec les routes : `POST /sessions`, `GET /sessions`, `GET /sessions/{session_id}`, toutes gardées par `Depends(require_role('gm'))`.
- [ ] T032 [US1] Créer `D:\Projets\dev\AI-Kaeyris\app\services\jdr\batch\__init__.py` puis `D:\Projets\dev\AI-Kaeyris\app\services\jdr\batch\router.py` exposant `POST /sessions/{session_id}/audio` (multipart). Le handler appelle `logic.store_audio_source` puis `enqueue_job(transcribe_session_job, session_id, transient_errors=True)` et renvoie `202` avec `job_id`. Inclure ce sub-router dans `app/services/jdr/router.py`.
- [ ] T033 [US1] Ajouter dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` la route `GET /jobs/{job_id}` qui projette `Job.fetch(job_id, connection=redis)` au format `JobOut` (statut RQ → `queued/running/succeeded/failed`, fallback à la table `jdr_jobs` si TTL RQ expiré).
- [ ] T034 [US1] Ajouter dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` les routes `GET /sessions/{session_id}/transcription` (JSON) et `GET /sessions/{session_id}/transcription.md` (Markdown). 404 si pas encore disponible.
- [ ] T035 [US1] Ajouter dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` les routes `POST /sessions/{session_id}/artifacts/narrative` (enqueue + 202), `GET /sessions/{session_id}/artifacts/narrative` (JSON), `GET /sessions/{session_id}/artifacts/narrative.md` (Markdown). 409 si la session n'est pas `transcribed`.
- [ ] T036 [US1] Implémenter dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\markdown.py` les fonctions `render_transcription_md(session, transcription, mapping=None)` (un paragraphe par tour, préfixe `**[speaker_X → PJ Y]**` si mapping fourni, sinon `**[speaker_X]**`) et `render_narrative_md(session, narrative_artifact)`.
- [ ] T037 [P] [US1] Ajouter `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\fixtures\demo-session.m4a` : fichier M4A court (~30s, 2-3 locuteurs synthétiques ou échantillon libre). Documenter sa provenance dans `tests/services/jdr/fixtures/README.md`.

**Checkpoint** : User Story 1 fonctionnelle. Le scénario `quickstart.md §5.1 → §5.7` passe avec un MockTranscriptionAdapter et un MockLLMAdapter.

---

## Phase 4: User Story 2 — MJ extrait une fiche d'éléments structurés (Priority: P2)

**Goal** : produire à la demande une fiche structurée (PNJ, lieux, items, indices) à partir d'une transcription disponible.

**Independent Test** : sur la session de la phase 3, `POST /artifacts/elements` puis `GET /artifacts/elements` renvoie un objet `{npcs, locations, items, clues}` ; les listes sont vides plutôt qu'absentes (acceptance scenario US 2.3).

### Tests for User Story 2

- [ ] T038 [P] [US2] `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_elements.py` : enqueue + génération avec mock LLM ; `GET` renvoie les 4 listes nommées ; format JSON conforme à `data-model.md` §7.
- [ ] T039 [P] [US2] `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_elements_md.py` : `GET /artifacts/elements.md` produit 4 sections h2 nommées correctement (`## PNJ`, `## Lieux`, `## Items`, `## Indices`).

### Implementation for User Story 2

- [ ] T040 [P] [US2] Compléter `D:\Projets\dev\AI-Kaeyris\app\services\jdr\prompts.py` avec `ELEMENTS_SYSTEM_PROMPT` (consigne : extraire 4 listes JSON, vide si rien à mettre, format strict). Inclure un schéma JSON de sortie attendue dans le prompt pour aider le LLM.
- [ ] T041 [P] [US2] Ajouter dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\schemas.py` le type `ElementsArtifactOut` avec sous-types `Element` (`name`, `description`).
- [ ] T042 [US2] Implémenter `D:\Projets\dev\AI-Kaeyris\app\jobs\jdr.py::generate_elements_job(session_id: str)` : appelle le LLM avec `ELEMENTS_SYSTEM_PROMPT`, parse la réponse JSON (avec retry transient si parsing échoue, max 1 retry), UPSERT `artifacts(session_id, kind='elements')`.
- [ ] T043 [US2] Ajouter dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` les routes `POST /sessions/{session_id}/artifacts/elements`, `GET /sessions/{session_id}/artifacts/elements`, `GET /sessions/{session_id}/artifacts/elements.md`.
- [ ] T044 [US2] Implémenter `render_elements_md(session, elements_artifact)` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\markdown.py`.

**Checkpoint** : User Stories 1 et 2 fonctionnelles indépendamment.

---

## Phase 5: User Story 3 — Résumés "point de vue" par PJ (Priority: P3)

**Goal** : autoriser le MJ à déclarer ses PJ, à mapper `speaker_X → PJ`, et à générer un résumé POV par PJ.

**Independent Test** : créer 2 PJ, déclarer un mapping pour la session, `POST /artifacts/povs` ; `GET /artifacts/povs/{pj_id}` renvoie un résumé centré sur ce PJ ; demander un POV sans mapping ⇒ 409 (FR-011).

### Tests for User Story 3

- [ ] T045 [P] [US3] `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_pjs.py` : `POST /pjs` crée un PJ scoped au MJ courant, `GET /pjs` ne liste que les PJ du MJ, unicité du nom par MJ.
- [ ] T046 [P] [US3] `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_mapping.py` : `PUT /mapping` valide les `pj_id` (rejet si PJ d'un autre MJ → 422), modification du mapping invalide les rows `artifacts(kind LIKE 'pov:%')` correspondantes.
- [ ] T047 [P] [US3] `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_povs.py` : avec mapping {speaker_1→Galadriel, speaker_2→Aragorn}, `POST /artifacts/povs` enqueue un job qui produit deux rows `artifacts` (`kind='pov:<id>'`), `GET /artifacts/povs/{pj_id}.md` renvoie un MD scoppé.
- [ ] T048 [P] [US3] `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_povs_no_mapping.py` : `POST /artifacts/povs` sans mapping → 409 avec message clair (FR-011).

### Implementation for User Story 3

- [ ] T049 [P] [US3] Compléter `D:\Projets\dev\AI-Kaeyris\app\services\jdr\prompts.py` avec `POV_SYSTEM_PROMPT` (consigne : restituer du point de vue d'un PJ donné, en respectant ce qu'il pouvait percevoir/savoir, pas d'inventions).
- [ ] T050 [P] [US3] Ajouter dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\schemas.py` les types `PjCreate`, `PjOut`, `MappingPut`, `MappingOut`, `PovArtifactOut`.
- [ ] T051 [US3] Implémenter dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\logic.py` : `create_pj(gm_key, name)`, `list_pjs(gm_key)`, `set_session_mapping(gm_key, session_id, mapping)` (validation que tous les `pj_id` appartiennent au MJ + invalidation des rows `pov:*` existantes pour cette session).
- [ ] T052 [US3] Implémenter `D:\Projets\dev\AI-Kaeyris\app\jobs\jdr.py::generate_povs_job(session_id: str)` : charge la transcription, charge le mapping, pour chaque `(speaker_label, pj_id)` du mapping, construit un `user` prompt (segments où le PJ est présent + contexte minimal des autres scènes), appelle le LLM avec `POV_SYSTEM_PROMPT`, UPSERT `artifacts(session_id, kind='pov:<pj_id>')`. Mise à jour de la row `jobs` à la fin (succès si tous OK, partial-failure documentée).
- [ ] T053 [US3] Ajouter dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` les routes `POST /pjs`, `GET /pjs`, `PUT /sessions/{session_id}/mapping`, `GET /sessions/{session_id}/mapping`, `POST /sessions/{session_id}/artifacts/povs`, `GET /sessions/{session_id}/artifacts/povs/{pj_id}`, `GET /sessions/{session_id}/artifacts/povs/{pj_id}.md`. Toutes gardées par `require_role('gm')`.
- [ ] T054 [US3] Implémenter `render_pov_md(session, pj, pov_artifact)` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\markdown.py`.

**Checkpoint** : User Stories 1, 2, 3 fonctionnelles. La diarisation fonctionnelle dépend du provider en place : avec le provider cloud OpenAI Whisper API (default), tous les segments sont labellés `unknown` (cf. plan §Risks #1) — les POV resteront pauvres tant que le provider local LAN n'est pas branché. À documenter dans le README du service.

---

## Phase 6: User Story 4 — Joueurs consultent en lecture leurs résumés (Priority: P3)

**Goal** : exposer des endpoints `/me/*` pour qu'un joueur authentifié lise le résumé narratif des sessions où son PJ est mappé et son propre résumé POV — et **uniquement** ceux-là (FR-014).

**Independent Test** : enrôler un joueur lié à `pj_uuid_aragorn`, récupérer le token en clair, faire `GET /me/sessions` (ne voit qu'une session précise), faire `GET /me/sessions/{id}/pov.md` (le sien), tenter `GET /me/sessions/{id}/pov.md` sur une session non mappée → 403, tenter de deviner un autre `pj_id` → impossible (l'endpoint n'expose que le PJ courant).

### Tests for User Story 4

- [ ] T055 [P] [US4] `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_player_enroll.py` : `POST /players` renvoie un `token` en clair une seule fois ; le hash Argon2 est en DB ; révocation via `DELETE /players/{id}` empêche immédiatement l'auth.
- [ ] T056 [P] [US4] `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_player_access.py` (test critique FR-014) : un joueur ne peut PAS accéder au POV d'un autre joueur (403), ne peut PAS accéder aux endpoints d'écriture (403), ne peut PAS lister les PJ d'autres MJ.
- [ ] T057 [P] [US4] `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_player_listing.py` : `GET /me/sessions` ne liste que les sessions où le PJ du joueur est mappé.

### Implementation for User Story 4

- [ ] T058 [P] [US4] Ajouter dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\schemas.py` les types `PlayerCreate`, `PlayerOut` (avec champ `token` exposé une seule fois), `MeOut`, `PlayerSessionListOut`.
- [ ] T059 [US4] Implémenter dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\logic.py` : `enroll_player(gm_key, name, pj_id)` (vérifie que `pj_id` appartient au MJ, génère un token aléatoire de ≥ 32 octets entropie, calcule l'Argon2 hash, INSERT row `api_keys` avec `role='player'` + `pj_id`, retourne le token plaintext une seule fois) ; `revoke_player(gm_key, player_id)` (vérifie ownership, UPDATE `status='revoked'` + `revoked_at`).
- [ ] T060 [US4] Ajouter dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` les routes `POST /players`, `DELETE /players/{player_id}`, gardées par `require_role('gm')`.
- [ ] T061 [US4] Ajouter dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` les routes `GET /me`, `GET /me/sessions` (filtre par `mapping.pj_id == current.pj_id`), gardées par `require_role('player')`.
- [ ] T062 [US4] Ajouter dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` les routes `GET /me/sessions/{session_id}/narrative`, `GET /me/sessions/{session_id}/narrative.md`, `GET /me/sessions/{session_id}/pov`, `GET /me/sessions/{session_id}/pov.md`. Toutes vérifient que le PJ du joueur courant est mappé sur la session demandée (sinon `403`) et exposent uniquement son POV.

**Checkpoint** : User Stories 1, 2, 3, 4 fonctionnelles. Les MJ produisent les artefacts, les joueurs consomment leurs résumés en lecture seule sans voir ceux des autres.

---

## Phase 7: User Story 5 — Endpoint live "stub documenté" (Priority: P4)

**Goal** : matérialiser le contrat REST/WS du mode live (sans l'implémenter) pour figer la surface d'API publique avant le branchement Discord (Jalon 6+). Cf. FR-015/016 et `contracts/rest-api.md` §"Mode live (stub — Jalon 5)".

**Independent Test** : `POST /live/sessions` renvoie `501` avec un Problem Details dont le `type` URI pointe vers `errors/live-not-implemented` ; ouvrir un WS sur `/live/stream` voit la connexion fermée immédiatement avec code `1011` ; les deux sont visibles dans `/docs` (OpenAPI).

### Tests for User Story 5

- [ ] T063 [P] [US5] `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_live_stub.py` : `POST /live/sessions` répond 501 + body Problem Details ; le `documentation_url` est présent ; le schéma est listé dans `/openapi.json`.

### Implementation for User Story 5

- [ ] T064 [P] [US5] Créer `D:\Projets\dev\AI-Kaeyris\app\services\jdr\live\__init__.py` puis `D:\Projets\dev\AI-Kaeyris\app\services\jdr\live\router.py` : sous-router avec `POST /live/sessions` qui lève une `AppError` `LiveNotImplementedError` (501, `type=errors/live-not-implemented`). Schéma de requête `LiveSessionInit` documenté en Pydantic mais jamais traité.
- [ ] T065 [P] [US5] Ajouter dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\live\router.py` un endpoint WebSocket `@router.websocket("/live/stream")` qui à la connexion fait `await ws.accept()` puis immédiatement `await ws.close(code=1011, reason="stub — not yet implemented at Jalon 5")`. Documenter les futurs events (`audio.chunk`, `session.end`, `error`) en commentaires Python dans le fichier (visibles dans la description OpenAPI du WS).
- [ ] T066 [US5] Inclure `live.router` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` afin que les routes apparaissent dans l'OpenAPI global.

**Checkpoint** : toutes les user stories fonctionnelles. Le mode live a son contrat publié sans chair.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose** : améliorations transverses, documentation, Definition of Done jalon.

- [ ] T067 [P] Rédiger `D:\Projets\dev\AI-Kaeyris\docs\adr\0006-jdr-service.md` : décisions du Jalon 5 (introduction ORM, posture transcription hybride, auth roles, mode live stub). Format identique aux ADR 0001-0005.
- [ ] T068 [P] Mettre à jour `D:\Projets\dev\AI-Kaeyris\README.md` : ajouter une section "Service `kaeyris-jdr` (Jalon 5)" qui pointe vers `quickstart.md`, mentionne le scénario E2E, liste les nouvelles env vars.
- [ ] T069 [P] Mettre à jour `D:\Projets\dev\AI-Kaeyris\docs\memo.md` : ajouter une ligne par nouvelle commande pertinente (`alembic upgrade head`, `rq worker`, lancement de l'API en mode jdr) et par choix techno (SQLAlchemy, faster-whisper, pyannote).
- [ ] T070 [P] Ajouter une entrée dans `D:\Projets\dev\AI-Kaeyris\docs\journal.md` pour le Jalon 5 : ce qui a été appris (premier ORM, premier service complet, hybride cloud/local), coûts LLM observés sur la première session réelle, anecdotes d'usage.
- [ ] T071 [P] Créer `D:\Projets\dev\AI-Kaeyris\docs\services\jdr.md` (premier document du dossier `docs/services/` per CLAUDE.md §4.2) : description, architecture interne, conventions de prompts, instructions opérationnelles.
- [ ] T072 [P] Mettre à jour `D:\Projets\dev\AI-Kaeyris\docker-compose.yml` : déclarer un volume `kaeyris-data` monté sur `KAEYRIS_DATA_DIR` pour le worker et l'API (audios + DB SQLite). PAS de Postgres en Compose à ce jalon (cible Jalon 8).
- [ ] T073 Documenter dans `D:\Projets\dev\AI-Kaeyris\docs\services\jdr.md` la procédure de stand-up de l'hôte GPU LAN (faster-whisper + pyannote) en lien avec la mémoire `infrastructure_topology.md` — pas d'implémentation, juste un README qui décrit l'API attendue côté hôte GPU.
- [ ] T074 [P] Exécuter `ruff check D:\Projets\dev\AI-Kaeyris` et corriger les warnings introduits par les nouveaux fichiers.
- [ ] T075 Exécuter `pytest D:\Projets\dev\AI-Kaeyris` et viser 100% vert (DoD §7 critère 2).
- [ ] T076 Validation manuelle : suivre `quickstart.md §5` et `§6` de bout en bout avec une vraie clé DeepInfra et la fixture audio. Vérifier que l'OpenAPI à `http://localhost:8000/docs` liste bien : sessions, audio, jobs, transcription, narrative, elements, povs, pjs, players, me/*, live/*. Cocher les critères DoD §7 #1-7.
- [ ] T077 Mettre à jour `D:\Projets\dev\AI-Kaeyris\specs\001-kaeyris-jdr\checklists\requirements.md` pour refléter l'état post-implémentation (statuts confirmés).
- [ ] T078 Commit final sous le format Conventional Commits : `feat(jdr): kaeyris-jdr service for Jalon 5 (transcription, summaries, POV, player access)`. Ne pas amender les commits intermédiaires.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)** : peut démarrer immédiatement.
- **Phase 2 (Foundational)** : démarre après Phase 1 ; **bloque toutes les US**.
- **Phase 3-7 (User Stories)** : démarrent après Phase 2.
  - US1 (P1) MVP : doit être livrée en premier. C'est le seul prérequis fonctionnel des autres.
  - US2 (P2), US3 (P3) : peuvent démarrer en parallèle après US1, ou séquentiellement. US3 ajoute le mapping ; US4 dépend de US3 (le mapping qu'il consulte).
  - US4 (P3) : démarre après US3 (les endpoints `/me/*` lisent le mapping).
  - US5 (P4) : indépendante, peut démarrer après Phase 2 en parallèle de US1.
- **Phase 8 (Polish)** : démarre après les US ciblées du Jalon (au minimum US1 — et idéalement toutes pour valider DoD §7).

### Within Each User Story

- Tests **avant** implémentation (CLAUDE.md §2.5 : "non-trivial logic, prefer test-first").
- Models / schemas → logic → jobs → routes → markdown.
- Une story ne franchit pas le checkpoint tant que ses tests ne sont pas verts en isolation.

### Parallel Opportunities

- Phase 1 : T002, T003, T004, T005 sont indépendants ⇒ parallèles.
- Phase 2 : T008 (transcription adapter) et T009-T010 (auth) sont indépendants ; T011-T015 (scaffolding service) parallélisables avec T008/009/010 sauf qu'ils dépendent de T006 (models) et T007 (migration). T017-T019 (tests foundational) indépendants entre eux.
- Phase 3 : tests US1 (T020-T025) parallélisables ; T026/T027 (prompts/schemas) parallélisables. Tâches sur `app/services/jdr/router.py` ne sont PAS parallèles entre elles (même fichier).
- Phases 4, 5, 6 : leurs tests respectifs sont parallèles ; les routes sur `router.py` ne le sont pas.
- US5 : T064 et T065 sur des endpoints distincts dans `live/router.py` mais même fichier ⇒ pas [P] entre eux. T063 (test) [P] avec eux.
- Phase 8 : la plupart des doc tasks (T067-T071) sont [P]. T074 (`ruff`) est [P] avec elles. T075/T076 sont séquentielles à la fin.

---

## Parallel Example: User Story 1

```bash
# Étape 1 — lancer en parallèle les 6 tâches de tests (fichiers distincts) :
Task: "tests/services/jdr/test_sessions.py"          # T020
Task: "tests/services/jdr/test_audio_upload.py"      # T021
Task: "tests/services/jdr/test_transcription_flow.py" # T022
Task: "tests/services/jdr/test_narrative.py"         # T023
Task: "tests/services/jdr/test_markdown_transcription.py" # T024
Task: "tests/jobs/test_jdr.py"                        # T025

# Étape 2 — lancer en parallèle les ajouts dans des fichiers distincts :
Task: "Compléter app/services/jdr/prompts.py (NARRATIVE_SYSTEM_PROMPT)"   # T026
Task: "Compléter app/services/jdr/schemas.py (Session*, Narrative*)"     # T027
Task: "Ajouter tests/services/jdr/fixtures/demo-session.m4a"             # T037

# Étape 3 — implémentation séquentielle (router.py est mono-fichier) :
T028 → T029 → T030 → T031 → T032 → T033 → T034 → T035 → T036
```

---

## Implementation Strategy

### MVP-first (US1 uniquement)

1. Phase 1 (Setup) — ~1-2h.
2. Phase 2 (Foundational) — ~1-2 jours (le gros bloc DB + auth + adapter).
3. Phase 3 (US1) — ~1-2 jours.
4. **STOP & VALIDATE** : suivre `quickstart.md §5`, vérifier que la chaîne upload → transcription mock → narrative → MD fonctionne. Démo possible.

À ce stade, le service produit déjà une transcription + un résumé narratif sur n'importe quel M4A, ce qui couvre 70% de la valeur fonctionnelle.

### Incremental delivery

5. Ajouter US2 (Phase 4) — fiche d'éléments. ~½ jour.
6. Ajouter US3 (Phase 5) — PJ + mapping + POV. ~1 jour.
7. Ajouter US4 (Phase 6) — endpoints joueur. ~½ jour.
8. Ajouter US5 (Phase 7) — stub live. ~2h (vraiment minimal).
9. Phase 8 (Polish + DoD) — ~½ jour.

### Bascule provider de transcription

À tester explicitement (SC-009) : changer `TRANSCRIPTION_PROVIDER=local` + `TRANSCRIPTION_BASE_URL=http://gpu-host.lan:8001/v1` puis redémarrer le worker. Aucun fichier de `app/services/jdr/` ne doit être modifié pour cette bascule.

---

## Notes

- `[P]` = fichiers différents, pas de dépendance bloquante.
- `[Story]` = label de traçabilité vers l'US correspondante du spec.
- Chaque US est livrable et testable indépendamment dès que sa phase passe.
- Vérifier que les tests **échouent** avant d'écrire le code (test-first).
- Commit après chaque tâche ou groupe logique de tâches (pas de mega-commit). Format Conventional Commits, en français pour le sujet, anglais pour les types (`feat:`, `test:`, `refactor:`).
- S'arrêter à n'importe quel checkpoint pour valider la story en isolation.
- Anti-pattern à éviter : vouloir tout livrer d'un coup avant le checkpoint US1.
