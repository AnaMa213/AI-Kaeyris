# Data Model: Apply Model Settings to Generation Pipeline

## ModelSettings

**Represents**: Per-web-user AI model provider choices for JDR workflows.

**Existing fields used by BD-19**:

- `user_id`: owner user identifier.
- `transcription_provider`: functional provider for transcription (`cloud`, `local`, `ollama` enum value currently accepted by schema).
- `summary_provider`: functional provider for LLM summaries (`cloud`, `local`, `ollama`).
- `transcription_local_path`: future local in-process path, not executed in BD-19.
- `summary_local_path`: future local in-process path, not executed in BD-19.
- `transcription_cloud_model`: optional cloud transcription model id.
- `summary_cloud_model`: optional cloud LLM model id.
- `deepinfra_api_key`: write-only personal cloud key.

**New field**:

- `ollama_model`: optional string, max length 200. Used only when `summary_provider=ollama`.

**Validation rules**:

- `ollama_model` is nullable and bounded to 200 characters.
- Raw `deepinfra_api_key` is accepted only in write payloads and never appears in output schemas.
- Empty personal keys keep the existing key, preserving current patch semantics.

## Session Ownership Projection

**Represents**: The minimum data needed to choose account settings for a job.

**Resolution order**:

1. `Session.campaign_id -> Campaign.owner_user_id`
2. `Session.gm_key_id -> ApiKey.owner_user_id`
3. `None`, which means operator configuration fallback

**Validation rules**:

- Missing session remains a permanent job error, as today.
- Missing owner is not a job error by itself; it selects operator defaults.

## EffectiveModelRoute

**Represents**: Runtime-only route chosen for a transcription or LLM call.

**Fields**:

- `provider`: technical adapter provider actually used.
- `model`: model identifier actually used.
- `api_key_source`: operator, personal, or noop; not logged or serialized.
- `base_url`: adapter endpoint URL where needed.

**Lifecycle**:

- Built before the external model call.
- Used to construct an adapter.
- For LLM artifacts, represented publicly only as `model_used="{provider}:{model}"`.
- For transcription, represented by `TranscriptionResult.model_used` and `provider`.

## State Transitions

No new persistent lifecycle state is introduced.

- Existing transcription job states remain unchanged.
- Existing artifact UPSERT behavior remains unchanged.
- Model settings updates remain patch-style partial updates.

## Relationships

- One `ModelSettings` row belongs to one web user.
- One `Campaign` has one owner user.
- One `Session` may belong to one campaign and always has a GM API key.
- One generation job selects at most one owning user's settings.
