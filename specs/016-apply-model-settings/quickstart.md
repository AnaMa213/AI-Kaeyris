# Quickstart: Apply Model Settings to Generation Pipeline

## 1. Prepare database

```powershell
alembic upgrade head
alembic heads
```

Expected: one Alembic head, including the migration that adds `jdr_model_settings.ollama_model`.

## 2. Run focused regression tests

```powershell
pytest tests/adapters/test_llm.py tests/adapters/test_transcription.py tests/services/jdr/test_model_settings.py tests/services/jdr/test_pipeline_model_routing.py -q
```

Expected: all focused tests pass.

## 3. Run quality gate

```powershell
ruff check .
pytest
```

Expected: both commands pass.

## 4. Manual settings smoke test

Start the API and sign in as an administrator GM, then:

```powershell
curl http://localhost:8000/services/jdr/settings/models `
  -H "Cookie: session=<cookie_session>"
```

Expected for a user without saved settings:

- response includes effective provider/model defaults.
- response includes `ollama_model`.
- response does not include `deepinfra_api_key`.
- `deepinfra_api_key_set` is `false`.

Then persist an Ollama model:

```powershell
curl -X PATCH http://localhost:8000/services/jdr/settings/models `
  -H "Content-Type: application/json" `
  -H "Cookie: session=<cookie_session>" `
  -d '{"summary_provider":"ollama","ollama_model":"llama3:8b"}'
```

Expected: response returns `summary_provider="ollama"` and `ollama_model="llama3:8b"`.

## 5. Regenerate OpenAPI

```powershell
python -c "import json; from app.main import app; open('docs/context/api/openapi.json','w',encoding='utf-8').write(json.dumps(app.openapi(),ensure_ascii=False,indent=2,sort_keys=True))"
```

Expected schema diff:

- `ModelSettingsOut` gains `ollama_model`.
- `ModelSettingsPatch` gains `ollama_model`.
