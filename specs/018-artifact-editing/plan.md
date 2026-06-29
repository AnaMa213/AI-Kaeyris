# Implementation Plan: Epic 8 — Artefacts JDR éditables par le MJ + lectures joueur

**Branch**: `018-artifact-editing` | **Date**: 2026-06-29 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/018-artifact-editing/spec.md` ; handoffs BD-23→BD-27 ; ADR frontend `architecture-artifact-editing-epic8.md`.

## Summary

Rendre les artefacts de session (résumé, récit, carte d'éléments, POV) éditables à la main par le MJ via des écritures synchrones, protéger ces éditions contre l'écrasement par régénération, migrer le modèle d'éléments vers des catégories libres, et ouvrir deux lectures joueur (résumé, éléments).

**Approche technique ancrée dans l'existant** (vérifiée dans le code, voir [research.md](research.md)) :
- Les artefacts sont une seule table `jdr_artifacts` (PK composite `(session_id, kind)`, contenu dans une colonne `content_json` de type `JSON`). Pas une colonne texte par type.
- **BD-25** (colonnes TEXT non bornées) : le texte vit dans `content_json["text"]`, déjà non borné en SQLite (TEXT) et Postgres (JSON). → **vérification + test, pas de migration DDL**.
- **BD-26** (éléments free-form) : transformation de la *forme JSON* `{npcs,locations,items,clues}` → `{elements:[{category,name,description}]}`. → **migration de données** (réécriture de `content_json` des lignes `kind='elements'`), pas un `ALTER COLUMN`.
- **BD-24** (provenance + garde) : **vraie migration Alembic** ajoutant `edited_at`, `is_edited`, `edited_by` à `jdr_artifacts` ; la garde de régénération doit couvrir aussi le **cascade-delete destructif** déclenché par `POST .../summary`.
- **BD-23 / BD-27** : nouveaux endpoints d'édition (PATCH/PUT) et de lecture joueur (GET `/me/...`), réutilisant les dépendances d'autorisation `require_gm` / projection joueur existantes.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy (async), Alembic, Redis + RQ (génération asynchrone existante, inchangée hormis garde)
**Storage**: PostgreSQL (cible) / SQLite (dev) ; table `jdr_artifacts`, contenu en colonne `JSON`
**Testing**: pytest + httpx (tests d'API), tests de migration (upgrade/downgrade + transformation de données)
**Target Platform**: Service web Linux (Raspberry Pi 5) sur LAN
**Project Type**: Modular monolith — service unique `app/services/jdr/`
**Performance Goals**: édition = écriture synchrone < 200 ms perçu (un UPDATE indexé sur PK) ; pas d'objectif de débit nouveau
**Constraints**: support textes ~10 000 mots sans troncature ; rétrocompat de contrat NON garantie pour les éléments (rupture assumée)
**Scale/Scope**: usage personnel ; volume d'artefacts faible (dizaines de sessions) ; migration de données sur un nombre limité de lignes `kind='elements'`

## Constitution Check

*Constitution = [`CLAUDE.md`](../../CLAUDE.md) (alias `.specify/memory/constitution.md`).*

| Principe | Vérification | Statut |
|---|---|---|
| §2.3 YAGNI | Pas d'endpoints CRUD par élément (PUT full-replace, Rule of Three respectée, voir DP-1). Pas de verrouillage optimiste (non requis). | ✅ |
| §2.4 Séparation des concerns | Tout dans `app/services/jdr/` ; aucun nom de vendor ; pas de modif de `app/core/`. | ✅ |
| §2.5 Discipline de tests | Chaque nouvel endpoint a ≥1 test ; migration testée upgrade/downgrade + intégrité données. | ✅ (cible) |
| §2.6 Sécurité | Édition MJ-only (`require_gm` + propriété campagne) ; lectures joueur scopées par PJ lié ; inputs validés Pydantic. OWASP API1/API3 (authz). | ✅ |
| §2.7 12-Factor | Pas de nouvelle config ; comportement inchangé hors périmètre. | ✅ |
| §3 Stack verrouillée | Aucun nouvel outil/lib introduit. | ✅ |
| §6 Anti-patterns | Périmètre = jalon courant (epic 8) ; pas de pattern fancy ; pas de modif core. | ✅ |

**Verdict** : pas de violation. Pas de section Complexity Tracking nécessaire.

## Project Structure

### Documentation (this feature)

```text
specs/018-artifact-editing/
├── plan.md              # Ce fichier
├── spec.md              # Spec validée
├── research.md          # Décisions techniques (Phase 0)
├── data-model.md        # Modèle de données + migrations (Phase 1)
├── contracts/
│   └── rest-api.md      # Contrats des nouveaux endpoints (Phase 1)
├── quickstart.md        # Procédure de validation manuelle (Phase 1)
├── checklists/
│   └── requirements.md  # Checklist qualité de la spec
└── tasks.md             # Phase 2 (/speckit-tasks — non créé ici)
```

### Source Code (repository root)

```text
app/services/jdr/
├── router.py            # + PATCH summary/narrative/povs/{pj}, PUT elements (BD-23)
│                        # + garde ?force sur POST summary/narrative/elements/povs (BD-24)
│                        # + GET /me/sessions/{id}/summary(.md), /elements(.md) (BD-27)
├── schemas.py           # Element{category,name,description} ; ElementsArtifactOut → liste plate
│                        # + edited_at/is_edited/edited_by sur les *ArtifactOut (BD-24)
│                        # + corps d'édition (TextEditIn, ElementsPutIn)
├── elements.py          # flatten 4 buckets → catégories (npcs→PNJ…), lecture legacy/new shape
├── logic.py             # règles métier JDR existantes
├── db/
│   ├── models.py        # Artifact: + edited_at, is_edited, edited_by
│   └── repositories.py  # ArtifactRepository: + update_content (édition), garde régénération
└── ...

migrations/versions/
├── 0019_jdr_artifact_provenance.py                         # provenance (DDL additif)
└── 0020_jdr_elements_freeform_category.py                  # éléments (migration de données JSON)

tests/services/jdr/
├── test_artifact_edit.py         # BD-23 (édition texte + elements, authz, artefact absent)
├── test_artifact_provenance.py   # BD-24 (is_edited/edited_at, garde 409/force, non-destructif)
├── test_artifact_elements_freeform.py  # BD-26 (modèle plat, flatten, migration)
├── test_artifact_text_length.py  # BD-25 (round-trip texte long)
└── test_player_artifact_reads.py # BD-27 (lectures joueur + isolation inter-sessions)
```

**Structure Decision** : service unique `app/services/jdr/` (modular monolith, §4.1). La baseline Epic 8 se fait en deux migrations séquentielles sur la même table : `0019` ajoute la provenance (DDL additif) et `0020` transforme les éléments (migration de données JSON). La séparation évite de transformer la forme stockée des éléments avant que le code de lecture/écriture US2 sache la projeter.

## Phase tracking

- **Phase 0** — [research.md](research.md) : décisions techniques, aucune NEEDS CLARIFICATION résiduelle.
- **Phase 1** — [data-model.md](data-model.md), [contracts/rest-api.md](contracts/rest-api.md), [quickstart.md](quickstart.md), mise à jour du marqueur SPECKIT dans `CLAUDE.md`.
- **Phase 2** — `/speckit-tasks` (non exécuté par cette commande) : génère `tasks.md` ordonné par dépendances, tracé BD-23→27.
