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

## Créer un nouveau service métier

Le pattern (ADR 0002) : 3 fichiers par service, jamais d'imports croisés entre services, `logic.py` ne touche pas FastAPI.

```powershell
Copy-Item -Recurse app\services\_template app\services\<mon_service>
```

Puis dans le nouveau dossier :

1. **`schemas.py`** — modèles Pydantic des inputs/outputs (renommer `EchoRequest`/`EchoResponse`).
2. **`logic.py`** — logique métier pure, signature `func(input_schema) -> output_schema`. Aucun import FastAPI.
3. **`router.py`** — adapter le `prefix="/services/<mon_service>"` et `tags=["<mon_service>"]`.
4. **`app/main.py`** — ajouter `app.include_router(<mon_service>.router)` (à la différence de `_template` qui n'est pas monté).
5. **`tests/services/<mon_service>/`** — créer `__init__.py`, `conftest.py` (fixture mini-app), `test_router.py`.
6. **Erreurs métier** — créer une sous-classe de `AppError` dans `app/core/errors.py` (ou un module dédié si la liste grossit).

**Format d'erreur** : RFC 9457 Problem Details (https://www.rfc-editor.org/rfc/rfc9457.html). Géré par `register_exception_handlers()`. Les handlers couvrent `AppError` (custom), `RequestValidationError` (Pydantic 422), et `Exception` (catch-all 500).

**Auth obligatoire** : monter le router avec `app.include_router(router, dependencies=[Depends(require_api_key)])` pour exiger un Bearer token. Cf. ADR 0003.

---

## Authentification (Jalon 2 — ADR 0003)

```powershell
# 1. Générer une clé API (et son hash Argon2id)
python scripts/generate_api_key.py <name>

# 2. Copier la ligne API_KEYS=name:hash dans .env (séparateur ';' entre entrées)

# 3. Tester un endpoint protégé
curl -H "Authorization: Bearer <plain_key>" http://localhost:8000/<route>
```

| Élément | Valeur |
|---|---|
| Header | `Authorization: Bearer <key>` (RFC 6750) |
| Hash | Argon2id (`argon2-cffi`) |
| Stockage | env var `API_KEYS=name1:hash1;name2:hash2` (séparateur `;`) |
| Routes publiques | `/health`, `/docs`, `/redoc`, `/openapi.json` |
| Refus | 401 Problem Details + header `WWW-Authenticate: Bearer realm="ai-kaeyris"` |
| Rate limiting | reporté au Jalon 3 (Redis) |

---

## Async jobs et rate limiting (Jalon 3 — ADR 0004)

### Stack
| Élément | Valeur |
|---|---|
| Lib queue | RQ — https://python-rq.org |
| Broker | Redis 7 (image `redis:7-alpine`) |
| URL | `redis://redis:6379/0` (Compose) ; `redis://localhost:6379/0` (dev local) |
| Queue | `default` (une seule pour ce jalon) |
| TTL résultats | 24h succès / 7j échecs |
| Retry policy | 3 essais, backoff `[10s, 30s, 90s]` (transient errors uniquement) |

### Écrire un nouveau job
```python
# app/jobs/<topic>.py
from app.jobs import TransientJobError, PermanentJobError

def my_job(arg1: int, arg2: str) -> dict:
    # Logique pure, args sérialisables (types primitifs ou dataclasses simples).
    # Lever TransientJobError pour les échecs réseau/timeout (retry).
    # Lever PermanentJobError pour les erreurs définitives (pas de retry).
    return {"result": ...}
```

### Enqueuer depuis un service
```python
from app.jobs import enqueue_job, get_default_queue
from app.jobs.my_topic import my_job

queue = get_default_queue(redis_client)
job = enqueue_job(queue, my_job, arg1, arg2)
return {"job_id": job.id}    # 202 Accepted
```

### Lancer Redis et un worker en local
```powershell
# Redis seul (hybride : Redis Docker, API venv)
docker run -d -p 6379:6379 --name kaeyris-redis redis:7-alpine

# Worker dans le venv (lit REDIS_URL depuis .env)
.venv\Scripts\Activate.ps1
rq worker default --url $env:REDIS_URL

# OU tout en Compose
docker compose up --build
```

### Inspecter les jobs
```powershell
# Stats globales
docker compose exec redis redis-cli  # puis : KEYS * / LLEN rq:queue:default
rq info --url redis://localhost:6379/0

# Jobs échoués (FailedJobRegistry)
rq info --url redis://localhost:6379/0 --queues default
```

### Rate limiting
Sliding window Redis, 60 req/min par API key. Activer sur un router :
```python
from app.core.rate_limit import enforce_rate_limit
app.include_router(svc.router, dependencies=[Depends(enforce_rate_limit)])
```
Réponse en cas de dépassement : 429 + `Retry-After: <seconds>` + body Problem Details.

---

## Workflow git

| Jalon courant | Stratégie | Pourquoi |
|---|---|---|
| 0 → 6 | trunk-based, push direct sur `main` | solo, pas de CI, itération rapide (DORA — https://dora.dev) |
| 7+ (CI activée) | branche `feature/...` + PR + merge | la CI valide avant le merge ; protéger `main` sur GitHub |

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
