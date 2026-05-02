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

_(à remplir)_
