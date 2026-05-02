# AI-Kaeyris

Personal AI sandbox platform — modular FastAPI services on Raspberry Pi.

Plateforme AI personnelle, monolithe modulaire en FastAPI, conçue pour héberger plusieurs services métier (résumé audio JDR, etc.) derrière une API REST sur le réseau local.

Conventions, principes et roadmap : voir [`CLAUDE.md`](./CLAUDE.md).

## Setup local

```bash
# 1. Copier le template d'environnement
cp .env.example .env

# 2. Créer un virtualenv et installer les dépendances (runtime + dev)
python -m venv .venv
source .venv/bin/activate            # Linux/macOS
# .venv\Scripts\activate             # Windows PowerShell
pip install -e ".[dev]"

# 3. Lancer l'API en local
uvicorn app.main:app --reload

# OU via Docker Compose
docker compose up --build
```

L'API écoute sur http://localhost:8000. Endpoint santé : `GET /health`.

## Tests

```bash
ruff check .
pytest
```
