# REST API Contract: Local Model Validation

## POST /services/jdr/settings/models/local/validation

**Auth**: Administrator GM web session required.

**Request body**:

```json
{
  "category": "transcription",
  "model_path": "/models/whisper-large-v3"
}
```

**Validation**:

- `category`: enum `"transcription" | "summary"`
- `model_path`: string, min length 1 after trimming, max length 1024

**Success 200 body**:

```json
{
  "validation_id": "opaque-server-proof",
  "category": "transcription",
  "model_path": "/models/whisper-large-v3",
  "status": "succeeded",
  "runtime": "faster-whisper",
  "model_format": "ctranslate2-whisper",
  "message": "Model loaded and accepted for transcription.",
  "expires_at": "2026-06-16T12:00:00Z"
}
```

**Error responses**: `application/problem+json`.

Recommended `type` suffixes:

- `local-model-path-not-found`
- `local-model-timeout`
- `local-model-out-of-memory`
- `local-model-incompatible-task`
- `local-model-unsupported-format`
- `local-model-validation-expired`
- `local-model-validation-required`

Errors must not include secrets, stack traces, or raw runtime logs.

## PATCH /services/jdr/settings/models

**Auth**: Administrator GM web session required.

**Request body additions**:

```json
{
  "transcription_provider": "local",
  "transcription_local_path": "/models/whisper-large-v3",
  "transcription_local_validation_id": "opaque-server-proof",
  "summary_provider": "local",
  "summary_local_path": "/models/mistral-7b-instruct.Q4_K_M.gguf",
  "summary_local_validation_id": "opaque-server-proof"
}
```

**Rules**:

- If a request introduces or changes a Local path, the matching proof field is required.
- The proof must match the authenticated user, category, normalized path/path hash, succeeded status, and unexpired validation window.
- A request that updates unrelated settings while keeping the saved Local path unchanged does not need to resend the old proof.
- Raw proof values are write-only; settings responses do not echo them.

**Failure examples**:

- Missing proof: `400 local-model-validation-required`
- Expired proof: `400 local-model-validation-expired`
- Wrong user/category/path proof: `400 local-model-validation-required`

## Generated Artifact and Transcription Job Contract

No artifact read endpoint shape changes.

Job behavior changes:

- Local transcription settings use the saved local transcription runtime/path.
- Local summary settings use the saved local text-generation runtime/path for narrative, elements, POV, and summary jobs.
- Saved Local runtime failure fails the job visibly; it does not silently use operator defaults.
- Missing owner/settings retains existing operator fallback.
