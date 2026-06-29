---
description: "Task list — Epic 8 : Artefacts JDR éditables"
---

# Tasks: Epic 8 — Artefacts JDR éditables par le MJ + lectures joueur

**Input**: Design documents from `specs/018-artifact-editing/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/rest-api.md](contracts/rest-api.md)

**Tests**: INCLUS — obligatoires par CLAUDE.md §2.5 (tout endpoint public a ≥1 test ; migration testée).

**Traçabilité**: chaque story mappe une issue GitHub — US1=BD-23, US2=BD-26, US3=BD-24, US4=BD-25, US5=BD-27.

## Format: `[ID] [P?] [Story] Description`

- **[P]** = parallélisable (fichiers différents, pas de dépendance sur une tâche incomplète)
- Chemins de fichiers exacts inclus.

---

## Phase 1: Setup

**Purpose**: Préparer le terrain de test (la branche `018-artifact-editing` et le squelette de service existent déjà).

- [X] T001 [P] Fixtures « session avec artefacts générés » — réalisées en seeding inline dans `tests/services/jdr/test_artifact_edit.py` (`_seed_session_with_artifacts`), conformément à la convention des tests existants (test_narrative/test_povs n'utilisent pas de conftest partagé)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Baseline de schéma partagée par toutes les stories (table `jdr_artifacts`). ⚠️ Couplage assumé : la migration unique `0019` porte à la fois la provenance (US3) et la transformation des éléments (US2) — voir [plan.md](plan.md) « Structure Decision ». Aucune story ne démarre avant.

- [X] T002 Colonnes de provenance ajoutées au modèle `Artifact` (`is_edited`/`edited_at`/`edited_by`) dans `app/services/jdr/db/models.py`
- [~] T003 **Partiel** : provenance ajoutée aux 4 `*ArtifactOut` via `ArtifactProvenanceMixin` dans `app/services/jdr/schemas.py`. La refonte `Element → {category,...}` et `ElementsArtifactOut → elements: list[Element]` est **reportée à US2** (la faire ici sans le code de lecture US2 casserait `GET .../elements`).
- [~] T004 [P] **Partiel** : `TextEditIn{text}` ajouté (avec rejet du blanc). `ElementsPutIn` **reporté à US2** (couplé à la forme free-form).
- [X] T005 Migration `migrations/versions/0019_jdr_artifact_provenance.py` — **provenance DDL uniquement** (additif). Le data-migration flatten des éléments est **reporté à US2** (futur 0020) pour livrer avec le code de lecture. Upgrade/downgrade validés sur SQLite.
- [~] T006 [P] **Adapté** : migration 0019 validée par `alembic upgrade head → downgrade -1 → upgrade head` (round-trip OK). Pas de test unitaire alembic-in-pytest (harnais de test = `create_all`, migration additive). Le test d'intégrité d'éléments (SC-006) sera écrit avec le flatten en US2.

**Checkpoint**: provenance (schéma + migration) prête ✅ — le flatten des éléments démarrera avec US2.

---

## Phase 3: User Story 1 — Édition texte résumé/récit/POV (Priority: P1) 🎯 MVP — BD-23

**Goal**: Le MJ remplace le texte d'un résumé, récit ou POV par une écriture synchrone immédiate.

**Independent Test**: PATCH le texte d'un artefact existant → GET renvoie le texte exact ; non-MJ → 403 ; artefact absent → 404/422.

### Tests (écrits d'abord, doivent échouer)

- [X] T007 [P] [US1] Test d'édition texte dans `tests/services/jdr/test_artifact_edit.py` : PATCH summary/narrative/povs round-trip + provenance, immuabilité model_used/generated_at, 404 artefact absent, 422 texte vide, 404 cross-tenant (6 tests verts)

### Implémentation

- [X] T008 [US1] `ArtifactRepository.update_content(...)` ajouté (exige ligne existante → `None` sinon ; pose provenance ; ne touche pas `model_used`/`generated_at`) ; `upsert` réinitialise la provenance à la (re)génération — dans `app/services/jdr/db/repositories.py`
- [X] T009 [US1] `PATCH /sessions/{session_id}/artifacts/summary` (corps `TextEditIn`, `require_gm` + `resolve_session_for_gm` → 404 non-propriétaire, 404 si artefact absent → `SummaryArtifactOut`) dans `app/services/jdr/router.py`
- [X] T010 [US1] `PATCH /sessions/{session_id}/artifacts/narrative` (même garde de propriété) dans `app/services/jdr/router.py`
- [X] T011 [US1] `PATCH /sessions/{session_id}/artifacts/povs/{pj_id}` (résout le PJ possédé via `_load_owned_pj_or_404` ; édite `kind='pov:<pj_id>'`) dans `app/services/jdr/router.py`

**Checkpoint**: US1 fonctionnelle et testable seule — MVP livrable.

---

## Phase 4: User Story 2 — Éléments en catégories libres (Priority: P2) — BD-26

**Goal**: Le MJ remplace la carte d'éléments en liste plate taggée par catégorie libre ; la génération aplatit les 4 buckets.

**Independent Test**: GET elements renvoie une liste plate taggée ; PUT avec catégorie libre + description longue round-trip ; une régénération produit des éléments taggés.

### Tests (écrits d'abord, doivent échouer)

- [ ] T012 [P] [US2] Tests éléments free-form dans `tests/services/jdr/test_artifact_elements_freeform.py` : GET forme plate, PUT round-trip catégorie libre, description >25 mots acceptée, flatten 4 buckets → catégories

### Implémentation

- [ ] T013 [US2] Ajouter le helper `flatten_elements(buckets) -> list[Element]` (correspondance npcs→PNJ, locations→Lieux, items→Objets, clues→Indices) dans `app/services/jdr/logic.py`
- [ ] T014 [US2] Brancher `flatten_elements` dans le job/chemin de génération des éléments pour que `content_json` soit écrit au format `{"elements":[...]}` dans `app/services/jdr/logic.py`
- [ ] T015 [US2] Adapter `GET /sessions/{session_id}/artifacts/elements` (et `.md`) pour projeter `content_json["elements"]` vers le nouveau `ElementsArtifactOut` dans `app/services/jdr/router.py`
- [ ] T016 [US2] Implémenter `PUT /sessions/{session_id}/artifacts/elements` (corps `ElementsPutIn`, remplacement atomique via `update_content`, `require_gm` **+ résolution de propriété via `resolve_session_for_gm`**, 404/422 si absent) dans `app/services/jdr/router.py` (dépend de T008)

**Checkpoint**: US1 + US2 fonctionnent indépendamment.

---

## Phase 5: User Story 3 — Provenance + garde de régénération (Priority: P2) — BD-24

**Goal**: Les artefacts édités sont marqués `is_edited` et protégés contre l'écrasement par régénération (409 sauf `?force=true`), sans perdre les infos de génération.

**Independent Test**: après édition, l'artefact est `is_edited=true` avec `edited_at` ; POST régénération → 409 ; avec `?force=true` → procède et repasse `is_edited=false` ; artefact non édité → pas de 409.

### Tests (écrits d'abord, doivent échouer)

- [ ] T017 [P] [US3] Tests provenance + garde dans `tests/services/jdr/test_artifact_provenance.py` : is_edited/edited_at posés à l'édition, model_used/generated_at intacts, 409 sans force, succès avec force + reset provenance, cascade summary bloquée si artefact aval édité, non-destructif si job échoue

### Implémentation

- [ ] T018 [US3] Faire poser `is_edited=true`/`edited_at=now`/`edited_by=<gm>` par `update_content` (et reset `false`/`null` dans `ArtifactRepository.upsert`) dans `app/services/jdr/db/repositories.py` (dépend de T002, T008)
- [ ] T019 [US3] Ajouter l'erreur applicative `ArtifactEditedAppError` (409, `artifact-edited`) dans `app/services/jdr/router.py` (ou module d'erreurs du service)
- [ ] T020 [US3] Ajouter le paramètre `force: bool = False` + garde 409 (si artefact cible `is_edited` et `force` absent) sur `POST narrative`, `POST elements`, `POST povs` dans `app/services/jdr/router.py` (dépend de T019)
- [ ] T021 [US3] Étendre la garde au `POST summary` : 409 si un artefact aval (`narrative`/`elements`/`pov:*`) est `is_edited` et `force` absent ; `?force=true` lève la garde pour toute la cascade ; cascade-delete uniquement au succès du job (non-destructif, FR-009) dans `app/services/jdr/router.py` (dépend de T019)

**Checkpoint**: éditions protégées ; régénération normale inchangée sur artefacts non édités.

---

## Phase 6: User Story 4 — Textes longs sans troncature (Priority: P3) — BD-25

**Goal**: Un artefact texte ≥ 10 000 mots est enregistré et relu intégralement.

**Independent Test**: PATCH summary avec ~10 000 mots → GET renvoie la longueur intégrale.

- [ ] T022 [P] [US4] Test round-trip texte long dans `tests/services/jdr/test_artifact_text_length.py` : PATCH ~10 000 mots → GET sans troncature (dépend de T009)
- [ ] T023 [US4] Vérifier qu'aucun chemin d'édition n'introduit de cap (pas de `max_length` sur `TextEditIn.text`) dans `app/services/jdr/schemas.py` ; documenter dans le code que le stockage `content_json` est non borné

---

## Phase 7: User Story 5 — Lectures joueur résumé + éléments (Priority: P3) — BD-27

**Goal**: Un joueur lit en lecture seule le résumé et les éléments des sessions de son PJ ; refus sinon.

**Independent Test**: clé joueur dont le PJ a participé → GET summary/elements OK ; session non jouée → 403/404.

### Tests (écrits d'abord, doivent échouer)

- [ ] T024 [P] [US5] Tests lectures joueur dans `tests/services/jdr/test_player_artifact_reads.py` : GET /me summary(.md) + elements(.md) autorisés, isolation inter-sessions (403/404)

### Implémentation

- [ ] T025 [US5] Implémenter `GET /me/sessions/{session_id}/summary` et `.../summary.md` en miroir de `/me/.../narrative` (même autorisation PJ-lié, projection `SummaryArtifactOut`) dans `app/services/jdr/router.py`
- [ ] T026 [US5] Implémenter `GET /me/sessions/{session_id}/elements` et `.../elements.md` (projection `ElementsArtifactOut` post-BD-26) dans `app/services/jdr/router.py` (dépend de T015)

**Checkpoint**: toutes les stories indépendamment fonctionnelles.

---

## Phase 8: Polish & Cross-Cutting

- [ ] T027 Régénérer `docs/context/api/openapi.json` (rupture intentionnelle sur `ElementsArtifactOut`)
- [ ] T028 [P] Mettre à jour `README.md` (édition + lectures joueur) et ajouter une entrée `docs/journal.md` (epic 8, écarts ADR↔backend)
- [ ] T029 [P] Documenter le service éditable dans `docs/services/jdr.md` si présent (sinon ignorer)
- [ ] T030 Lancer la validation complète : `ruff check .`, `pytest`, `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`, puis le parcours `quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (P1)** : immédiat.
- **Foundational (P2)** : dépend de Setup — **BLOQUE toutes les stories** (schéma + migration partagés).
- **US1 (P3)** : après Foundational. Aucune dépendance inter-story.
- **US2 (P4)** : après Foundational. `T016` dépend de `update_content` (T008, US1).
- **US3 (P5)** : après US1 (modifie les chemins d'édition pour poser la provenance) et touche les POST de régénération.
- **US4 (P6)** : après US1 (réutilise le PATCH summary).
- **US5 (P7)** : après Foundational ; `T026` dépend de la projection éléments de US2 (T015).
- **Polish (P8)** : après les stories désirées.

### Within Each User Story

- Tests écrits d'abord et **rouges** avant implémentation.
- Modèle/schéma → repository → endpoints.

### Parallel Opportunities

- T003/T004 partagent `schemas.py` → **pas** parallèles entre eux ; T004 marqué [P] car isolable si fait avant T003.
- Tests de stories différentes (T007, T012, T017, T024) sont [P] entre eux.
- Les endpoints PATCH d'US1 (T009/T010/T011) touchent tous `router.py` → séquentiels.

---

## Implementation Strategy

### MVP (US1 seule)

1. Phase 1 Setup → 2. Phase 2 Foundational → 3. Phase 3 US1 → **STOP & VALIDATE** (édition texte fonctionnelle, livrable).

### Livraison incrémentale

US1 (MVP) → US2 (éléments) → US3 (protection) → US4 (textes longs) → US5 (lectures joueur). Fermer BD-23→27 via la PR à mesure (ou en une PR d'epic).

---

## Notes

- `[P]` = fichiers différents, pas de dépendance.
- Couplage Foundational assumé : migration unique `0019` (provenance + flatten) sert US2 et US3 ; les stories restent **comportementalement** indépendantes même si elles partagent la baseline de schéma.
- Commit par tâche ou groupe logique, message Conventional Commits référençant `BD-XX` (cf. convention epic-7).
