# Implementation Plan: kaeyris-jdr — Assistant de session de jeu de rôle

**Branch**: `001-kaeyris-jdr` | **Date**: 2026-05-04 | **Spec**: [`spec.md`](./spec.md)
**Input**: Feature specification from `/specs/001-kaeyris-jdr/spec.md`

## Summary

Premier vrai service métier de la plateforme (Jalon 5 du roadmap CLAUDE.md). Le service `app/services/jdr/` ingère un audio M4A de session de jeu de rôle (2-3h, 4-5 locuteurs), produit en asynchrone une transcription diarisée, puis, sur demande du MJ, dérive trois familles d'artefacts : un résumé narratif, une fiche d'éléments structurés (PNJ/lieux/items/indices) et un résumé "point de vue" par PJ. Les joueurs accèdent en lecture, scoppés à leur PJ, via leur propre clé API. Le mode live est un **stub documenté** côté OpenAPI/WS pour figer le contrat futur (bot Discord, Jalon 6+).

L'approche technique tient en quatre piliers, posés par les jalons précédents :
- Auth Bearer + rôles (`gm` / `player`) en migrant les clés vers une table DB (extension de `app/core/auth.py`, ADR 0003).
- Jobs RQ (ADR 0004) : un job par transcription, un par artefact narratif/élements, un par batch de POV.
- `LLMAdapter` (ADR 0005) déjà en place pour les LLM.
- **Nouveau** : `TranscriptionAdapter` agnostique (cf. `contracts/transcription-adapter.md`). Posture **hybride** dès ce jalon, avec une seule implémentation OpenAI-compatible paramétrée par `base_url` qui adresse aussi bien un fournisseur cloud (DeepInfra/Groq/OpenAI) qu'un wrapper local sur un hôte GPU LAN (RTX 4090, faster-whisper + pyannote). Le Pi 5 reste orchestrateur (FR-022).

Sur le plan stockage, ce jalon **introduit l'ORM** (SQLAlchemy 2.x + Alembic) — autorisé par CLAUDE.md §3 à partir du Jalon 5. SQLite en dev, Postgres en prod (Jalon 8). L'audio source est purgé du disque dès la transcription réussie (FR-004).

## Technical Context

