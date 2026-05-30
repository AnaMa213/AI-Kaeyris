# Memo — commandes & raisons

> Aide-mémoire technique : commandes essentielles + raisons des choix techno (1 ligne).
> Pour la méthodo générale : [`playbook.md`](./playbook.md). Pour la vision projet : [`CLAUDE.md`](../CLAUDE.md).

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
| **SQLAlchemy 2.x** (Jalon 5) | ORM standard Python, type-safe en v2.x, async natif via `AsyncSession` |
| **Alembic** | migrations standard de l'écosystème SQLAlchemy, fichiers Python versionnés |
| **aiosqlite** | driver SQLite async pour le dev ; PostgreSQL+asyncpg en cible Jalon 8 |
| **argon2-cffi** | hash mots de passe / tokens conforme OWASP (vs bcrypt/pbkdf2 : Argon2 gagnant PHC 2015) |
| **faster-whisper + pyannote** (futur GPU host) | transcription + diarisation locale sur RTX 4090 LAN, alternative au cloud OpenAI |
| **prometheus-client** (Jalon 6) | métriques applicatives, `/metrics` text exposition, naming `kaeyris_*` |
| **OpenTelemetry** (Jalon 6 — opt-in) | traces auto-instrumentation FastAPI/SQLAlchemy/httpx, exporter console ou OTLP |
| **GitHub Actions** (Jalon 7) | CI native du repo, gratuit pour OSS / repo perso, workflows YAML (vs Jenkins : auto-hébergé inutile à ce scale) |
| **bandit** (Jalon 7) | SAST Python natif, lib PyCQA, config TOML (vs semgrep : registry online + sur-dim pour mono-lang) |
| **pip-audit** (Jalon 7) | scan deps via OSV gratuit + officiel PyPA (vs safety : version free cap 50 packages ; vs snyk : commercial) |
| **gitleaks** (Jalon 7) | secrets scan stateless Go, action GH officielle (vs trufflehog : plus bruyant ; vs detect-secrets : baseline à maintenir) |
| **pre-commit** (Jalon 7) | orchestrateur de hooks, miroir local de la CI, install optionnel mais documenté |
| **PostgreSQL 16** (Jalon 8) | DB prod cible CLAUDE.md §3, ferme la dette dev/prod parity. SQLite reste en dev. |
| **asyncpg** (Jalon 8) | driver Postgres async — switch dev/prod via `DATABASE_URL` uniquement, pas de code change |
| **GHCR** (Jalon 8) | registry container OCI gratuit, intégré au repo GitHub (auth via `GITHUB_TOKEN`) |
| **Docker Buildx + QEMU** (Jalon 8) | build multi-arch amd64+arm64 sur runner GH, ~30s coût, $0, garde option Pi 5 |
| **Watchtower** (Jalon 8) | pull-based CD, label-enable pour scope, polling 5min, 0 port ouvert host |
| **Caddy v2** (Jalon 8) | reverse proxy HTTP LAN (HTTPS triviale à activer), `basic_auth` sur `/metrics` |
| **Prometheus** (Jalon 8) | TSDB pull-based, scrape `api:8000/metrics`, rétention 15j sur volume |
| **Grafana 11.3** (Jalon 8) | UI métriques, provisioning automatique (datasource + dashboards) depuis le repo |

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

### Persistance + migrations (Jalon 5+)
```bash
alembic upgrade head                # applique toutes les migrations
alembic downgrade -1                # rollback la dernière (test aller-retour)
alembic revision -m "add foo"       # nouvelle migration vide
alembic revision --autogenerate -m "add foo"   # diff vs ORM (à relire avant commit)
alembic current                     # version DB courante
alembic history                     # historique des migrations
```

### Mode `non_diarised` (sub-jalon 5.5) — variables et endpoints clés

| Var env | Default | Rôle |
|---|---|---|
| `KAEYRIS_CHUNK_MAX_CHARS` | `30000` | Taille max (caractères) d'un chunk de transcription pour les sessions `non_diarised` |

