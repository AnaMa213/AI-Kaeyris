# Implementation Plan: Mode `non_diarised` (pipeline alternatif sans diarisation)

**Branch**: `002-non-diarised-mode` | **Date**: 2026-05-18 | **Spec**: [`spec.md`](./spec.md)
**Input**: Feature specification from [`specs/002-non-diarised-mode/spec.md`](./spec.md)

## Summary

Ajouter un mode `non_diarised` optionnel à la création de session du service `kaeyris-jdr` (Jalon 5). Ce mode forke le pipeline existant **sans le modifier** : la transcription est stockée en chunks de texte ordonnés au lieu de segments diarisés ; un nouvel endpoint `POST /sessions/{id}/artifacts/summary` exécute un map-reduce LLM (1 résumé par chunk persisté inline dans `chunks.summary_text`, puis 1 résumé global) ; les jobs existants `narrative`, `elements`, `povs` consomment ces résumés partiels via la colonne `summary_text` quand la session est en mode `non_diarised`, sans aucun changement de contrat HTTP côté client. Le mode `diarised` reste le défaut et conserve strictement le comportement Jalon 5.

**Approche technique** : extension chirurgicale du schéma existant — 1 colonne ajoutée à `jdr_sessions`, 2 nouvelles tables (`jdr_chunks`, `jdr_session_players`), 0 modification du code des routes/jobs existants côté diarised (forks internes uniquement dans les jobs LLM via `session.transcription_mode`).

## Technical Context

**Language/Version** : Python 3.12+
**Primary Dependencies** : FastAPI, Pydantic v2, SQLAlchemy 2.x async (`AsyncSession`), Alembic, `aiosqlite` (dev) / `asyncpg` (cible Jalon 8), RQ + redis, `argon2-cffi`, `openai` SDK (LLMAdapter — cf. ADR 0005), `structlog`
**Storage** : SQLite via `aiosqlite` en dev, PostgreSQL via `asyncpg` en cible. Une seule URL d'engine (`DATABASE_URL`). Préfixe `jdr_*` sur les nouvelles tables (cohérent ADR 0006 §1).
**Testing** : `pytest` + `pytest-asyncio` (`asyncio_mode=auto`) + `httpx.ASGITransport` (tests in-memory) + `fakeredis` pour la queue RQ + monkeypatching du `LLMAdapter` via `_StubLLM` (pattern Jalon 5)
**Target Platform** : Linux/Windows dev, Pi 5 cible (déploiement Jalon 8)
**Project Type** : web-service (monolithe modulaire FastAPI, cf. CLAUDE.md §4.1 et ADR 0001)
**Performance Goals** : SC-001 résumé global ≤ 5 min pour 60 000 caractères avec modèle cloud raisonnable ; SC-002 ≤ 60 s sur session tenant en 1 chunk ; SC-004 coût LLM `summary + narrative + elements + povs` ≤ 60 % du coût d'une exécution naïve sans réutilisation des `summary_text`
**Constraints** : FR-014 — la suite `pytest` du Jalon 5 reste verte sans modification, le scénario `quickstart.md` du Jalon 5 reste exécutable. FR-011 — atomicity stricte (transaction unique) sur le reset des `summary_text` + suppression cascade des artefacts dérivés à la régénération du `summary`. Pas de leak vendor dans le code métier.
**Scale/Scope** : 15 FRs, 3 user stories (P1/P2/P3), 2 nouvelles tables + 1 colonne, ~8 nouveaux endpoints REST (5 sur `summary` / `chunks` / `players` + extensions transparentes de 3 endpoints existants), ~9 nouveaux fichiers de tests, 1 migration Alembic, 0 modification du contrat HTTP du Jalon 5 sur les sessions `diarised`.

## Constitution Check

Source unique : [`CLAUDE.md`](../../CLAUDE.md) §2 (10 sous-principes) et §3 (stack lockée).

| Principe | Statut | Justification |
|---|---|---|
| §2.1 — Honesty over speed | ✅ PASS | Spec documente explicitement les limites (qualité POV sans diarisation, X = 30 000 chars à affiner par benchmarks empiriques). |
| §2.2 — Pedagogy over output volume | ✅ PASS | Cette feature aura sa propre entrée `docs/journal.md` post-livraison + ADR 0007 si une décision structurante émerge en cours d'implémentation. |
| §2.3 — YAGNI | ✅ PASS | Pas d'extension `/me/*` pour les joueurs au jalon courant (assumption explicite §spec). Pas de map-reduce sur mode diarised (hors scope). Pas de UI de visualisation des chunks. |
| §2.4 — Strict separation of concerns | ✅ PASS | 3 couches conservées : `router.py` ↔ `logic.py` ↔ `db/repositories.py`. Layered exceptions étendues (`InvalidPlayerListError` côté logic, `InvalidPlayerListAppError` côté route). Aucun import croisé entre services. Le `LLMAdapter` reste agnostique du provider. |
| §2.5 — Test discipline | ✅ PASS | TDD strict (tests rouge avant impl), ≥ 1 test par endpoint public, tests d'isolation cross-mode obligatoires (FR-014 = non-régression sur diarised). |
| §2.6 — Security by default | ✅ PASS | Pas de nouveau secret. Validation MJ ownership sur tous les `pj_id` du payload `/players` (`422 invalid-player-list`). Immutabilité de `transcription_mode` (interdite via `PATCH`). Isolation joueur inchangée — `/me/*` reste exclusivement diarised au jalon courant. |
| §2.7 — 12-Factor App compliance | ✅ PASS | Nouvelle env var `KAEYRIS_CHUNK_MAX_CHARS` (default 30 000), lue via `pydantic-settings` comme les autres. Logs structlog sur toutes les nouvelles routes/jobs. Stateless (DB est source de vérité). |
| §3 — Locked technology stack | ✅ PASS | Aucune nouvelle dépendance externe. Tout est fait avec SQLAlchemy 2.x async + Alembic + RQ + LLMAdapter existant. Pas de nouveau framework. |

