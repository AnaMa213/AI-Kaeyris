# Quickstart — kaeyris-jdr (Jalon 5)

**Spec** : [`./spec.md`](./spec.md) · **Plan** : [`./plan.md`](./plan.md)

> Suit le pattern dev déjà documenté dans `docs/memo.md` (Redis dans Compose, API en venv).

---

## Pré-requis

| Outil | Version | Notes |
|---|---|---|
| Python | ≥ 3.12 | Cohérent avec `pyproject.toml`. |
| Docker + Compose | récent | Pour Redis et le worker. |
| `ffmpeg` (ffprobe) | quelconque récent | Calcul de la durée des M4A. Installé sur l'hôte qui fait tourner l'API. |
| Compte cloud LLM | DeepInfra / Groq / OpenAI | Sert au LLM (Jalon 4) ET à la transcription cloud par défaut. |
| **Optionnel** : hôte GPU LAN avec faster-whisper + pyannote | RTX 4090 | Pour basculer la transcription en local. |

---

## 1. Bootstrap dépendances

```powershell
# venv & installation
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
# Nouvelles deps Jalon 5 :
pip install "sqlalchemy>=2.0" alembic aiosqlite
```

> Les deux nouvelles dépendances `sqlalchemy` et `alembic` sont à ajouter à `pyproject.toml` (Tasks).

---

## 2. Configuration `.env`

À copier depuis `.env.example` et compléter :

```dotenv
# Auth (clé GM bootstrap, format `name:argon2_hash`)
API_KEYS=kenan:$argon2id$v=19$m=...

# Async (déjà présent depuis Jalon 3)
REDIS_URL=redis://localhost:6379/0

# LLM (Jalon 4)
LLM_PROVIDER=deepinfra
LLM_MODEL=meta-llama/Meta-Llama-3.1-8B-Instruct
LLM_API_KEY=<deepinfra_api_key>

# Transcription (Jalon 5 — nouveau)
TRANSCRIPTION_PROVIDER=cloud
TRANSCRIPTION_BASE_URL=https://api.deepinfra.com/v1/openai
TRANSCRIPTION_API_KEY=<même clé que LLM si DeepInfra>
TRANSCRIPTION_MODEL=openai/whisper-large-v3
TRANSCRIPTION_LANGUAGE_HINT=fr

# Stockage
KAEYRIS_DATA_DIR=./data           # racine pour audios/* et la DB SQLite
DATABASE_URL=sqlite+aiosqlite:///./data/kaeyris.sqlite3
```

---

## 3. Migrations DB

```powershell
# Initialiser Alembic (à faire une seule fois lors de l'implémentation)
alembic upgrade head
```

> En dev, la DB est un fichier SQLite (`data/kaeyris.sqlite3`). En prod (Pi 5, Jalon 8), `DATABASE_URL` pointera vers un Postgres.

---

## 4. Démarrage de la stack

```powershell
# Redis (containerisé, déjà câblé en Compose)
docker compose up -d redis

# Worker RQ (autre terminal)
$env:REDIS_URL="redis://localhost:6379/0"
rq worker default

# API (autre terminal)
uvicorn app.main:app --reload
```

OpenAPI : http://localhost:8000/docs

---

## 5. Scénario E2E batch (MJ)

```powershell
# 5.1 — Créer un PJ
$gmKey = "<clé_gm_en_clair>"
curl.exe -X POST http://localhost:8000/services/jdr/pjs `
  -H "Authorization: Bearer $gmKey" -H "Content-Type: application/json" `
  -d '{"name": "Galadriel"}'
# → { "id": "pj-uuid-1", ... }

# 5.2 — Créer une session
curl.exe -X POST http://localhost:8000/services/jdr/sessions `
  -H "Authorization: Bearer $gmKey" -H "Content-Type: application/json" `
  -d '{"title": "Donjon des morts-vivants — chapitre 4", "recorded_at": "2026-05-03T20:00:00Z"}'
# → { "id": "session-uuid", "state": "created", ... }

# 5.3 — Uploader le M4A
curl.exe -X POST http://localhost:8000/services/jdr/sessions/<session-uuid>/audio `
  -H "Authorization: Bearer $gmKey" `
  -F "file=@./tests/fixtures/demo-session.m4a"
# → 202 { "session_id": "...", "job_id": "rq:abc...", "audio": { ... } }

# 5.4 — Poller le statut
curl.exe http://localhost:8000/services/jdr/jobs/<job_id> `
  -H "Authorization: Bearer $gmKey"
