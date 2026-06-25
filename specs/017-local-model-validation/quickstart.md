# Quickstart: Local Model Validation

## 1. Configure local runtime defaults

```powershell
$env:LOCAL_MODEL_VALIDATION_TIMEOUT_SECONDS = "45"
$env:LOCAL_MODEL_VALIDATION_TTL_SECONDS = "900"
$env:LOCAL_MODEL_DEVICE = "cpu"
$env:LOCAL_WHISPER_COMPUTE_TYPE = "int8"
$env:LOCAL_LLM_CONTEXT_TOKENS = "2048"
$env:LOCAL_LLM_GPU_LAYERS = "0"
```

Model paths are backend-visible paths. In Docker, prefer mounted container paths such as `/models/...`, not host-only paths such as `D:\models\...`.

## 2. Optional runtime packages

Default backend installation does not require heavyweight local runtimes.

Install local runtime extras only on hosts that validate or execute Local models:

```powershell
pip install -e ".[local]"
```

Expected formats for BD-20:

| Category | Expected format | Notes |
|----------|-----------------|-------|
| transcription | CTranslate2 Whisper directory | Directory should contain a loadable Whisper/CTranslate2 model. |
| summary | GGUF file | Used through llama.cpp Python bindings. |

## 3. Validate a transcription model

```powershell
curl -X POST http://localhost:8000/services/jdr/settings/models/local/validation `
  -H "Content-Type: application/json" `
  -b "session=<cookie>" `
  -d "{\"category\":\"transcription\",\"model_path\":\"/models/whisper-large-v3\"}"
```

Expected: 200 with `validation_id`, `runtime`, `model_format`, and `expires_at`.

## 4. Save Local transcription settings with proof

```powershell
curl -X PATCH http://localhost:8000/services/jdr/settings/models `
  -H "Content-Type: application/json" `
  -b "session=<cookie>" `
  -d "{\"transcription_provider\":\"local\",\"transcription_local_path\":\"/models/whisper-large-v3\",\"transcription_local_validation_id\":\"<validation_id>\"}"
```

Expected: 200 settings response. Missing, expired, wrong-category, or wrong-path proofs return Problem Details.

## 5. Validate a summary model

```powershell
curl -X POST http://localhost:8000/services/jdr/settings/models/local/validation `
  -H "Content-Type: application/json" `
  -b "session=<cookie>" `
  -d "{\"category\":\"summary\",\"model_path\":\"/models/mistral-7b-instruct.Q4_K_M.gguf\"}"
```

## 6. Quality gates

```powershell
ruff check .
pytest tests/services/jdr/test_local_model_validation.py -q
pytest tests/services/jdr/test_model_settings.py -q
pytest tests/services/jdr/test_pipeline_model_routing.py -q
pytest
```

## 7. Frontend contract sync

After backend implementation:

1. Regenerate backend OpenAPI into `docs/context/api/openapi.json`.
2. Copy it to the frontend repository's `docs/context/api/openapi.json`.
3. In the frontend repo, run `npm run gen:api`.
4. In the frontend repo, run `npm run check:api-types`.
