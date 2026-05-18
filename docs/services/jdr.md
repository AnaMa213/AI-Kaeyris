# Service `kaeyris-jdr`

Assistant de session de jeu de rôle — premier service métier de la plateforme (Jalon 5).
Réfs : [`specs/001-kaeyris-jdr/spec.md`](../../specs/001-kaeyris-jdr/spec.md), [`docs/adr/0006-jdr-service.md`](../adr/0006-jdr-service.md).

---

## 1. Ce que fait le service

Pipeline batch en 4 étapes, chacune asynchrone via RQ :

```
[M4A upload] -> transcription diarisée -> 3 familles d'artefacts -> /me/* (lecture joueur)
```

1. Le MJ crée une `Session`, uploade un M4A.
2. Un worker RQ transcrit l'audio (Whisper cloud ou local), purge le fichier source, écrit la `Transcription` (segments + speaker labels).
3. Le MJ déclare ses PJ, mappe `speaker_X → PJ`, déclenche à la demande la génération de 3 artefacts via le `LLMAdapter` (DeepInfra par défaut) :
   - `narrative` — récit chronologique global.
   - `elements` — fiche structurée `{npcs, locations, items, clues}`.
   - `pov:<pj_id>` — un résumé par PJ mappé, scoppé à ce qu'il pouvait percevoir.
4. Chaque joueur enrôlé (`role='player'`, lié à un `pj_id`) consulte `narrative` et **son** `pov` via `/me/*` — jamais ceux des autres (FR-014).

Le mode live (WebSocket Discord) est un **stub publié sans implémentation** (FR-015/016) — voir §5.

## 2. Architecture interne

```
app/services/jdr/
├── router.py         # routes /services/jdr/*, AppError pour chaque cas HTTP
├── logic.py          # orchestration métier (pas de SQL direct, pas d'imports vendor)
├── schemas.py        # Pydantic v2 — projections in/out de chaque entité
├── prompts.py        # NARRATIVE_/ELEMENTS_/POV_SYSTEM_PROMPT (centralisés, CLAUDE.md §2.4)
├── markdown.py       # rendu MD des artefacts (transcription, narrative, elements, pov)
├── audio.py          # chunking ffmpeg pour cap blast-radius hallucinations Whisper
├── batch/router.py   # sub-router /sessions/{id}/audio + reset
├── live/router.py    # sub-router stub /live/sessions (501) + /live/stream (WS 1011)
└── db/
    ├── models.py     # 8 tables jdr_*
    └── repositories.py  # une classe par entité (ADR 0006 §5)

app/jobs/jdr.py       # _transcribe_session / _generate_narrative / _generate_elements / _generate_povs
```

**Convention 3 couches** (CLAUDE.md §2.4) :
- `router.py` parle HTTP. Convertit les exceptions logic en AppError.
- `logic.py` parle métier. Lève des exceptions de domaine (`DuplicatePjError`, `InvalidMappingError`, `InvalidPlayerError`…).
- `repositories.py` parle SQL. Ne lève qu'une exception infra (`DuplicatePjNameError` qui mappe sur l'`IntegrityError` de la contrainte unique).

**Layered exceptions** : aucune couche ne connaît les types d'exception des couches voisines en aval — exemple : le router ne `catch IntegrityError` jamais ; il `catch DuplicatePjError` venue de `logic`.

## 3. Conventions de prompts