**Language/Version**: Python 3.12 (cohérent avec `pyproject.toml`)
**Primary Dependencies**:
- Existant : FastAPI, Pydantic v2, Uvicorn, `pydantic-settings`, `argon2-cffi`, `redis>=5.0`, `rq>=2.0`, `openai>=1.50`
- **Nouveau Jalon 5** : `sqlalchemy>=2.0`, `alembic`, `aiosqlite` (driver SQLite async)
- Outil système requis : `ffprobe` (pour récupérer la durée audio à l'upload)

**Storage**:
- DB business : SQLite (dev) / PostgreSQL (prod Pi 5 ; cible Jalon 8). ORM SQLAlchemy 2.x, migrations Alembic.
- Fichiers audio : système de fichiers, volume Docker (`./data/audios/<session_id>.m4a`), purgé après transcription.

**Testing**: pytest + pytest-asyncio + httpx (déjà en place) ; nouveau : tests d'intégration avec un fichier audio de fixture court (~30s) + adapter mock.

**Target Platform**: Linux server (Docker, x86_64 dev / arm64 Pi 5 prod). Hôte GPU LAN : Linux/Windows + NVIDIA driver — hors-scope du repo `ai-kaeyris`.

**Project Type**: Web service (modular monolith) — option 1 du template, structure unique.

**Performance Goals** :
- Upload accusé en ≤ 5 s (SC-004) — l'API ne fait que copier l'audio sur disque + enqueue.
- Transcription d'une session 2-3h ≤ 60 min en régime nominal (SC-005), avec timeout (FR-018).
- Génération d'un artefact narratif (résumé / fiche / POV) ≤ 5 min par PJ.
- Bascule provider de transcription en ≤ 5 min (SC-009).

**Constraints** :
- Pi 5 = orchestrateur uniquement, ne transcrit pas (FR-022).
- Aucun fichier audio brut n'est conservé une fois transcrit (FR-004).
- Aucun joueur ne doit pouvoir lire un POV qui n'est pas le sien (FR-014).
- Aucune référence à un vendor concret dans `app/services/jdr/` (CLAUDE.md §2.4).

**Scale/Scope** :
- 1 session/semaine, 2-3h d'audio, 4-5 locuteurs.
- Mono-MJ assumé au Jalon 5 (Assumption "Identité du MJ" du spec).
- ≈ 50 sessions/an cumulées en stockage (transcriptions + artefacts uniquement après purge audio) — empreinte de quelques Mo en JSON.

## Constitution Check

> Source : [`CLAUDE.md`](../../CLAUDE.md) (alias dans `.specify/memory/constitution.md`).

| Gate | Statut | Justification |
|---|---|---|
| §2.1 Honesty | ✅ | Sources citées dans `research.md` (URLs OpenAI, faster-whisper, pyannote, SQLAlchemy, RFC 9110/9457). |
| §2.2 Pedagogy | ✅ | Le plan explique *pourquoi* chaque décision (Decision/Rationale/Alternatives). |
| §2.3 YAGNI | ✅ | Pas de versioning d'artefacts (R9), pas de webhooks, pas de chunking côté client (R3), live = stub. |
| §2.4 Separation of concerns | ✅ | `app/adapters/transcription.py` (nouveau) ; `app/services/jdr/` ne nomme aucun fournisseur. Auth & rôles restent dans `app/core/`. |
| §2.5 Test discipline | ✅ | Pyramide respectée : tests unitaires sur logique JDR + adapter mock, tests d'intégration RQ avec fakeredis, un test E2E sur fixture audio courte. |
| §2.6 Security by default | ✅ | Auth Bearer obligatoire, hashes Argon2 (réutilise Jalon 2), purge audio (réduit la surface privacy), validation Pydantic systématique. **Risque privacy** documenté : le provider cloud fait sortir l'audio du LAN — choix assumé, le provider local existe pour le contre-balancer. |
| §2.7 12-Factor | ✅ | Config par env (cf. `quickstart.md` §2), stateless processes (Redis pour l'état des jobs, DB pour l'état métier), logs stdout, dev/prod parity (SQLite ↔ Postgres via le même ORM). |
| §3 Stack lockée | ✅ | Aucune dérogation : FastAPI, Pydantic v2, Redis+RQ, Postgres/SQLite, Docker Compose, ruff, pytest, structlog (Jalon 6+), DeepInfra par défaut. **Introduction d'ORM** : explicitement autorisée à partir du Jalon 5. |
| §4 Architecture | ✅ | Service unique `app/services/jdr/` avec sous-modules `batch/`, `live/`, `db/` et un `core/` interne pour les types partagés. Aucun cross-import entre services. |
| §6 Anti-patterns | ✅ | Pas d'over-engineering live, pas de CQRS, pas de skip de tests, pas de référence vendor dans le service, .env non commité. |
| §7 Definition of Done | À tenir | Cible à la fin de l'implémentation (cf. `quickstart.md` §8). |

**Verdict** : aucune violation. Pas de section `Complexity Tracking` à remplir.

## Project Structure

### Documentation (this feature)

```text
specs/001-kaeyris-jdr/
├── spec.md                # Specification (avec Clarifications session 2026-05-04)
├── plan.md                # Ce fichier (sortie /speckit-plan)
├── research.md            # Phase 0 — décisions techno (R1..R10)
├── data-model.md          # Phase 1 — entités, contraintes, transitions
├── contracts/
│   ├── rest-api.md        # Phase 1 — contrat REST + stub live
│   └── transcription-adapter.md  # Phase 1 — interface TranscriptionAdapter
├── quickstart.md          # Phase 1 — bootstrap dev + scénario E2E
├── checklists/
│   └── requirements.md    # Validation /speckit-specify
└── tasks.md               # Phase 2 — sortie /speckit-tasks (à créer)
```

### Source Code (repository root)

```text
app/
├── main.py                                # +mount du router jdr
├── core/
│   ├── auth.py                            # ↻ extension : rôles (gm/player), lecture DB-backed
│   └── db.py                              # NEW : engine + session SQLAlchemy + dépendance FastAPI
├── adapters/
│   ├── llm.py                             # déjà existant (Jalon 4)
│   └── transcription.py                   # NEW : TranscriptionAdapter + impl OpenAI-compat
├── jobs/
│   ├── __init__.py                        # déjà existant (machinerie RQ)
│   ├── llm.py                             # déjà existant
│   └── jdr.py                             # NEW : 4 jobs (transcription, narrative, elements, povs)
└── services/
    └── jdr/                               # NEW
        ├── __init__.py
        ├── router.py                      # routes communes (/sessions, /pjs, /players, /jobs, /me)
        ├── schemas.py                     # Pydantic v2 (cf. contracts/rest-api.md)
        ├── logic.py                       # règles métier (validation, autorisation, mappings)
        ├── prompts.py                     # prompts système narrative/elements/POV (centralisés ici)
        ├── markdown.py                    # rendu MD des artefacts
        ├── batch/
        │   └── router.py                  # POST /sessions/{id}/audio (mode batch)
        ├── live/
        │   └── router.py                  # POST /live/sessions (501) + WS stub
        └── db/
            ├── models.py                  # SQLAlchemy (api_keys, pjs, sessions, …)
            └── repositories.py            # encapsulation des requêtes DB par entité

migrations/                                # NEW : Alembic env
├── env.py
├── versions/
│   └── 0001_initial.py                    # schema initial Jalon 5

tests/
├── adapters/
│   ├── test_llm.py                        # déjà existant
│   └── test_transcription.py              # NEW
├── core/
│   └── test_auth.py                       # ↻ étendu pour les rôles
├── jobs/
│   └── test_jdr.py                        # NEW
└── services/
    └── jdr/                               # NEW
        ├── test_sessions.py
        ├── test_mapping.py
        ├── test_artifacts.py
        ├── test_player_access.py          # cas FR-014 (joueur ne voit pas POV des autres)
        └── fixtures/
            └── demo-session.m4a           # ~30s d'audio de démo, 2-3 locuteurs

docs/
├── adr/
│   └── 0006-jdr-service.md                # NEW (à arbitrer en Tasks)
├── journal.md                             # +entrée Jalon 5
└── memo.md                                # +ligne TranscriptionAdapter, +DB, +rôles
```

**Structure Decision** : option 1 du template (single project), conforme à l'architecture monolithe modulaire arrêtée par ADR 0001 et au layout déjà en place dans `app/`. Chaque sous-dossier est nouveau ou modifié de façon non-cassante. Aucun cross-import entre services (`app/services/jdr/` ne référence `app/services/_template/` que par convention de pattern, pas par import).

## Risks & Open Trade-offs

> Identifiés ici pour informer `tasks.md` ; pas de bloqueur.

1. **OpenAI Whisper API ne diarise pas** (cf. `contracts/transcription-adapter.md`). Conséquence : avec `TRANSCRIPTION_PROVIDER=cloud`, tous les segments arrivent avec `speaker_label="unknown"` — donc pas de résumés POV pertinents tant qu'on n'a pas le provider local opérationnel. À documenter dans le README du service pour qu'aucun utilisateur ne soit surpris. *Mitigation* : la production des POV reste possible mais cohérente avec ce que l'audio permet ; l'auto-suggestion du mapping est désactivée (FR-010a), c'est le MJ qui labelle.
2. **Découpage des fichiers > 25 Mo pour le cloud** (R3). À implémenter avec soin (concaténation + dé-shift des timestamps). Test unitaire dédié à prévoir.
3. **Migration des clés API** (R7) : risque de bricoler une erreur sur le bootstrap (env var → DB) et de casser l'auth en place. Mitigation : test d'intégration explicite "API_KEYS env var seule ⇒ une clé `gm` importée au premier démarrage".
4. **Coût LLM** : 4-5 POV générés à la suite peuvent vite atteindre des dizaines de milliers de tokens en input. Tasks à prévoir : configuration explicite de `LLM_MAX_TOKENS_DEFAULT` pour ce service, et mention dans `journal.md` du coût observé sur la première vraie session.
5. **Hôte GPU LAN hors-repo** : la procédure de stand-up du wrapper `faster-whisper` + pyannote est documentée mais pas livrée. Si l'utilisateur veut la posture full-local au Jalon 5, un README annexe est nécessaire — décidé en Tasks.

## Constitution re-check (post-design)

Après production de `research.md`, `data-model.md`, `contracts/`, `quickstart.md` :

- §2.4 Separation of concerns : ✅ confirmé. Le module `app/services/jdr/db/models.py` reste interne au service (pas exporté vers d'autres services). L'unique nouveauté qui touche `app/core/` est `auth.py` (extension légitime — l'auth est un concern transverse) et `db.py` (engine SQLAlchemy partagé, prêt pour de futurs services).
- §3 Stack lockée : ✅ confirmé. Aucune lib supplémentaire au-delà de SQLAlchemy/Alembic/aiosqlite (toutes dans le périmètre "PostgreSQL via ORM" autorisé Jalon 5).
- §6 Anti-patterns : ✅ confirmé. Le live mode est volontairement stub ; la fiche d'éléments suit la structure exigée par US 2 ; aucun raccourci sur les tests d'autorisation joueur.

Aucun nouveau gate à lever. **Plan prêt pour `/speckit-tasks`.**

## Phase 2 — Hand-off vers `/speckit-tasks`

> Ne pas implémenter ici. Liste indicative des grands blocs que `/speckit-tasks` aura à découper :

1. **DB & migrations** : ajout SQLAlchemy + Alembic + premier `0001_initial.py` qui crée toutes les tables de `data-model.md`. Engine async dans `app/core/db.py`. Dépendance FastAPI pour la session DB.
2. **Auth roles** : extension de `app/core/auth.py` pour lire la table `api_keys`, dépendance `require_role("gm" | "player")`, bootstrap depuis l'env var.
3. **Adapter transcription** : `app/adapters/transcription.py` (interface + impl OpenAI-compat + Mock), factory + cache, settings dans `config.py`.
4. **Service jdr — squelette** : copie du `_template`, mount dans `main.py`, schémas Pydantic, autorisation par rôle, repositories.
5. **Mode batch** : routes session+upload+mapping+artefact+jobs, jobs RQ correspondants (cf. `app/jobs/jdr.py`), purge audio, projection `jobs`.
6. **Endpoints joueur** : `/me`, `/me/sessions`, `/me/sessions/{id}/narrative`, `/me/sessions/{id}/pov` avec scoping strict par PJ.
7. **Markdown export** : `app/services/jdr/markdown.py` + endpoints `.md`.
8. **Live stub** : routes 501 + WebSocket fermé immédiatement, schéma documenté en commentaires + OpenAPI.
9. **Tests** : unitaires (logique, autorisation, markdown), intégration (fakeredis + DB SQLite en mémoire), un E2E avec fixture audio courte et `MockTranscriptionAdapter`.
10. **Docs** : `docs/adr/0006-jdr-service.md`, entrée `docs/journal.md`, mises à jour `README.md`, `docs/memo.md`.

Aucun de ces blocs ne franchit la frontière des décisions ; tous découlent du présent plan, du data model et des contrats.