**Verdict** : aucune violation. Section `## Complexity Tracking` reste vide.

## Project Structure

### Documentation (this feature)

```text
specs/002-non-diarised-mode/
├── plan.md                  # Ce fichier (/speckit-plan)
├── spec.md                  # /speckit-specify + /speckit-clarify (déjà livrés)
├── research.md              # Phase 0 — décisions techno + études de patterns
├── data-model.md            # Phase 1 — entités, schémas, invariants
├── contracts/
│   └── rest-api.md          # Phase 1 — contrat REST (nouveaux endpoints + extensions)
├── quickstart.md            # Phase 1 — scénario E2E mode non_diarised
├── checklists/
│   └── requirements.md      # Quality checklist (/speckit-specify, MAJ par /speckit-clarify)
└── tasks.md                 # Phase 2 — généré par /speckit-tasks (NOT livré ici)
```

### Source Code (repository root)

Extension du monolithe modulaire AI-Kaeyris (cf. CLAUDE.md §4.1 et ADR 0001). Tous les ajouts s'inscrivent dans la structure Jalon 5 existante — pas de nouveau service, pas de nouveau dossier de niveau supérieur.

```text
app/
├── core/                                # cross-cutting concerns — INCHANGÉ
│   └── config.py                        # +1 setting: KAEYRIS_CHUNK_MAX_CHARS
├── adapters/                            # external integrations — INCHANGÉ
│   ├── llm.py                           # LLMAdapter (réutilisé tel quel)
│   └── transcription.py                 # TranscriptionAdapter (réutilisé tel quel)
├── jobs/
│   └── jdr.py                           # ÉTENDU : _generate_summary + generate_summary_job ; forks internes dans _transcribe_session/_generate_narrative/_generate_elements/_generate_povs sur session.transcription_mode
└── services/jdr/
    ├── router.py                        # ÉTENDU : POST /sessions accepte transcription_mode ; nouveaux : POST/GET /sessions/{id}/players, GET /sessions/{id}/chunks, POST/GET /sessions/{id}/artifacts/summary[.md]
    ├── logic.py                         # ÉTENDU : create_session(..., transcription_mode), set_session_players, list_session_players, list_session_chunks ; logic d'invalidation cascade pour summary
    ├── schemas.py                       # ÉTENDU : SessionCreate(+transcription_mode), ChunkOut, SummaryArtifactOut, SessionPlayersIn, SessionPlayersOut
    ├── prompts.py                       # ÉTENDU : SUMMARY_MAP_SYSTEM_PROMPT, SUMMARY_REDUCE_SYSTEM_PROMPT ; variantes non_diarised pour NARRATIVE/ELEMENTS/POV (ou conditionnement interne au choix)
    ├── markdown.py                      # ÉTENDU : render_summary_md
    ├── audio.py                         # INCHANGÉ (chunking audio existe ; on ajoute un chunker TEXTE séparé)
    ├── text_chunker.py                  # NOUVEAU : découpe texte en chunks de N caractères au mieux sur des frontières naturelles
    └── db/
        ├── models.py                    # ÉTENDU : Session(+transcription_mode), Chunk (nouveau), SessionPlayer (nouveau)
        └── repositories.py              # ÉTENDU : ChunkRepository, SessionPlayerRepository ; SessionRepository.create accepte transcription_mode

migrations/versions/
└── 0002_non_diarised_mode.py            # NOUVEAU : ALTER jdr_sessions ADD transcription_mode + CREATE jdr_chunks + CREATE jdr_session_players

tests/services/jdr/
├── test_sessions_with_mode.py           # NOUVEAU : POST /sessions accepte transcription_mode, défaut, immuabilité, validation valeurs
├── test_players.py                      # NOUVEAU : POST/GET /sessions/{id}/players, validation ownership, isolation cross-mode
├── test_chunks_endpoint.py              # NOUVEAU : GET /sessions/{id}/chunks (non_diarised seulement)
├── test_summary.py                      # NOUVEAU : POST/GET /artifacts/summary, map-reduce, single-chunk shortcut, cascade invalidation
├── test_non_diarised_artefacts.py       # NOUVEAU : narrative/elements/povs sur non_diarised consomment chunks.summary_text
├── test_mode_isolation.py               # NOUVEAU : /mapping refusé sur non_diarised, /players refusé sur diarised, /chunks/summary refusés sur diarised, /transcription refusée sur non_diarised
└── (test_sessions.py, test_audio_upload.py, test_transcription_flow.py, test_narrative.py, test_elements.py, test_pjs.py, test_mapping.py, test_povs.py, test_player_*.py)
                                          # ↑ INCHANGÉS, reste verts (non-régression FR-014)

tests/services/jdr/test_text_chunker.py   # NOUVEAU : tests unitaires du chunker
tests/jobs/test_jdr_summary.py            # NOUVEAU : _generate_summary map-reduce avec mock LLM
```

**Structure Decision** : extension chirurgicale du monolithe modulaire existant. **Aucun fichier du Jalon 5 n'est dupliqué.** Les jobs existants reçoivent des forks internes au lieu d'avoir des doublons, ce qui garantit FR-014 (non-régression sur diarised) sans coût de maintenance double. Les nouveaux modules (`text_chunker.py`) restent isolés et testables unitairement sans DB.

## Complexity Tracking

> Vide — aucune violation Constitution Check détectée.
