# Specification Quality Checklist: non-diarised-mode

**Purpose** : Validate specification completeness and quality before proceeding to clarification / planning
**Created** : 2026-05-18
**Feature** : [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain *(les 2 markers initiaux ont été résolus par la session `/speckit-clarify` du 2026-05-18, voir section `## Clarifications` du spec)*
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- Session `/speckit-clarify` du 2026-05-18 : 3 questions posées, 3 réponses intégrées :
  - FR-012 → nouvel endpoint `POST /sessions/{id}/players` (pj_ids list) ;
  - FR-013 → `narrative` disponible sur les deux modes ;
  - Q3 → résumés partiels persistés inline via `chunks.summary_text` (impacte FR-007, FR-009, FR-011 et l'entité `Chunk`).
- Spec rédigée en français conformément à CLAUDE.md §8 (préférence francophone).
- La valeur exacte de la taille de chunk (FR-004) reste une **Assumption** (default 30 000 caractères, configurable env var), à affiner par benchmarks empiriques après la première session réelle. Non bloquant pour le plan.

---

## Post-implementation status (2026-05-18)

Bilan de la livraison du sub-jalon 5.5 — implémentation complète sauf la validation E2E manuelle (T059).

### Functional requirements

- [x] FR-001 / FR-002 — `transcription_mode` à la création + immuabilité PATCH : `tests/services/jdr/test_sessions_with_mode.py`.
- [x] FR-003 — pipeline forké en transcription (chunks vs segments) : `tests/services/jdr/test_transcription_flow_non_diarised.py`.
- [x] FR-004 — taille chunk configurable via env var : `KAEYRIS_CHUNK_MAX_CHARS` + `tests/services/jdr/test_text_chunker.py`.
- [x] FR-005 — `GET /chunks` : `tests/services/jdr/test_chunks_endpoint.py`.
- [x] FR-006 / FR-007 — `POST /artifacts/summary` map-reduce + single-chunk shortcut : `tests/jobs/test_jdr_summary.py`, `tests/services/jdr/test_summary.py`.
- [x] FR-008 — `GET /artifacts/summary[.md]` : couvert par `test_summary.py`.
- [x] FR-009 — narrative/elements/povs consomment `chunks.summary_text` : `tests/services/jdr/test_non_diarised_artefacts.py`.
- [x] FR-010 — 409 no-summary si summary pas généré : `tests/services/jdr/test_mode_isolation.py` (3 tests narrative/elements/povs).
- [x] FR-011 — cascade atomique au regénération summary : `tests/services/jdr/test_summary_cascade.py`.
- [x] FR-012 — `POST /sessions/{id}/players` + validation ownership : `tests/services/jdr/test_players.py`.
- [x] FR-013 — `narrative` disponible sur les deux modes : `test_non_diarised_artefacts.py::test_narrative_non_diarised_consumes_chunk_summaries`.
- [x] FR-014 — non-régression mode `diarised` : 248 tests Jalon 5 verts sans modification.
- [x] FR-015 — error mapping TransientLLMError/PermanentLLMError → JobError : couvert par les tests `_generate_summary` et le pattern Jalon 5.

### Success criteria

- [x] SC-002 — single-chunk skip reduce : `test_jdr_summary.py::test_generate_summary_single_chunk_skips_reduce`.
- [x] SC-003 — non-régression : 248/248 tests Jalon 5 verts, ruff clean.
- [x] SC-007 — mode activable via un seul champ payload : `test_sessions_with_mode.py::test_post_session_explicit_non_diarised`.
- [ ] SC-001 / SC-004 / SC-005 / SC-006 — **validation manuelle E2E (T059)** à exécuter avec une vraie clé DeepInfra avant clôture formelle. Tester avec un audio M4A réel d'au moins 30 min.

### DoD CLAUDE.md §7

- [x] `ruff check .` clean
- [x] `pytest` vert (301/301)
- [x] `docker compose up` démarre sans crash (validé Jalon 0+)
- [x] OpenAPI complet à `/docs` (nouveaux endpoints `/chunks`, `/players`, `/artifacts/summary[.md]`)
- [x] README mis à jour (section "Mode `non_diarised` (sub-jalon 5.5)")
- [x] Entrée `docs/journal.md` pour le sub-jalon 5.5
- [x] ADR 0007 livré
- [x] Commits Conventional Commits (6 commits sur la branche `002-non-diarised-mode` : 1 chore scaffolding + 4 feat US + 1 docs polish)
- [ ] **Commit final** : couvre la phase Polish (en cours, T060)