# → { "status": "running", ... } puis { "status": "succeeded", ... }
# Note : audio source purgé, ./data/audios/<session_id>.m4a supprimé (FR-004).

# 5.5 — Lire la transcription
curl.exe http://localhost:8000/services/jdr/sessions/<session-uuid>/transcription `
  -H "Authorization: Bearer $gmKey"
# Markdown :
curl.exe http://localhost:8000/services/jdr/sessions/<session-uuid>/transcription.md `
  -H "Authorization: Bearer $gmKey"

# 5.6 — Définir le mapping locuteur ↔ PJ
curl.exe -X PUT http://localhost:8000/services/jdr/sessions/<session-uuid>/mapping `
  -H "Authorization: Bearer $gmKey" -H "Content-Type: application/json" `
  -d '{"mapping": {"speaker_1": "pj-uuid-1", "speaker_2": "pj-uuid-2"}}'

# 5.7 — Demander le résumé narratif
curl.exe -X POST http://localhost:8000/services/jdr/sessions/<session-uuid>/artifacts/narrative `
  -H "Authorization: Bearer $gmKey"
# → 202 { "job_id": "...", "kind": "narrative" }
# Poller, puis :
curl.exe http://localhost:8000/services/jdr/sessions/<session-uuid>/artifacts/narrative.md `
  -H "Authorization: Bearer $gmKey"

# 5.8 — Demander la fiche d'éléments
curl.exe -X POST http://localhost:8000/services/jdr/sessions/<session-uuid>/artifacts/elements `
  -H "Authorization: Bearer $gmKey"

# 5.9 — Demander les POV
curl.exe -X POST http://localhost:8000/services/jdr/sessions/<session-uuid>/artifacts/povs `
  -H "Authorization: Bearer $gmKey"
```

---

## 6. Scénario joueur

```powershell
# 6.1 — En tant que MJ, enrôler un joueur
curl.exe -X POST http://localhost:8000/services/jdr/players `
  -H "Authorization: Bearer $gmKey" -H "Content-Type: application/json" `
  -d '{"name": "joueur-aragorn", "pj_id": "pj-uuid-2"}'
# → { "token": "kjdr_3f9a1b...", ... }
# 🚨 Sauvegarder le token : il n'est plus affiché ensuite.

# 6.2 — En tant que joueur
$playerKey = "kjdr_3f9a1b..."
curl.exe http://localhost:8000/services/jdr/me `
  -H "Authorization: Bearer $playerKey"
# → { "name": "joueur-aragorn", "pj": { "id": "pj-uuid-2", "name": "Aragorn" } }

curl.exe http://localhost:8000/services/jdr/me/sessions `
  -H "Authorization: Bearer $playerKey"

curl.exe http://localhost:8000/services/jdr/me/sessions/<session-uuid>/pov.md `
  -H "Authorization: Bearer $playerKey"
```

---

## 7. (Optionnel) Bascule transcription locale (RTX 4090 LAN)

L'hôte GPU expose un endpoint OpenAI-compatible `/v1/audio/transcriptions` enrichi de la diarisation pyannote. Le repo `ai-kaeyris` n'embarque pas ce wrapper — voir `docs/runbook.md` (à créer Jalon 8) pour la procédure de démarrage. Une référence d'implémentation possible : `https://github.com/m-bain/whisperX` exposé via un mince serveur FastAPI maison.

Une fois l'hôte GPU démarré sur `gpu-host.lan:8001` :

```dotenv
# .env, basculer simplement :
TRANSCRIPTION_PROVIDER=local
TRANSCRIPTION_BASE_URL=http://gpu-host.lan:8001/v1
TRANSCRIPTION_API_KEY=any-placeholder    # le wrapper local accepte n'importe quoi
TRANSCRIPTION_MODEL=large-v3
```

Redémarrer le worker RQ. Aucune autre modification (FR-021 / SC-009).

---

## 8. Tests

```powershell
# Unitaires + intégration
pytest

# Lint
ruff check .

# Critères Definition of Done jalon (CLAUDE.md §7) :
# 1. ruff propre  ✓
# 2. pytest vert  ✓
# 3. docker compose up démarre la stack ✓
# 4. les routes répondent (curl ci-dessus) ✓
# 5. README à jour (mention `kaeyris-jdr`) — Tasks
# 6. journal.md entry — Tasks
# 7. ADR éventuel : 0006-jdr-service.md (à arbitrer en Tasks)
```

---

## 9. Cleanup

```powershell
docker compose down
Remove-Item .\data\kaeyris.sqlite3
Remove-Item .\data\audios\*
```
