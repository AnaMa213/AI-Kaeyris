---
description: "Tasks for 002-non-diarised-mode — mode `non_diarised` (pipeline alternatif sans diarisation)"
---

# Tasks: Mode `non_diarised` (pipeline alternatif sans diarisation)

**Input**: Design documents from `D:\Projets\dev\AI-Kaeyris\specs\002-non-diarised-mode\`
**Prerequisites** : `plan.md` ✅, `spec.md` ✅, `research.md` ✅, `data-model.md` ✅, `contracts/` ✅, `quickstart.md` ✅

**Tests** : INCLUS PAR DÉFAUT pour ce projet — CLAUDE.md §2.5 impose ≥ 1 test par endpoint public et la pyramide de tests classique (cohérent avec le Jalon 5).

**Organization** : tasks groupées par user story pour permettre une livraison MVP-first puis incrémentale.

## Format: `[ID] [P?] [Story] Description`

- **[P]** : peut tourner en parallèle (fichier différent, pas de dépendance bloquante).
- **[Story]** : à quelle user story la tâche appartient (US1, US2, US3). Setup/Foundational/Polish n'ont pas de label.
- Chaque tâche cite le **chemin de fichier exact** dans le repo.

## Path Conventions

Convention monolithe modulaire AI-Kaeyris (cf. `plan.md §Project Structure` et ADR 0001) :

- Code : `app/` (sous-dossiers `core/`, `adapters/`, `jobs/`, `services/jdr/`).
- Tests : `tests/` miroir de `app/`.
- Migrations DB : `migrations/` à la racine (Alembic).
- Documentation : `docs/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose** : pré-requis techniques au niveau du repo (config, migration DB) avant écriture du code métier.

