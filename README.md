# AI-Kaeyris

[![CI](https://github.com/AnaMa213/AI-Kaeyris/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/AnaMa213/AI-Kaeyris/actions/workflows/ci.yml)

Personal AI sandbox platform — modular FastAPI services on Raspberry Pi.

Plateforme AI personnelle, monolithe modulaire en FastAPI, conçue pour héberger plusieurs services métier (résumé audio JDR, etc.) derrière une API REST sur le réseau local.

## Documentation interne

- [`CLAUDE.md`](./CLAUDE.md) — constitution du projet (principes, stack verrouillée, roadmap des jalons)
- [`docs/playbook.md`](./docs/playbook.md) — méthodo générale pour mener un projet logiciel pro (toutes phases)
- [`docs/memo.md`](./docs/memo.md) — aide-mémoire technique (commandes + raisons)
- [`docs/Jalon1.md`](./docs/Jalon1.md) … [`docs/Jalon5.md`](./docs/Jalon5.md) — walkthroughs pédagogiques par jalon
- [`docs/services/jdr.md`](./docs/services/jdr.md) — premier service métier (Jalon 5) : architecture, opérations, hôte GPU LAN
- [`docs/adr/`](./docs/adr/) — Architecture Decision Records (décisions structurantes)
- [`docs/journal.md`](./docs/journal.md) — journal d'apprentissage par jalon

## Setup local

```powershell
# 1. Copier le template d'environnement
Copy-Item .env.example .env

# 2. Créer un virtualenv et installer les dépendances (runtime + dev)
python -m venv .venv
.venv\Scripts\activate              # Windows PowerShell
# source .venv/bin/activate         # Linux/macOS
pip install -e ".[dev]"

# 3. (Optionnel, recommandé) Installer les hooks pre-commit
pip install pre-commit
pre-commit install

# 4. Lancer l'API en local
uvicorn app.main:app --reload

# OU via Docker Compose (intégration)
docker compose up --build
```

L'API écoute sur http://localhost:8000.

| Endpoint | Description |
|---|---|
| `GET /healthz` | Liveness probe (Jalon 6) — 200 si le process tourne. |
| `GET /readyz` | Readiness probe (Jalon 6) — 200 si DB + Redis OK, sinon 503 + detail par check. |
| `GET /metrics` | Métriques Prometheus (text exposition, Jalon 6). |
| `GET /health` | Alias legacy de `/healthz` (compat Jalon 0). |
| `GET /docs` | Swagger UI interactif (généré automatiquement par FastAPI) |
| `GET /redoc` | ReDoc (alternative à Swagger, lecture seule) |
| `GET /openapi.json` | Spec OpenAPI 3 brute |

## Tests et qualité

```powershell
ruff check .                # lint (cf. CLAUDE.md §3)
ruff format .               # formattage
pytest                      # tests
pytest -v                   # tests verbeux
```

## Architecture

```
app/
├── main.py            # point d'entrée FastAPI, monte les routers
├── core/              # cross-cutting concerns (config, errors, auth, logging)
├── services/          # un dossier par feature métier — pas d'imports croisés
│   └── _template/     # modèle pour créer un nouveau service (NON monté en prod)
└── adapters/          # intégrations externes derrière des interfaces (LLM, etc.)
```

**Format d'erreur** : toutes les erreurs API suivent la [RFC 9457 Problem Details](https://www.rfc-editor.org/rfc/rfc9457.html) avec `Content-Type: application/problem+json`.

## Authentification

Toutes les routes hors `/health`, `/docs`, `/redoc`, `/openapi.json` exigent un header `Authorization: Bearer <api_key>` (cf. [ADR 0003](./docs/adr/0003-authentication-strategy.md)).

```powershell
# Générer une clé API
python scripts/generate_api_key.py mon-laptop

# Coller la ligne API_KEYS=... dans .env, redémarrer l'API, puis :
curl -H "Authorization: Bearer <plain_key>" http://localhost:8000/<route>
```

Les clés sont **stockées hashées** (Argon2id). Plusieurs entrées séparées par `;` :
```
API_KEYS=laptop:$argon2id$...;pi-monitor:$argon2id$...
```

## LLM adapter (vendor-neutral)

Voir [ADR 0005](./docs/adr/0005-llm-adapter-and-providers.md). Interface unique `LLMAdapter` (Protocol PEP 544) avec une méthode `complete(system, user, max_tokens)`. Une implémentation `OpenAICompatibleLLMAdapter` couvre tous les providers compatibles OpenAI : **DeepInfra, OpenAI, Groq, Ollama, vLLM, Together AI**.

```bash
# Cloud DeepInfra (défaut)
LLM_PROVIDER=deepinfra
LLM_API_KEY=<your_key>

# Local sur GPU avec Ollama
LLM_PROVIDER=ollama
LLM_MODEL=llama3.1:8b-instruct-q4_K_M
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_API_KEY=ollama-noop
```

Le code métier ne référence **jamais** un nom de fournisseur — il appelle `LLMAdapter.complete()`. Le prompt système (style narratif, formel, technique…) est défini par chaque service dans son propre module. Détails dans [`docs/memo.md`](./docs/memo.md) et [`docs/Jalon4.md`](./docs/Jalon4.md).

## Async jobs et rate limiting

Voir [ADR 0004](./docs/adr/0004-async-jobs-and-rate-limiting.md). Stack : **Redis 7 + RQ**, une queue `default`, retry exponentiel sur erreurs transient, TTL résultats 24h / échecs 7j.

```powershell
# Tout en Compose (3 services : redis, api, worker)
docker compose up --build

# OU dev hybride : Redis en Docker, API + worker en venv local
docker run -d -p 6379:6379 --name kaeyris-redis redis:7-alpine
uvicorn app.main:app --reload                # terminal 1
rq worker default --url $env:REDIS_URL       # terminal 2
```

Un nouveau job se crée dans `app/jobs/<topic>.py` puis s'enqueue via `enqueue_job(queue, func, *args)`. Détails dans [`docs/memo.md`](./docs/memo.md).

**Rate limiting** : 60 req/min par API key (configurable via `RATE_LIMIT_PER_MINUTE`). Activer sur un router avec `dependencies=[Depends(enforce_rate_limit)]`.

## Observabilité (Jalon 6)

Trois piliers + healthchecks. Détail dans [ADR 0008](./docs/adr/0008-observability.md).

| Pilier | Endpoint / Convention | Activation |
|---|---|---|
| **Logs JSON structurés** | stdout / stderr via `structlog` | `LOG_FORMAT=json` (prod) ou `console` (dev). `LOG_LEVEL=INFO\|DEBUG\|...`. |
| **Métriques Prometheus** | `GET /metrics` (text exposition) | Toujours actif. 9 séries `kaeyris_*` (HTTP, LLM, transcription, jobs). |
| **Traces OpenTelemetry** | Auto-instrumentation FastAPI/SQLAlchemy/httpx | `OTEL_ENABLED=true` (opt-in). `OTEL_EXPORTER=console\|otlp`, `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318`. |
| **Healthchecks** | `GET /healthz` (liveness), `GET /readyz` (readiness DB+Redis) | Toujours actifs. |

Corrélation : chaque requête HTTP reçoit un `X-Request-Id` (UUIDv4 minté ou trust du header entrant) qui est bound au context structlog. Tous les logs émis pendant la requête le portent automatiquement.

```bash
# Scraper Prometheus en local
curl http://localhost:8000/metrics | grep kaeyris_

# Activer OTEL avec export console (debug)
OTEL_ENABLED=true OTEL_EXPORTER=console uvicorn app.main:app --reload

# Activer OTEL contre un collector Tempo/Jaeger
OTEL_ENABLED=true OTEL_EXPORTER=otlp \
  OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4318 \
  uvicorn app.main:app
```

## Service `kaeyris-jdr` (Jalon 5)

Assistant de session de jeu de rôle — premier service métier. Détails complets dans [`docs/services/jdr.md`](./docs/services/jdr.md), spec dans [`specs/001-kaeyris-jdr/`](./specs/001-kaeyris-jdr/), décisions dans [ADR 0006](./docs/adr/0006-jdr-service.md).

```bash
# Préparer la DB puis lancer
alembic upgrade head
uvicorn app.main:app --reload
rq worker default --url redis://localhost:6379/0
```

Scénario E2E (résumé — la procédure complète est dans [`specs/001-kaeyris-jdr/quickstart.md`](./specs/001-kaeyris-jdr/quickstart.md)) :

1. MJ : `POST /services/jdr/sessions` puis `POST /sessions/{id}/audio` (M4A) → job de transcription.
2. MJ : `POST /pjs`, `PUT /sessions/{id}/mapping` pour relier `speaker_X` à chaque PJ.
3. MJ : `POST /sessions/{id}/artifacts/{narrative|elements|povs}` puis polling `GET /jobs/{id}`.
4. MJ : `POST /players` pour enrôler un joueur (token plaintext renvoyé **une seule fois**).
5. Joueur : `GET /me`, `GET /me/sessions`, `GET /me/sessions/{id}/{narrative|pov}[.md]` — strictement scoppé à son PJ (FR-014).

Variables d'environnement spécifiques (voir [`.env.example`](./.env.example) pour le détail) : `DATABASE_URL`, `KAEYRIS_DATA_DIR`, `TRANSCRIPTION_PROVIDER` (`cloud` par défaut, `local` pour l'hôte GPU LAN), `TRANSCRIPTION_BASE_URL`, `TRANSCRIPTION_API_KEY`, `TRANSCRIPTION_MODEL`, `TRANSCRIPTION_LANGUAGE_HINT`, `TRANSCRIPTION_CHUNK_DURATION_SECONDS`.

> **Mode live** : `POST /services/jdr/live/sessions` et `WS /services/jdr/live/stream` sont publiés dans l'OpenAPI mais retournent respectivement `501` et ferment immédiatement le WebSocket avec le code `1011` — l'implémentation arrive au Jalon 6+ (FR-015/016).

### Mode `non_diarised` (sub-jalon 5.5)

Posture alternative opt-in pour les sessions où la diarisation cloud ne donne rien d'exploitable (Whisper sans speaker labels). Tag posé à la création de session, immuable ensuite. Détails dans [ADR 0007](./docs/adr/0007-non-diarised-mode.md), spec dans [`specs/002-non-diarised-mode/`](./specs/002-non-diarised-mode/), procédure E2E dans [`specs/002-non-diarised-mode/quickstart.md`](./specs/002-non-diarised-mode/quickstart.md).

Flow MJ :

1. `POST /sessions` avec `{"transcription_mode": "non_diarised", ...}` → session créée en mode chunked
2. `POST /sessions/{id}/audio` → transcription écrite en chunks ordonnés dans `jdr_chunks` (au lieu de segments diarisés)
3. `GET /sessions/{id}/chunks` → inspecter le texte chunked
4. `POST /sessions/{id}/players` avec `{"pj_ids": [...]}` → déclarer les PJ présents (équivalent du mapping en mode diarised)
5. `POST /sessions/{id}/artifacts/summary` → map-reduce LLM : 1 résumé par chunk + 1 reduce global. Persisté dans `jdr_artifacts(kind="summary")`. Régénération = cascade delete des `narrative` / `elements` / `pov:*` existants (FR-011)
6. `POST /sessions/{id}/artifacts/{narrative|elements|povs}` → consomment les résumés de chunks au lieu des segments diarisés. Contrat HTTP côté client **inchangé** vs mode diarised

Variable d'environnement additionnelle : `KAEYRIS_CHUNK_MAX_CHARS` (default `30000`, taille max d'un chunk de transcription).

> **Limite assumée** : la qualité POV reste dégradée par construction tant que la diarisation n'est pas opérationnelle (Jalon 9) — le LLM doit deviner qui agit depuis le contexte narratif. Les endpoints `/me/*` joueur restent réservés aux sessions `diarised` au sub-jalon courant.

## Créer un nouveau service

Voir la section "Créer un nouveau service" dans [`docs/memo.md`](./docs/memo.md). En résumé : copier `app/services/_template/`, adapter les schémas et le préfixe, monter le router dans `app/main.py` avec `dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)]`, écrire les tests.
