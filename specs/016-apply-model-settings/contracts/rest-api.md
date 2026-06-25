# REST API Contract: Apply Model Settings to Generation Pipeline

BD-19 changes the existing model-settings endpoint contract. No new endpoint is introduced.

## GET /services/jdr/settings/models

**Auth**: Administrator GM web session required.

**Success 200 body**:

```json
{
  "transcription_provider": "cloud",
  "summary_provider": "cloud",
  "transcription_local_path": null,
  "summary_local_path": null,
  "transcription_cloud_model": "whisper-large-v3",
  "summary_cloud_model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
  "ollama_model": null,
  "deepinfra_api_key_set": false
}
```

**Rules**:

- If a settings row exists, return saved values plus `deepinfra_api_key_set`.
- If no settings row exists, return effective operator defaults:
  - cloud transcription exposes the effective transcription model.
  - cloud summary exposes the effective LLM model.
  - Ollama summary exposes the effective LLM model as `ollama_model`.
- Never include `deepinfra_api_key`.
- Never expose operator secret presence through `deepinfra_api_key_set`; users without personal keys receive `false`.

## PATCH /services/jdr/settings/models

**Auth**: Administrator GM web session required.

**Request body additions**:

```json
{
  "summary_provider": "ollama",
  "ollama_model": "llama3:8b"
}
```

**Success 200 body**:

Same shape as GET, including `ollama_model`.

**Validation**:

- `ollama_model`: nullable string, max length 200.
- Existing provider enum validation remains unchanged.
- Raw personal cloud key remains write-only.

## Generated Artifact Contract

No endpoint shape changes for artifact reads. Existing `model_used` fields must now reflect the effective provider/model selected by the job rather than blindly mirroring operator env values.

## Transcription Contract

No endpoint shape changes for transcription reads. Existing `provider` and `model_used` fields continue to come from the transcription adapter result.