Tous les `*_SYSTEM_PROMPT` sont en français, instruits pour **rester fidèles au transcript** (pas d'invention) et ignorer les labels techniques (`speaker_X`, `unknown`) dans la sortie.

| Prompt | Sortie attendue | Particularité |
|---|---|---|
| `NARRATIVE_SYSTEM_PROMPT` | Récit chronologique en prose, 3ème personne | Pas de conclusion bilan ; s'arrête au dernier événement exploitable |
| `ELEMENTS_SYSTEM_PROMPT` | **JSON strict** `{npcs, locations, items, clues}` | Listes vides plutôt qu'absentes (acceptance US2.3) ; parsing tolère bloc ```json``` ou `{…}` extrait |
| `POV_SYSTEM_PROMPT` | Récit centré sur un PJ donné | Limite l'omniscience : ne raconte que ce que ce PJ pouvait percevoir |

L'utilisateur prompt embarque la transcription formatée segment par segment, et optionnellement le `campaign_context` de la session comme bloc "CONTEXTE DE CAMPAGNE" séparé.

## 4. Instructions opérationnelles

```bash
# 1) Préparer la DB
alembic upgrade head

# 2) Lancer l'API + un worker RQ (deux processus)
uvicorn app.main:app --reload
rq worker default --url redis://localhost:6379/0

# OU tout-en-un via Docker Compose (api + worker + redis)
docker compose up --build
```

**Bootstrap d'une clé MJ** (au premier démarrage uniquement, voir ADR 0006 §3) :
```powershell
python scripts/generate_api_key.py owner   # imprime le token plaintext (à conserver) + le hash Argon2
# Coller dans .env :  API_KEYS='owner:$argon2id$...'
```
Au premier démarrage, l'app importe cette entrée dans `jdr_api_keys` avec `role='gm'`. Les démarrages suivants ignorent l'env var (la DB devient source de vérité).

**Bascule transcription cloud → local** (sans modifier le code) :
```ini
TRANSCRIPTION_PROVIDER=local
TRANSCRIPTION_BASE_URL=http://gpu-host.lan:8001/v1
```
Redémarrer le worker. Aucun fichier de `app/services/jdr/` n'a besoin de changer (SC-009).

## 5. Hôte GPU LAN (transcription locale)

Topologie cible (mémoire `infrastructure_topology.md`) :
- **Pi 5** : orchestrateur uniquement — héberge l'API, le worker RQ, Redis, la DB.
- **PC RTX 4090 sur le même LAN** : héberge le moteur de transcription lourd.

**API attendue côté hôte GPU**, format compatible OpenAI Whisper :

```
POST {TRANSCRIPTION_BASE_URL}/audio/transcriptions
Headers: Authorization: Bearer {TRANSCRIPTION_API_KEY}
Body (multipart):
  file=<audio.m4a>
  model=<TRANSCRIPTION_MODEL>            # informatif
  response_format=verbose_json
  language=fr                            # quand fourni
```

Réponse attendue (enrichie par rapport à l'API OpenAI officielle d'un champ `speaker` par segment) :

```json
{
  "language": "fr",
  "segments": [
    { "speaker": "speaker_1", "start": 0.0, "end": 3.2, "text": "..." },
    { "speaker": "speaker_2", "start": 3.2, "end": 5.1, "text": "..." }
  ]
}
```

**Stack recommandée côté GPU host** (hors scope `ai-kaeyris`) :
- [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) pour la transcription (CTranslate2, bien plus rapide qu'openai-whisper).
- [`pyannote.audio`](https://github.com/pyannote/pyannote-audio) pour la diarisation, fusion des speakers dans la réponse.
- Wrapper FastAPI minimal exposant l'endpoint ci-dessus.
- À écrire dans un repo séparé ; pas de dépendance au repo `ai-kaeyris`.

**Limite assumée du provider cloud** (par défaut Jalon 5) : OpenAI Whisper API ne diarise pas → tous les segments arrivent avec `speaker_label="unknown"` → les résumés POV resteront pauvres tant que l'hôte GPU local n'est pas branché.

## 6. Repères opérationnels

| Question | Endroit |
|---|---|
| Modifier un prompt | `app/services/jdr/prompts.py` |
| Ajouter une nouvelle table | Modèle dans `db/models.py`, repository dans `db/repositories.py`, migration `alembic revision -m "..."` |
| Ajouter un nouvel artefact `kind=...` | `prompts.py` (system prompt) + `jobs/jdr.py` (_generate_xxx) + `schemas.py` + `markdown.py` (rendu) + `router.py` (POST/GET/MD) |
| Voir le contrat REST complet | [`specs/001-kaeyris-jdr/contracts/rest-api.md`](../../specs/001-kaeyris-jdr/contracts/rest-api.md) |
| Vérifier les FRs côté tests | `tests/services/jdr/test_player_access.py` (FR-014), `test_mapping.py` (FR-010/011), `test_audio_*.py` (FR-017, purge) |