- [X] T001 Ajouter le setting `KAEYRIS_CHUNK_MAX_CHARS: int = 30000` dans `D:\Projets\dev\AI-Kaeyris\app\core\config.py` (validation `> 0` au démarrage via `pydantic-settings`) et documenter la variable dans `D:\Projets\dev\AI-Kaeyris\.env.example` avec un commentaire (taille en caractères max d'un chunk de transcription en mode `non_diarised`).
- [X] T002 Créer la migration Alembic `D:\Projets\dev\AI-Kaeyris\migrations\versions\0003_non_diarised_mode.py` (renuméroté de 0002 → 0003 car `0002_add_campaign_context.py` existait déjà au Jalon 5) qui : (a) `ALTER TABLE jdr_sessions ADD COLUMN transcription_mode VARCHAR(16) NOT NULL DEFAULT 'diarised'` (avec `server_default`), (b) `CREATE TABLE jdr_chunks(...)` cf. `data-model.md §3`, (c) `CREATE TABLE jdr_session_players(...)` cf. `data-model.md §4`. Aller-retour `alembic upgrade head` / `alembic downgrade -1` testé OK.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose** : bloc structurant — modèles ORM, schemas Pydantic, repositories, prompts. Doit être livré avant toute user story.

⚠️ **CRITICAL** : aucune US ne peut démarrer tant que cette phase n'est pas verte.

- [X] T003 [P] Ajouter l'énumération `TranscriptionMode(str, enum.Enum)` (`DIARISED = "diarised"`, `NON_DIARISED = "non_diarised"`) dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\models.py`, et la colonne correspondante `transcription_mode: Mapped[TranscriptionMode]` sur `Session` (NOT NULL, default `DIARISED`, server_default `'diarised'`, cohérent avec la migration T002). Note : `String(16)` au lieu de `Enum(...)` côté ORM pour parité avec la migration et cohérence research.md §7. Ajoute aussi `JobKind.SUMMARY`.
- [X] T004 [P] Créer la classe ORM `Chunk` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\models.py` (table `jdr_chunks`) avec les champs `id`, `session_id`, `ordre`, `text`, `summary_text` (nullable), `created_at` ; contrainte `UniqueConstraint("session_id", "ordre")` ; relation `session: Mapped[Session]`. Strict respect de `data-model.md §3`.
- [X] T005 [P] Créer la classe ORM `SessionPlayer` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\models.py` (table `jdr_session_players`) avec PK composite `(session_id, pj_id)`, FK CASCADE vers `jdr_sessions`, FK RESTRICT vers `jdr_pjs`, `created_at`. Relations bidirectionnelles.
- [X] T006 [P] Créer `ChunkRepository` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\repositories.py` avec : `bulk_create_for_session(session_id, *, texts: list[str])` (insert atomique de N rows, `ordre = i`), `list_for_session(session_id) -> list[Chunk]` (ordonnés par `ordre`), `update_summary_text(chunk_id, *, summary_text)`, `reset_summary_texts(session_id) -> int` (UPDATE ... SET summary_text = NULL, retourne le rowcount).
- [X] T007 [P] Créer `SessionPlayerRepository` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\repositories.py` avec : `replace_for_session(session_id, *, pj_ids)` (DELETE + INSERT atomique + dédup), `list_for_session(session_id) -> list[SessionPlayer]`.
- [X] T008 [P] Étendre `SessionRepository.create(...)` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\repositories.py` pour accepter un paramètre `transcription_mode: TranscriptionMode = TranscriptionMode.DIARISED` et le passer au constructeur.
- [X] T009 [P] Ajouter les constantes `SUMMARY_MAP_SYSTEM_PROMPT` et `SUMMARY_REDUCE_SYSTEM_PROMPT` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\prompts.py` (en français, conformes à `research.md §3` — fidélité au texte, pas d'invention, pas de méta-commentaire, préservation de la chronologie au reduce).
- [X] T010 [P] Étendre `D:\Projets\dev\AI-Kaeyris\app\services\jdr\schemas.py` avec : `TranscriptionMode` (réexporté), `SessionCreate` accepte `transcription_mode: TranscriptionMode | None = None` (avec validation), `SessionOut` expose `transcription_mode`, `ChunkOut` (`chunk_id` via `validation_alias="id"`, `ordre`, `text`), `ChunkListOut` (envelope `items`), `SummaryArtifactOut` (`session_id`, `text`, `model_used`, `generated_at`), `SessionPlayersIn` (`pj_ids: list[UUID]`, max 50, non vide), `SessionPlayersOut` (`session_id`, `pj_ids`, `updated_at`).
- [X] T011 [P] Créer le module utilitaire `D:\Projets\dev\AI-Kaeyris\app\services\jdr\text_chunker.py` : fonction `chunk_text(text: str, max_chars: int) -> list[str]` qui découpe sur frontières naturelles (priorité `\n\n` > `[.!?]\s` > `\s` > coupe brute), implémentation pure (pas de DB, pas d'I/O), respecte `research.md §1`.
- [X] T012 [P] Créer `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_text_chunker.py` : 10 tests unitaires du chunker (texte vide, texte court < max, texte > max avec et sans frontières naturelles, edge case texte sans aucun espace > max, max_chars ≤ 0 raise). Tous verts.
- [ ] T013 [P] Ajouter dans `D:\Projets\dev\AI-Kaeyris\tests\conftest.py` (si besoin) une fixture `chunk_max_chars` qui patche `settings.KAEYRIS_CHUNK_MAX_CHARS` à une valeur faible (ex. 200) pour les tests de chunking en intégration. **Déféré** : pas nécessaire pour les tests Phase 2 ; à réintroduire si un test US1+ en a besoin.

**Checkpoint** : foundation prête, les user stories peuvent démarrer.

---

## Phase 3: User Story 1 — MJ crée une session `non_diarised` et obtient une transcription chunked (Priority: P1) 🎯 MVP

**Goal** : permettre au MJ de créer une session avec `transcription_mode = "non_diarised"`, uploader un audio, et récupérer la transcription stockée en chunks ordonnés via `GET /sessions/{id}/chunks`. C'est le scénario foundational — sans cette US, aucune fonctionnalité en aval n'est accessible.

**Independent Test** : créer une session avec `transcription_mode = "non_diarised"`, uploader un M4A court, poller le job de transcription jusqu'à `succeeded`. Vérifier que `jdr_chunks` contient N rows ordonnées et que `jdr_transcriptions` est vide pour cette session ; vérifier symétriquement qu'une session créée sans tag continue d'écrire dans `jdr_transcriptions` (non-régression Jalon 5).

### Tests for User Story 1

> Écrire les tests AVANT l'implémentation des handlers (CLAUDE.md §2.5).

- [ ] T014 [P] [US1] Créer `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_sessions_with_mode.py` : `POST /sessions` accepte `transcription_mode="non_diarised"` (201 + champ renvoyé), défaut `diarised` si absent (non-régression), valeur invalide → `422 invalid-transcription-mode`. `PATCH /sessions/{id}` avec `transcription_mode` dans le body → `422 immutable-field`. `GET /sessions/{id}` expose toujours `transcription_mode`.
- [ ] T015 [P] [US1] Créer `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_chunks_endpoint.py` : `GET /sessions/{id}/chunks` sur session `non_diarised` avec chunks seedés → 200 + items ordonnés par `ordre`. Sur session sans chunks (`audio_uploaded`) → `404 transcription-not-ready`. Sur session `diarised` → `409 wrong-mode`. 404 si session non-owned par le MJ courant.
- [ ] T016 [P] [US1] Créer `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_transcription_flow_non_diarised.py` : avec `MockTranscriptionAdapter`, le job `_transcribe_session` sur une session `non_diarised` (a) calcule la concaténation des segments adapter, (b) appelle `text_chunker.chunk_text` avec `KAEYRIS_CHUNK_MAX_CHARS`, (c) écrit N rows `jdr_chunks` ordonnées via `ChunkRepository.bulk_create_for_session`, (d) **n'écrit pas** dans `jdr_transcriptions`, (e) supprime l'audio + pose `audio_sources.purged_at`, (f) passe `sessions.state` à `transcribed`. Test miroir sur session `diarised` qui écrit dans `jdr_transcriptions` (régression Jalon 5).
- [ ] T017 [P] [US1] Ajouter un test critique dans `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_mode_isolation.py` : `GET /sessions/{id}/transcription` (et `.md`) sur session `non_diarised` → `409 wrong-mode` avec message qui pointe vers `/chunks`.

### Implementation for User Story 1

- [ ] T018 [US1] Étendre `logic.create_session(...)` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\logic.py` pour accepter `transcription_mode: TranscriptionMode | None = None` (default `TranscriptionMode.DIARISED`) et le propager au repository (T008 fait déjà l'extension côté repo).
- [ ] T019 [US1] Étendre la route `POST /sessions` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` pour propager le `transcription_mode` du payload `SessionCreate` à `logic.create_session`. Ajouter l'AppError `InvalidTranscriptionModeError` (422, error_type `"invalid-transcription-mode"`) levée si Pydantic refuse la valeur.
- [ ] T020 [US1] Étendre la route `PATCH /sessions/{id}` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` : si `transcription_mode` figure dans le `request.json()` brut (FastAPI `model_fields_set`), lever `ImmutableFieldError` (422, error_type `"immutable-field"`, detail mentionne `transcription_mode`).
- [ ] T021 [US1] Ajouter l'AppError `WrongModeError` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` (status 409, error_type `"wrong-mode"`). Utilisée par toutes les routes mode-sensibles (introduites par US1, US2, US3).
- [ ] T022 [US1] Étendre `_transcribe_session` dans `D:\Projets\dev\AI-Kaeyris\app\jobs\jdr.py` : après l'appel `adapter.transcribe(...)`, vérifier `session_row.transcription_mode`. Si `non_diarised` : concaténer `segment.text` pour tous les segments, appeler `text_chunker.chunk_text(...)`, appeler `ChunkRepository(db).bulk_create_for_session(session_id, [ChunkData(ordre=i, text=t) for i,t in enumerate(chunks)])`. **Ne pas** écrire dans `TranscriptionRepository` dans ce branch. Le reste du flow (purge audio, transition d'état) reste identique.
- [ ] T023 [US1] Implémenter `logic.list_session_chunks(db, *, session_id, gm_key_id) -> list[Chunk] | None` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\logic.py` (charge la session pour ownership, retourne None si pas owned, sinon délègue à `ChunkRepository.list_for_session`).
- [ ] T024 [US1] Ajouter la route `GET /sessions/{session_id}/chunks` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` (gardée par `require_gm`). Renvoie `ChunkListOut`. 404 si session non-owned ou absente. 409 `wrong-mode` si session `diarised`. 404 `transcription-not-ready` si aucun chunk.
- [ ] T025 [US1] Modifier les routes existantes `GET /sessions/{id}/transcription` et `GET /sessions/{id}/transcription.md` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` pour rejeter 409 `wrong-mode` si la session est `non_diarised` (un check supplémentaire avant le lookup transcription).

**Checkpoint** : User Story 1 fonctionnelle. Le scénario `quickstart.md §1-3` passe avec un `MockTranscriptionAdapter` configuré pour produire ~50 segments concaténables.

---

## Phase 4: User Story 2 — MJ obtient un résumé global de session via map-reduce (Priority: P2)

**Goal** : sur une session `non_diarised` avec chunks stockés, déclencher `POST /artifacts/summary` qui appelle le LLM 1 fois par chunk (map, persisté inline dans `chunks.summary_text`) puis 1 fois pour consolider (reduce), et publie l'artefact `summary` consultable en JSON et Markdown. C'est ce qui justifie l'effort de US1 — la transcription chunked n'a pas de valeur en soi pour le MJ sans le résumé.

**Independent Test** : sur une session `non_diarised` avec 3 chunks seedés, mocker le `LLMAdapter` (3 réponses map distinctes + 1 réponse reduce). `POST /artifacts/summary`, poller jusqu'à `succeeded`. Vérifier : 4 appels LLM dans l'ordre attendu, `chunks.summary_text` peuplés pour les 3 chunks dans l'ordre, row `artifacts(kind="summary")` contient bien le texte reduce. Test miroir 1-chunk : 1 seul appel LLM, pas de reduce.

### Tests for User Story 2

- [ ] T026 [P] [US2] Créer `D:\Projets\dev\AI-Kaeyris\tests\jobs\test_jdr_summary.py` : avec `_StubLLM` séquentiel, `_generate_summary(session_id)` sur une session 3-chunks (a) appelle le LLM 4 fois dans l'ordre attendu (3 map + 1 reduce), (b) `chunks.summary_text` peuplés, (c) row `artifacts(kind="summary")` créée avec le texte reduce, (d) `model_used` = `f"{settings.LLM_PROVIDER}:{settings.LLM_MODEL}"`. Test miroir 1-chunk : 1 seul appel LLM, le `summary_text` du chunk unique sert directement de `summary` global.
- [ ] T027 [P] [US2] Créer `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_summary.py` : `POST /sessions/{id}/artifacts/summary` sur session `non_diarised` `transcribed` → 202 + `JobQueuedOut` (kind=`summary`). Sur session `diarised` → 409 `wrong-mode`. Sur session pas transcribed → 409 `session-not-transcribed`. Sur session sans chunks → 409 `no-chunks`. `GET /artifacts/summary` → 200 + `SummaryArtifactOut`, ou 404 si pas généré. `GET /artifacts/summary.md` → `text/markdown` avec en-tête de session standard.
- [ ] T028 [P] [US2] Créer `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_summary_cascade.py` (test critique FR-011) : seed une session `non_diarised` avec `chunks.summary_text` peuplés + artefacts `narrative`, `elements`, `pov:<pj_id>` existants. Re-`POST /artifacts/summary`. Vérifier au début du nouveau job (avant les LLM calls) : (a) tous les `chunks.summary_text` sont remis à NULL, (b) les rows `artifacts(kind IN ('narrative', 'elements'))` ET `artifacts(kind LIKE 'pov:%')` sont supprimées, (c) la transaction est atomique (test via mock LLM qui raise au map : vérifier rollback de l'état antérieur).

### Implementation for User Story 2

- [ ] T029 [US2] Compléter `SUMMARY_MAP_SYSTEM_PROMPT` et `SUMMARY_REDUCE_SYSTEM_PROMPT` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\prompts.py` (contenu fonctionnel français, calé sur `research.md §3`).
- [ ] T030 [US2] Implémenter `_generate_summary(session_id)` dans `D:\Projets\dev\AI-Kaeyris\app\jobs\jdr.py` : (1) charge session, vérifie `transcription_mode == NON_DIARISED` (sinon PermanentJobError), vérifie state TRANSCRIBED, charge chunks ordonnés ; (2) **transaction reset** : `ChunkRepository.reset_summary_texts(session_id)` + cascade DELETE des artefacts `narrative`/`elements`/`pov:%` via `ArtifactRepository` ; commit ; (3) **phase map** : pour chaque chunk, appel LLM avec `SUMMARY_MAP_SYSTEM_PROMPT` + user prompt = `chunk.text`, persist via `ChunkRepository.update_summary_text(chunk_id, response)`, commit par chunk ; (4) **phase reduce** : si > 1 chunk, concat des `summary_text` séparés par `\n\n---\n\n`, appel LLM avec `SUMMARY_REDUCE_SYSTEM_PROMPT` ; sinon le `summary_text` du chunk unique est le résumé final ; (5) UPSERT `artifacts(kind="summary")` ; commit final. Mapping erreurs LLM → `TransientJobError` / `PermanentJobError` cohérent avec ADR 0004.
- [ ] T031 [US2] Ajouter le sync wrapper `generate_summary_job(session_id)` dans `D:\Projets\dev\AI-Kaeyris\app\jobs\jdr.py` et l'enregistrer dans `_FUNC_NAME_TO_KIND` (côté router) avec le nouveau `JobKind.SUMMARY` (ajouter aussi dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\models.py` la valeur `SUMMARY = "summary"` sur l'enum `JobKind`).
- [ ] T032 [US2] Ajouter la route `POST /sessions/{session_id}/artifacts/summary` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` (require_gm). Pré-checks : session ownership (404), `transcription_mode == NON_DIARISED` (409 wrong-mode), state TRANSCRIBED (409 session-not-transcribed), au moins 1 chunk (409 no-chunks via nouveau AppError `NoChunksError`). Enqueue `generate_summary_job` et retourne `JobQueuedOut`.
- [ ] T033 [US2] Ajouter la route `GET /sessions/{session_id}/artifacts/summary` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` qui lit `artifacts(session_id, kind="summary")` et retourne `SummaryArtifactOut`. 404 `artifact-not-ready` si pas généré. 409 `wrong-mode` si session `diarised`.
- [ ] T034 [US2] Implémenter `render_summary_md(session, summary_artifact)` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\markdown.py` (réutilise `render_session_header`, layout calé sur `render_narrative_md`).
- [ ] T035 [US2] Ajouter la route `GET /sessions/{session_id}/artifacts/summary.md` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` qui sert `text/markdown; charset=utf-8`. Mêmes erreurs que la version JSON.
- [ ] T036 [US2] Ajouter `NoChunksError(AppError)` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` (status 409, error_type `"no-chunks"`).

**Checkpoint** : User Stories 1 et 2 fonctionnelles. Le MJ peut désormais ingérer une session `non_diarised`, la résumer globalement, et lire le résumé en JSON ou Markdown.

---

## Phase 5: User Story 3 — MJ génère narrative, elements, povs sur une session `non_diarised` (Priority: P3)

**Goal** : sur une session `non_diarised` dont le `summary` est généré (donc les `chunks.summary_text` sont peuplés), les endpoints existants `POST /artifacts/{narrative|elements|povs}` continuent de fonctionner mais consomment les résumés des chunks au lieu des segments diarisés. Le mapping `speaker → PJ` du Jalon 5 est remplacé par une liste plate de PJ présents via `POST /sessions/{id}/players`.

**Independent Test** : sur une session `non_diarised` avec `chunks.summary_text` peuplés, déclarer 2 PJ via `POST /players`, déclencher `POST /artifacts/povs`. Vérifier : 2 rows `artifacts(kind="pov:<pj_id>")` produites (une par PJ déclaré) à partir des `summary_text` concaténés, sans nouveau map LLM (chunks.summary_text réutilisés). Test miroir : sur session `non_diarised` sans summary, `POST /artifacts/{narrative|elements|povs}` → `409 no-summary`.

### Tests for User Story 3

- [ ] T037 [P] [US3] Créer `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_players.py` : `POST /sessions/{id}/players` sur session `non_diarised` avec 2 PJ owned → 200 + `SessionPlayersOut`. `POST /players` sur session `diarised` → 409 wrong-mode. `POST /players` avec un `pj_id` d'un autre MJ → 422 `invalid-player-list`. `POST /players` deux fois remplace intégralement la liste (sémantique PUT-like). `GET /players` renvoie la liste actuelle.
- [ ] T038 [P] [US3] Créer `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_non_diarised_artefacts.py` : seed session `non_diarised` avec `chunks.summary_text` peuplés (3 chunks) + 2 PJ déclarés via `SessionPlayer`. Avec `_StubLLM`, déclencher successivement `_generate_narrative` / `_generate_elements` / `_generate_povs`. Vérifier : (a) le `user` prompt envoyé au LLM contient les `chunks.summary_text` concaténés avec `\n\n---\n\n`, **pas** la transcription brute ni `segments_json` ; (b) `narrative` produit `artifacts(kind="narrative")` ; (c) `elements` produit `artifacts(kind="elements")` avec les 4 listes JSON ; (d) `povs` produit 2 rows `artifacts(kind="pov:<pj_id>")` (une par PJ déclaré) ; (e) le prompt système est identique à celui du Jalon 5 (les system prompts sont réutilisés tels quels per `research.md §3`).
- [ ] T039 [P] [US3] Compléter `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_mode_isolation.py` (déjà créé partiellement en US1 T017) avec : `PUT /mapping` sur session `non_diarised` → 409 wrong-mode ; `POST /artifacts/summary` sur session `diarised` → 409 wrong-mode ; `POST /artifacts/{narrative|elements|povs}` sur session `non_diarised` sans summary → 409 `no-summary` ; idem sur session `diarised` sans mapping → 409 `no-mapping` (régression Jalon 5).
- [ ] T040 [P] [US3] Ajouter dans `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_summary_cascade.py` (déjà créé en US2 T028) un cas additionnel : après une régénération de `summary` qui cascade-delete les artefacts dérivés, un `POST /artifacts/{narrative|elements|povs}` immédiat doit produire de nouveaux artefacts sans erreur (re-build complet).

### Implementation for User Story 3

- [ ] T041 [US3] Implémenter `logic.set_session_players(db, *, session, pj_ids, gm_key_id)` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\logic.py` : (a) vérifie `session.transcription_mode == NON_DIARISED` (sinon raise `WrongModeError` côté logic — pattern à symétriser avec `DuplicatePjError`) ; (b) valide que tous les `pj_ids` appartiennent à `gm_key_id` (sinon raise `InvalidPlayerListError(Exception)` côté logic) ; (c) appelle `SessionPlayerRepository.replace_for_session`. Implémenter aussi `logic.list_session_players(db, *, session) -> list[SessionPlayer]`.
- [ ] T042 [US3] Ajouter les AppError `InvalidPlayerListAppError(AppError)` (422, `"invalid-player-list"`) et `WrongModeError(AppError)` (si pas déjà créé en US1 T021) dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`.
- [ ] T043 [US3] Ajouter les routes `POST /sessions/{session_id}/players` et `GET /sessions/{session_id}/players` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` (require_gm). Catch `InvalidPlayerListError` logic → raise `InvalidPlayerListAppError`. Catch `WrongModeError` logic → raise `WrongModeError` router (cf. T021). Renvoient `SessionPlayersOut`.
- [ ] T044 [US3] Modifier les routes existantes `PUT /sessions/{id}/mapping` et `GET /sessions/{id}/mapping` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` pour rejeter 409 `wrong-mode` si la session est `non_diarised` (check ajouté avant le handler logic).
- [ ] T045 [US3] Étendre `_generate_narrative(session_id)` dans `D:\Projets\dev\AI-Kaeyris\app\jobs\jdr.py` : check `session.transcription_mode`. Si `non_diarised` : (a) charge chunks ordonnés, (b) vérifie que tous les `summary_text` sont non-NULL — sinon `PermanentJobError("session_summary not generated")` ; (c) construit le user prompt en concatenant `chunks.summary_text` avec `\n\n---\n\n` + un en-tête expliquant "ceci est un résumé chunked d'une transcription sans diarisation ; déduis qui parle à partir du contexte" ; (d) appelle LLM avec `NARRATIVE_SYSTEM_PROMPT` (réutilisé tel quel) ; (e) UPSERT artifact. Branch `diarised` inchangé (régression FR-014).
- [ ] T046 [US3] Étendre `_generate_elements(session_id)` dans `D:\Projets\dev\AI-Kaeyris\app\jobs\jdr.py` selon le même pattern que T045 : fork sur `transcription_mode`, branch `non_diarised` lit `chunks.summary_text`, prompt système `ELEMENTS_SYSTEM_PROMPT` inchangé, parsing JSON identique.
- [ ] T047 [US3] Étendre `_generate_povs(session_id)` dans `D:\Projets\dev\AI-Kaeyris\app\jobs\jdr.py` : fork sur `transcription_mode`. En `non_diarised` : (a) charger `chunks.summary_text`, (b) charger la liste de PJ via `SessionPlayerRepository.list_for_session` (pas `MappingRepository`), (c) pour chaque PJ, appel LLM avec user prompt contenant le résumé chunked + le nom du PJ scoppé + l'indication "tu n'as pas de speaker labels, déduis qui agit depuis le contexte". `POV_SYSTEM_PROMPT` réutilisé tel quel. (d) UPSERT row `artifacts(kind=f"pov:{pj_id}")` par PJ.
- [ ] T048 [US3] Modifier les routes `POST /sessions/{id}/artifacts/{narrative|elements|povs}` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` : ajouter un pré-check si `session.transcription_mode == NON_DIARISED` que tous les `chunks.summary_text` sont non-NULL (sinon raise `NoSummaryError(AppError)`, 409 `"no-summary"`, detail pointe vers `POST /artifacts/summary`). Conserver le check `no-mapping` existant uniquement pour le branch `diarised`.
- [ ] T049 [US3] Ajouter `NoSummaryError(AppError)` (status 409, error_type `"no-summary"`) dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`.
- [ ] T050 [US3] Ajouter `ImmutableFieldError(AppError)` dans `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py` (status 422, error_type `"immutable-field"`) si pas déjà créé en T020.

**Checkpoint** : toutes les user stories du sous-jalon 5.5 sont livrées. Un MJ peut produire un résumé global ET les artefacts dérivés (narrative, elements, povs) sur une session `non_diarised` — sans modifier le comportement Jalon 5 sur les sessions `diarised` (FR-014).

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose** : améliorations transverses, documentation, Definition of Done sous-jalon 5.5.

- [ ] T051 [P] Rédiger `D:\Projets\dev\AI-Kaeyris\docs\adr\0007-non-diarised-mode.md` : décisions structurantes (mode `non_diarised` comme posture additive sur le pipeline Jalon 5, persistance inline `chunks.summary_text`, séquence atomique de la cascade FR-011, choix de réutiliser les system prompts existants). Format identique aux ADR 0001-0006.
- [ ] T052 [P] Mettre à jour `D:\Projets\dev\AI-Kaeyris\README.md` : section "Mode `non_diarised` (sous-jalon 5.5)" qui pointe vers `quickstart.md` 002, mentionne les nouvelles routes (chunks, players, summary) et la variable `KAEYRIS_CHUNK_MAX_CHARS`.
- [ ] T053 [P] Mettre à jour `D:\Projets\dev\AI-Kaeyris\docs\services\jdr.md` : ajouter une section "Mode non_diarised" avec le pipeline forké (transcription → chunks → summary map-reduce → artefacts dérivés), exemple curl `POST /sessions { transcription_mode: "non_diarised" }`, le tableau des erreurs cross-mode.
- [ ] T054 [P] Mettre à jour `D:\Projets\dev\AI-Kaeyris\docs\memo.md` : ajouter la ligne pour l'env var `KAEYRIS_CHUNK_MAX_CHARS` (default 30000) dans la section variables, et `alembic upgrade head` (couvre la migration 0002).
- [ ] T055 [P] Ajouter une entrée dans `D:\Projets\dev\AI-Kaeyris\docs\journal.md` pour le sous-jalon 5.5 : ce qui a été appris (pattern map-reduce LLM, atomicité de la cascade reset+delete via 2 transactions, choix du chunking par caractères + frontières naturelles, réutilisation des system prompts).
- [ ] T056 [P] Mettre à jour `D:\Projets\dev\AI-Kaeyris\specs\002-non-diarised-mode\checklists\requirements.md` pour refléter l'état post-implémentation : statuts confirmés, bullet final "Spec livrée, validation manuelle quickstart.md en attente".
- [ ] T057 [P] Exécuter `ruff check D:\Projets\dev\AI-Kaeyris` et corriger les warnings introduits par les nouveaux fichiers.
- [ ] T058 Exécuter `pytest D:\Projets\dev\AI-Kaeyris` et viser 100% vert. La suite Jalon 5 (248 tests) doit rester verte sans modification (FR-014).
- [ ] T059 Validation manuelle : suivre `quickstart.md §1` à `§10` de bout en bout avec une vraie clé DeepInfra et un M4A réel. Vérifier que `/docs` (OpenAPI) liste les nouveaux endpoints (`/chunks`, `/players`, `/artifacts/summary[.md]`) et que `transcription_mode` apparaît dans le schéma `SessionCreate` / `SessionOut`.
- [ ] T060 Commit final sous format Conventional Commits : `feat(jdr): non_diarised mode with map-reduce summary pipeline (sub-jalon 5.5)`. Ne pas amender les commits intermédiaires des US.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)** : démarre immédiatement. T001 et T002 séquentiels (T002 dépend de T001 pour la cohérence env var ↔ migration).
- **Phase 2 (Foundational)** : démarre après Phase 1. **Bloque toutes les US**.
- **Phase 3-5 (User Stories)** : démarrent après Phase 2.
  - **US1 (P1) MVP** : doit être livrée en premier — sans la transcription chunked en DB, aucune fonctionnalité en aval n'a de matière première.
  - **US2 (P2)** : démarre après US1 (utilise les chunks produits par le pipeline US1).
  - **US3 (P3)** : démarre après US2 (utilise les `chunks.summary_text` produits par le job summary).
- **Phase 6 (Polish)** : démarre après les 3 US (au minimum US1 + US2 pour ouvrir une validation manuelle partielle).

### Within Each User Story

- Tests **avant** implémentation (CLAUDE.md §2.5 : "non-trivial logic, prefer test-first").
- Modèles / schemas → repositories → logic → routes / jobs → markdown.
- Une story ne franchit pas le checkpoint tant que ses tests ne sont pas verts en isolation.

### Parallel Opportunities

- **Phase 1** : T001 et T002 séquentiels (env var d'abord, migration ensuite).
- **Phase 2** : T003, T004, T005 modifient `models.py` (séquentiels). T006, T007, T008 modifient `repositories.py` (séquentiels). T009 (prompts.py), T010 (schemas.py), T011 (text_chunker.py), T012 (test_text_chunker.py), T013 (conftest.py) sur fichiers distincts : **[P]**.
- **Phase 3** : T014, T015, T016, T017 (tests sur fichiers distincts) : **[P]**. T018-T025 modifient `router.py` ou `logic.py` ou `jobs/jdr.py` séquentiellement sur les zones concernées — pas tous [P] entre eux car même fichier.
- **Phase 4** : T026, T027, T028 (tests sur fichiers distincts) : **[P]**. T029-T036 modifient `prompts.py`, `jobs/jdr.py`, `router.py`, `markdown.py` — partiellement parallèles selon le fichier.
- **Phase 5** : T037, T038, T039, T040 (tests sur fichiers distincts) : **[P]**. T041-T050 séquentiels (logique + router + jobs entrelacés).
- **Phase 6** : T051-T056 (docs sur fichiers distincts) : **[P]**. T057 ruff [P] avec les docs. T058/T059/T060 séquentiels à la fin.

---

## Parallel Example: User Story 1

```bash
# Étape 1 — lancer en parallèle les 4 tâches de tests (fichiers distincts) :
Task: "tests/services/jdr/test_sessions_with_mode.py"          # T014
Task: "tests/services/jdr/test_chunks_endpoint.py"             # T015
Task: "tests/services/jdr/test_transcription_flow_non_diarised.py"  # T016
Task: "tests/services/jdr/test_mode_isolation.py (partie US1)" # T017

# Étape 2 — implémentation séquentielle (router.py et logic.py partagés, jobs/jdr.py modifié à plusieurs endroits) :
T018 (logic.create_session) → T019 (router POST /sessions)
                            → T020 (router PATCH /sessions)
                            → T021 (router WrongModeError)
                            → T022 (jobs._transcribe_session fork)
                            → T023 (logic.list_session_chunks)
                            → T024 (router GET /chunks)
                            → T025 (router refus 409 /transcription)
```

---

## Implementation Strategy

### MVP-first (US1 uniquement)

1. Phase 1 (Setup) — ~½ j (env var + migration aller-retour).
2. Phase 2 (Foundational) — ~½-1 j (ORM + schemas + chunker + prompts vides).
3. Phase 3 (US1) — ~1 j.
4. **STOP & VALIDATE** : suivre `quickstart.md §1-3`. Vérifier qu'on peut créer une session `non_diarised`, uploader un audio, et inspecter les chunks. Démo possible. Aucune fonctionnalité LLM activée pour l'instant — le résumé attend US2.

À ce stade, on a la fondation : transcription chunked stockée, prête à être consommée.

### Incremental delivery

5. Ajouter US2 (Phase 4) — résumé global. ~1 j (pipeline map-reduce + atomicity de la cascade). Pic de complexité.
6. Ajouter US3 (Phase 5) — narrative/elements/povs en mode non_diarised + /players. ~½-1 j.
7. Phase 6 (Polish + DoD) — ~½ j (ADR, README, docs, validation manuelle).

**Estimation totale** : 3-4 jours de travail focalisé.

### Critère de bascule mode `non_diarised` ↔ `diarised`

À tester explicitement (FR-014, SC-003) : créer en parallèle deux sessions, une de chaque mode, et faire passer la suite `pytest` complète (Jalon 5 + sous-jalon 5.5). Aucun test du Jalon 5 ne doit nécessiter de modification.

---

## Notes

- `[P]` = fichiers différents, pas de dépendance bloquante.
- `[Story]` = label de traçabilité vers l'US correspondante du spec.
- Chaque US est livrable et testable indépendamment dès que sa phase passe.
- Vérifier que les tests **échouent** avant d'écrire le code (test-first).
- Commit après chaque tâche ou groupe logique de tâches (pas de mega-commit). Format Conventional Commits, en français pour le sujet, anglais pour les types (`feat:`, `test:`, `refactor:`).
- S'arrêter à n'importe quel checkpoint pour valider la story en isolation.
- Anti-pattern à éviter : modifier un fichier du Jalon 5 sans nécessité absolue. La feature est explicitement **additive** (FR-014).
- T059 (validation manuelle E2E avec vraie clé DeepInfra) est le seul item non automatisable. Comme T076 du Jalon 5, il reste à la charge du MJ avant de fermer formellement le sous-jalon.
