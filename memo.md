# Memo — commandes & raisons

> Aide-mémoire technique : commandes essentielles + raisons des choix techno (1 ligne).
> Pour la méthodo générale : [`playbook.md`](./playbook.md). Pour la vision projet : [`CLAUDE.md`](./CLAUDE.md).

---

## Pourquoi cette stack (1 ligne par techno)

| Techno | Raison |
|---|---|
| **Python 3.12+** | type hints natifs matures, pattern matching, asyncio stable |
| **FastAPI** | OpenAPI auto, Pydantic-first, async natif (vs Flask : pas async ; vs Django : trop lourd pour API pure) |
| **Pydantic v2** | validation type-safe runtime (vs dataclasses : pas de validation ; vs marshmallow : plus lent) |
| **pydantic-settings** | conf 12-Factor, lit env + `.env` ; séparé de Pydantic core depuis v2 |
| **uvicorn[standard]** | serveur ASGI rapide ; `[standard]` ajoute httptools + uvloop pour les perfs |
| **ruff** | linter + formateur Rust 10-100× plus rapide ; remplace flake8+black+isort en 1 outil |
| **pytest** | assertions Python natives, fixtures puissantes (vs unittest : trop verbeux) |
| **httpx** | client HTTP async + sync, supporte ASGI in-memory pour tests (vs requests : pas async) |
| **pytest-asyncio** | exécute les `async def test_…` ; mode `auto` évite les décorateurs |
| **Docker + Compose** | reproductibilité dev/prod sans VM, isolation processus |
| **venv** | isolation Python locale, natif, léger (vs poetry/pipenv : couche supplémentaire) |
| **PostgreSQL** (cible) | SQL standard, transactions ACID, écosystème mature (vs MongoDB : pas pour relationnel) |
| **Redis + RQ** | queue async simple (vs Celery : trop d'abstractions pour ce scale) |
| **structlog** | logs JSON structurés, contexte propagé (12-Factor §XI) |
| **Caddy** (futur) | HTTPS automatique, conf simple (vs nginx : verbeux pour ce besoin) |

---

## Étapes de création d'un projet (ordre)

| # | Étape | Pourquoi |
|---|---|---|
| 1 | `git init` + `.gitignore` | tracker dès le 1er commit, ignorer secrets/artefacts |
| 2 | `pyproject.toml` (PEP 621) | manifeste unique deps + outillage avant tout code |
| 3 | Structure `app/{core,services,adapters}` + `tests/` | séparation des concerns dès le début (CLAUDE.md §4) |
| 4 | `app/core/config.py` (Pydantic Settings) | conf via env vars, jamais hardcodée (12-Factor §III) |
| 5 | `app/main.py` + endpoint `/health` | smoke test minimal, vérifie que l'app démarre |
| 6 | `tests/test_health.py` | DoD §7.1 : tout endpoint a 1 test minimum |
| 7 | `Dockerfile` (slim, non-root, couches optimisées) | image immutable, sécurisée, build rapide |
| 8 | `docker-compose.yml` | orchestration locale, parité dev/prod (12-Factor §X) |
| 9 | `README.md` (setup < 5 min) | onboarding développeur |
| 10 | `docs/journal.md` | trace d'apprentissage (pourquoi, pas quoi) |
| 11 | `docs/adr/0001-*.md` | décisions structurantes documentées |

---

## Commandes essentielles

### venv (dev local + tests)
```bash
python -m venv .venv
.venv\Scripts\activate              # Windows PowerShell
source .venv/bin/activate           # Linux/macOS
pip install -e ".[dev]"             # -e = editable (modifs visibles sans reinstall)
deactivate                          # sortir du venv
```

### Qualité de code
```bash
ruff check .                        # lint le projet
ruff check . --fix                  # corrige automatiquement ce qui peut l'être
ruff format .                       # formatte (équivalent black)
pytest                              # lance la suite
pytest -v                           # verbeux (1 ligne par test)
pytest tests/test_health.py         # un fichier précis
pytest -k "health"                  # tests dont le nom matche
pytest --cov=app                    # couverture (nécessite pytest-cov)
```

### Docker (intégration)
```bash
docker compose up --build           # build image + démarre (foreground, Ctrl+C pour stop)
docker compose up -d --build        # idem en arrière-plan
docker compose ps                   # liste les services tournants
docker compose logs -f api          # suit les logs du service api
docker compose exec api bash        # shell dans le conteneur (debug)
docker compose down                 # arrête + supprime les conteneurs
docker compose build --no-cache     # force rebuild complet (couches foireuses)
```

### Git (Conventional Commits)
```bash
git status
git diff                            # changements non stagés
git diff --staged                   # changements stagés
git log --oneline                   # historique compact
git add <fichier>                   # éviter `-A` (risque de commit secrets)
git commit -m "feat(scope): msg"    # types: feat|fix|docs|chore|test|refactor
git push
```

---

## Pourquoi chaque étape technique (1 ligne)

| Geste | Raison |
|---|---|
| `python -m venv .venv` | isole les deps du projet du Python système |
| `pip install -e ".[dev]"` | mode editable + extras `dev` ; modifier le code n'oblige pas à réinstaller |
| `ruff check .` | détecte erreurs/bad practices avant runtime, < 1s sur petit projet |
| `pytest` | exécute la suite, garantit le contrat de l'API |
| `docker compose up --build` | rebuild image et démarre la stack identique à la prod |
| `docker compose logs -f` | suivi temps réel pour debugger sans entrer dans le conteneur |
| `docker compose down` | nettoie réseau + conteneurs, libère le port |
| Conventional Commits | parse-able par CI, génère changelog auto, lisibilité historique |
| `.env` + Pydantic Settings | secrets hors du code, conf par environnement (12-Factor §III) |
| User non-root dans Dockerfile | si compromission, dégâts limités (OWASP) |
| `COPY pyproject.toml` AVANT `COPY app` | cache Docker préservé tant que les deps n'ont pas changé |
| `httpx + ASGITransport` en test | rapide, en mémoire, pas de port à gérer, pas de flakiness |
| `asyncio_mode = "auto"` (pytest) | dispense de mettre `@pytest.mark.asyncio` sur chaque test async |
| Bind-mount `./app:/app/app` (compose) | hot-reload sans rebuild quand on modifie le code |
| `--reload` côté compose, pas Dockerfile | l'image prod reste propre (parité dev/prod minimale) |
| `EXPOSE 8000` | documentaire (Compose `ports:` fait le vrai mapping) |
| `PYTHONUNBUFFERED=1` | logs immédiats vers stdout (12-Factor §XI) |
| `PYTHONDONTWRITEBYTECODE=1` | pas de `.pyc` dans l'image, plus propre |
| `PIP_NO_CACHE_DIR=1` | image plus petite |

---

## Réflexes par situation

| Situation | Réflexe |
|---|---|
| Ajouter une dépendance | l'ajouter dans `pyproject.toml`, **rebuild** Docker (`--build`) |
| Ajouter une variable d'env | `.env.example` + champ dans `app/core/config.py` |
| Tests qui passent en local mais cassent en CI | différence Python version / OS / deps non listées |
| `ruff` se plaint d'un import non utilisé | supprimer ou justifier avec `# noqa: F401` (rare) |
| Conteneur ne démarre pas | `docker compose logs api` puis `docker compose exec api bash` |
| Port 8000 déjà pris | `docker compose down` ou changer le mapping `ports:` |
| Cache Docker ne marche pas | vérifier l'ordre des `COPY` (deps avant code) |
| Push refusé sur main | branche protégée → passer par PR |

---

## Définition de fin (DoD) — projet courant

Voir CLAUDE.md §7 pour la liste complète. Vérification rapide :

```bash
ruff check . && pytest && docker compose up --build -d && curl http://localhost:8000/health && docker compose down
```
