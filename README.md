# AI-Kaeyris

Personal AI sandbox platform — modular FastAPI services on Raspberry Pi.

Plateforme AI personnelle, monolithe modulaire en FastAPI, conçue pour héberger plusieurs services métier (résumé audio JDR, etc.) derrière une API REST sur le réseau local.

## Documentation interne

- [`CLAUDE.md`](./CLAUDE.md) — constitution du projet (principes, stack verrouillée, roadmap des jalons)
- [`playbook.md`](./playbook.md) — méthodo générale pour mener un projet logiciel pro (toutes phases)
- [`memo.md`](./memo.md) — aide-mémoire technique (commandes + raisons)
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

# 3. Lancer l'API en local
uvicorn app.main:app --reload

# OU via Docker Compose (intégration)
docker compose up --build
```

L'API écoute sur http://localhost:8000.

| Endpoint | Description |
|---|---|
| `GET /health` | Vérifie que l'API est en vie. Retourne `{"status":"ok","version":...}` |
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

## Créer un nouveau service

Voir la section "Créer un nouveau service" dans [`memo.md`](./memo.md). En résumé : copier `app/services/_template/`, adapter les schémas et le préfixe, monter le router dans `app/main.py` avec `dependencies=[Depends(require_api_key)]`, écrire les tests.