| Endpoint nouveau | Action |
|---|---|
| `POST /sessions` (champ `transcription_mode`) | `"diarised"` (défaut) ou `"non_diarised"` |
| `GET  /sessions/{id}/chunks` | Liste les chunks ordonnés (mode non_diarised) |
| `POST/GET /sessions/{id}/players` | Liste des PJ présents (équivalent /mapping sans speaker, non_diarised) |
| `POST /sessions/{id}/artifacts/summary` | Déclenche le map-reduce LLM (non_diarised) |
| `GET  /sessions/{id}/artifacts/summary[.md]` | Lit le résumé global (non_diarised) |

### Observabilité (Jalon 6) — variables et endpoints

| Var env | Default | Rôle |
|---|---|---|
| `LOG_FORMAT` | `console` | Format des logs : `console` (dev humain coloré) ou `json` (prod, 1 ligne par log) |
| `LOG_LEVEL` | `INFO` | Niveau global (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `OTEL_ENABLED` | `false` | Active OpenTelemetry tracing (opt-in) |
| `OTEL_EXPORTER` | `console` | `console` (stdout) ou `otlp` (HTTP → collector) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | URL du collector OTLP/HTTP (Tempo/Jaeger/OTEL Collector) |
| `OTEL_SERVICE_NAME` | `ai-kaeyris` | `service.name` attribut de ressource OTel |

| Endpoint | Action |
|---|---|
| `GET /healthz` | Liveness (200 toujours si process vivant) |
| `GET /readyz` | Readiness (200 si DB+Redis OK, 503 sinon avec detail) |
| `GET /metrics` | Prometheus text exposition, scrape direct |
| `GET /health` | Alias legacy de `/healthz` (compat Jalon 0) |

### CI / sécurité (Jalon 7)

```bash
# Tout-en-un local — miroir de la CI
ruff check app tests migrations
pytest -q
bandit -c pyproject.toml -r app --severity-level medium
pip-audit --desc                                      # non-bloquant en CI
# Secrets — nécessite le binaire gitleaks (scoop install gitleaks)
gitleaks detect --source . --config .gitleaks.toml --verbose

# Hooks pre-commit (optionnel mais recommandé)
pip install pre-commit
pre-commit install                                    # installe le hook .git/hooks/pre-commit
pre-commit run --all-files                            # exécute tous les hooks sur tout le repo
pre-commit autoupdate                                 # bump les revs des hooks
```

| Outil | Bloquant en CI ? | Scope |
|---|---|---|
| `ruff check` | ✅ | `app tests migrations` |
| `pytest` | ✅ | tout `tests/` |
| `bandit` | ✅ sur Medium+ | `app/` (tests/migrations exclus) |
| `pip-audit` | ❌ (`continue-on-error`) | toutes deps installées |
| `gitleaks` | ✅ | tout le repo + historique (CI) ou diff staged (pre-commit) |

### Déploiement prod (Jalon 8)

```bash
# Première installation (sur le PC fixe Windows)
docker run --rm caddy:2-alpine caddy hash-password --plaintext 'pwd-metrics'   # → CADDY_METRICS_HASH
cp .env.example .env                                                            # éditer ensuite
docker compose -f docker-compose.prod.yml up -d

# Suivi
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f api worker
docker compose -f docker-compose.prod.yml logs --tail=100 watchtower

# Déploiement d'une nouvelle version — automatique via Watchtower (~5 min après push main).
# Forcer immédiatement :
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d

# Rollback à une version précédente
docker images ghcr.io/anama213/ai-kaeyris                                        # liste les tags
# Set KAEYRIS_IMAGE=ghcr.io/anama213/ai-kaeyris:main-<sha> dans .env
docker compose -f docker-compose.prod.yml stop watchtower                       # éviter qu'il re-pull
docker compose -f docker-compose.prod.yml up -d api worker migrations

# Backup Postgres + audios
docker compose -f docker-compose.prod.yml exec postgres pg_dump -U kaeyris kaeyris > backup.sql
```

| Endpoint LAN | Service |
|---|---|
| `http://<host>/` | API (via Caddy reverse proxy) |
| `http://<host>/healthz` | Liveness |
| `http://<host>/readyz` | Readiness (DB + Redis) |
| `http://<host>/metrics` | Prometheus exposition (basic auth `metrics:<pwd>`) |
| `http://<host>:3000` | Grafana (login admin + `GRAFANA_ADMIN_PASSWORD`) |

Détails opérationnels (troubleshooting, rotation secrets, rollback migration) : [`docs/runbook.md`](./runbook.md).

### Jobs RQ (worker)
```bash
rq worker default --url redis://localhost:6379/0   # worker en avant-plan
rq info --url redis://localhost:6379/0             # état des queues + jobs
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

## Authentification web (feature 003)

| Endpoint | Rôle |
|---|---|
| `GET /services/jdr/auth/setup/status` | Indique si la base vide exige la création du premier GM |
| `POST /services/jdr/auth/setup` | Crée le premier GM (`username` + `password`) et pose `session` |
| `POST /services/jdr/auth/login` | Login web `username` + `profile` + `password`, pose un cookie HTTP-only |
| `POST /services/jdr/auth/logout` | Révoque la session courante et expire le cookie |
| `POST/GET/PATCH/DELETE /services/jdr/users` | Gestion GM des comptes applicatifs |

| Var env | Default | Rôle |
|---|---|---|
| `CORS_ALLOWED_ORIGINS` | vide | Liste explicite des origins front autorisées avec cookies |
| `SESSION_COOKIE_NAME` | `session` | Nom du cookie HTTP-only |
| `SESSION_COOKIE_SECURE` | `false` | `true` derrière HTTPS |
| `SESSION_COOKIE_SAMESITE` | `lax` | Protection CSRF de base côté navigateur |
| `WEB_SESSION_TTL_SECONDS` | `28800` | Durée de vie serveur d'une session web |

```powershell
curl http://localhost:8000/services/jdr/auth/setup/status
curl -X POST http://localhost:8000/services/jdr/auth/setup `
  -H "Content-Type: application/json" `
  -d '{"username":"admin","password":"mot-de-passe-choisi"}'
```

## Authentification API key (Jalon 2 — ADR 0003)

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

## LLM adapter (Jalon 4 — ADR 0005)

### Stack
| Élément | Valeur |
|---|---|
| Interface | `LLMAdapter` (typing.Protocol, PEP 544) |
| Méthode unique | `complete(*, system: str, user: str, max_tokens: int) -> str` |
| Implémentation cloud | `OpenAICompatibleLLMAdapter` (DeepInfra, OpenAI, Groq, vLLM, Together AI, Ollama) |
| Implémentation tests | `MockLLMAdapter` (déterministe, sans réseau) |
| SDK | `openai>=1.50` (couvre tous les providers compatibles OpenAI) |
| Erreurs | `LLMError` racine, `TransientLLMError` (retry), `PermanentLLMError` (no retry) |
| Premier job | `app/jobs/llm.py::llm_complete(system, user, max_tokens)` |

### Switcher de provider en 3 lignes (`.env`)

```
# Cloud DeepInfra (par défaut)
LLM_PROVIDER=deepinfra
LLM_MODEL=meta-llama/Meta-Llama-3.1-8B-Instruct
LLM_API_KEY=<ta_clé_deepinfra>

# OU local sur RTX 4090 + Ollama
LLM_PROVIDER=ollama
LLM_MODEL=llama3.1:8b-instruct-q4_K_M
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_API_KEY=ollama-noop          # placeholder, Ollama l'ignore
```

### Pattern d'utilisation depuis un service

```python
# app/services/<feature>/logic.py
SYSTEM_PROMPT = "Tu es un résumeur narratif..."  # style propre au service

from app.jobs.llm import llm_complete
from app.jobs import enqueue_job, get_default_queue

queue = get_default_queue(redis_client)
job = enqueue_job(
    queue, llm_complete,
    system=SYSTEM_PROMPT, user=transcript, max_tokens=1500,
)
```

### Pourquoi un seul `complete` (et pas `summarize`/`chat`/...)
Le **prompt système est de la logique métier**, pas de la responsabilité de l'adapter (CLAUDE.md §2.4). Chaque service met son prompt dans `app/services/<feature>/logic.py` et appelle `complete(system, user)`.

### Démarrer Ollama en local (RTX 4090)
```powershell
# Installer Ollama : https://ollama.com/download
ollama pull llama3.1:8b-instruct-q4_K_M
ollama serve                       # tourne sur localhost:11434
# Puis docker compose up : depuis le conteneur, host.docker.internal:11434 résout vers l'host
```

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
