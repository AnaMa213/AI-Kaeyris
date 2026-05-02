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

---

## 2026-05-02 — Jalon 1 : Modular API skeleton

### Ce qui a été fait

- **ADR 0002** rédigé puis accepté : trois décisions structurantes (structure de service en 3 fichiers `router/schemas/logic`, `_template` non monté en prod, RFC 9457 Problem Details fait main).
- **`app/core/errors.py`** : classe de base `AppError` + 3 exception handlers FastAPI (custom `AppError`, `RequestValidationError` Pydantic, catch-all `Exception`). Format de réponse RFC 9457 conforme avec `Content-Type: application/problem+json`.
- **`app/services/_template/`** matérialisé en 3 fichiers : `schemas.py` (Pydantic), `logic.py` (pure, aucune dépendance FastAPI), `router.py` (`POST /services/_template/echo`).
- **`app/main.py`** enrichi : métadonnées OpenAPI (title, version, description), tag `health` sur `/health`, appel à `register_exception_handlers(app)`. Le router `_template` n'est **pas** inclus.
- **5 nouveaux tests** : 3 sur le template (echo nominal, message manquant, message vide) via fixture `template_app` qui monte un mini-app dédié ; 2 sur les erreurs (`AppError` custom transformé en 418 Problem Details, `RuntimeError` non géré transformé en 500 générique sans leak du message).
- **`memo.md`** enrichi avec la section "Créer un nouveau service" (workflow `Copy-Item` + 6 étapes).
- **README.md** mis à jour avec mention de la doc OpenAPI auto et pointeurs vers les docs internes.

### Ce que j'ai appris

- **Différence routing / validation / métier** : avec FastAPI, le `router.py` ne fait que router et appeler `logic.py`. La validation est entièrement déléguée à Pydantic via `schemas.py`. La logique métier est testable sans démarrer FastAPI — c'est pour ça que `logic.py` ne doit jamais importer `fastapi`. Cette discipline coûte 0 ligne de plus mais rend les tests unitaires triviaux.
- **RFC 9457 Problem Details** : un standard IETF qui définit un format JSON unique pour toutes les erreurs HTTP (`type`, `title`, `status`, `detail`, `instance`). Content-Type `application/problem+json` au lieu de `application/json`. Adopté par Microsoft, Zalando, et de plus en plus d'APIs publiques. Coût d'implémentation maison : ~50 lignes.
- **Fixture pytest pour tester un router en isolation** : on crée un mini `FastAPI()` dans un `conftest.py`, on y monte uniquement le router à tester, on y attache les handlers via `register_exception_handlers()`. Permet de tester un service sans le polluer dans l'app principale ni avoir à démonter la prod. Pattern réutilisable pour tous les futurs services métier.
- **`raise_app_exceptions=False` sur `ASGITransport`** : par défaut httpx re-lève les exceptions non gérées dans les tests (utile pour debug). Pour tester qu'un handler catch-all transforme bien une `Exception` non prévue en réponse HTTP, il faut désactiver ce comportement, sinon la `RuntimeError` remonte avant d'atteindre Starlette.
- **Préfixe `_` sur `_template`** : convention Python signifiant "interne / privé / pas pour la prod". Renforce le message que ce n'est pas un service réel mais un modèle de copie. Cohérent avec le fait qu'il n'est pas monté.
- **Cache de couches Docker en pratique** : après `docker compose up --build`, l'ancienne image existe toujours sous le tag `<none>` (image "dangling"). Docker ne supprime jamais une image automatiquement par sécurité (rollback, conteneurs actifs). À nettoyer périodiquement avec `docker image prune`.

### Limitations acceptées (à reprendre dans des jalons futurs)

- Type URI Problem Details (`https://kaeyris.local/errors/...`) pointe vers un domaine non hébergé. À documenter ou remplacer par `about:blank` quand on aura une page d'erreurs.
- Pas de handler pour FastAPI `HTTPException` (raisé par exemple par `Depends`). YAGNI tant qu'on n'utilise pas ce pattern.
- Logging non configuré (Jalon 6 — structlog).
- Pas de correlation ID propagé dans les logs (Jalon 6).
- `openapi_tags` (descriptions des tags dans Swagger) non défini ; cosmétique.
