# Journal d'apprentissage

## 2026-05-01 — Jalon 0 : Foundations

### Ce qui a été fait

- Création de l'arborescence cible définie en §4.1 du `CLAUDE.md` : `app/{core,services/_template,adapters}`, `tests/`, `docker/`, `docs/adr/`.
- `pyproject.toml` : Python 3.12+, dépendances runtime (`fastapi`, `uvicorn[standard]`, `pydantic-settings`) et dev (`pytest`, `pytest-asyncio`, `httpx`, `ruff`). Configuration `ruff` (line-length 100, target py312) et `pytest` (`testpaths = ["tests"]`, `asyncio_mode = "auto"`).
- `app/core/config.py` : `Settings` Pydantic minimal qui lit `.env` (12-Factor §III). Un seul réglage `APP_VERSION`.
- `app/main.py` : application FastAPI avec un unique endpoint `GET /health` retournant `{"status":"ok","version":<APP_VERSION>}`.
- `tests/test_health.py` : un test asynchrone qui valide statut 200 et JSON exact via `httpx.AsyncClient` + `ASGITransport`.
- `docker/Dockerfile` : `python:3.12-slim`, utilisateur non-root `app`, couches optimisées (deps avant code), `EXPOSE 8000`, `CMD uvicorn`.
- `docker-compose.yml` : un seul service `api` (YAGNI — pas de Postgres ni Redis avant Jalon 3), volume `./app:/app/app` pour le hot-reload, `--reload` ajouté côté compose pour préserver la parité dev/prod de l'image.
- `.env.example`, `.gitignore` (interdiction de commit `.env` — §2.6), `README.md` mis à jour avec setup local et tests.

### Ce que j'ai appris

- **Différence venv vs Docker dans un workflow pro** : le venv reste utile pour le dev local et l'exécution rapide des tests (boucle de feedback < 1s avec `pytest`), tandis que Docker garantit la parité avec la prod et l'intégration. Décision actée pour ce projet : combo des deux, venv pour l'itération, Docker pour les vérifications d'intégration.
- **Rôle exact de `ruff`** : ce n'est pas "juste un linter". C'est un outil unique écrit en Rust qui remplace `flake8 + black + isort + pyupgrade` (et une partie de `pylint`). Deux modes : `ruff check .` (lint) et `ruff format .` (formattage). Vitesse 10-100× supérieure aux outils Python historiques. Source : https://docs.astral.sh/ruff
- **Pourquoi `pip install -e ".[dev]"`** : le `-e` (editable) installe le projet en mode développement — toute modification du code est immédiatement visible sans réinstallation. Le `.[dev]` active l'extra `dev` défini dans `pyproject.toml` (`pytest`, `httpx`, `ruff`).
- **Ordre des couches dans un Dockerfile** : `COPY pyproject.toml` puis `RUN pip install` AVANT `COPY app` permet à Docker de garder la couche d'installation en cache tant que les dépendances n'ont pas changé. Inverser cet ordre déclenche un `pip install` à chaque modif de code source — minutes perdues à chaque build.
- **`ASGITransport` pour les tests httpx** : permet d'appeler l'app FastAPI directement en mémoire, sans démarrer de vrai serveur ni binder de port. Pas de flakiness, pas de cleanup, exécution quasi-instantanée. Pattern standard pour tester une app ASGI.
- **`env_file` dans Compose exige le fichier par défaut** : si `.env` est absent, `docker compose up` refuse de démarrer. Solution naturelle : `Copy-Item .env.example .env` à la première utilisation. Solution avancée disponible depuis Compose v2.24 : `env_file: [{ path: .env, required: false }]` pour rendre le fichier optionnel (utile en CI ou pour onboarding rapide).
- **Conventional Commits = contrat de communication** : le format `feat:`, `fix:`, `chore:`… n'est pas cosmétique. Il rend l'historique parsable par des outils (génération de changelog, détection de bumps semver), et discipline le découpage en commits atomiques. Standard documenté : https://www.conventionalcommits.org
- **Différence ADR vs journal vs memo** : l'ADR (`docs/adr/`) capture le **pourquoi** d'une décision structurante, immuable une fois acceptée (on en crée une nouvelle qui "supersede" plutôt que d'éditer). Le journal (`docs/journal.md`) trace l'apprentissage chronologique. Le memo/playbook (`memo.md`, `playbook.md`) condense la connaissance opérationnelle réutilisable. Les trois ne se substituent pas.
