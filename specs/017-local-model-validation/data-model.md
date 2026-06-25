# Data Model: Local Model Validation

## Entity: LocalModelValidation

Represents a short-lived proof that a backend-visible local model path was successfully validated for one user and one category.

| Field | Type | Rules |
|-------|------|-------|
| `validation_hash` | string | Primary key; SHA-256 hex digest of the opaque proof returned to the client. |
| `user_id` | UUID | Required; references the authenticated administrator GM. |
| `category` | enum | `transcription` or `summary`. |
| `model_path` | string | Normalized backend-visible path, max 1024 characters. |
| `path_hash` | string | SHA-256 hex digest of normalized path, used for binding checks. |
| `status` | enum | `succeeded` for proofs accepted by PATCH. Failed attempts are represented by Problem Details and are not persisted as reusable proofs. |
| `runtime` | string | Runtime label returned to the frontend. |
| `model_format` | string | Detected/inferred format label. |
| `message` | string | Short user-safe message. |
| `expires_at` | datetime | UTC expiry; proof is unusable after this instant. |
| `created_at` | datetime | UTC creation timestamp. |

### Invariants

- A proof is valid only when `status=succeeded`, `expires_at` is in the future, and user/category/path hash match the save request.
- The raw `validation_id` is never stored.
- Validation errors do not create reusable proof records.

## Entity: ModelSettings additions

Extends the existing per-GM model settings row.

| Field | Type | Rules |
|-------|------|-------|
| `transcription_local_validation_hash` | string/null | Hash of the proof accepted for the saved transcription Local path. |
| `summary_local_validation_hash` | string/null | Hash of the proof accepted for the saved summary Local path. |

### Invariants

- A changed Local transcription path requires a proof whose hash is stored in `transcription_local_validation_hash`.
- A changed Local summary path requires a proof whose hash is stored in `summary_local_validation_hash`.
- Proof fields are write-only in PATCH; the settings response does not need to expose reusable proof values.

## Entity: LocalModelPath

Represents the submitted backend-visible path.

| Field | Type | Rules |
|-------|------|-------|
| `category` | enum | `transcription` or `summary`. |
| `model_path` | string | Non-empty after trimming; max 1024 characters; normalized before proof binding. |

### State Transitions

```text
submitted -> normalized -> checked_exists -> format_checked -> runtime_loaded -> proof_created
submitted -> normalized -> rejected_problem
```

## Entity: LocalRuntimeSelection

Transient job-time selection object, not persisted separately.

| Field | Type | Rules |
|-------|------|-------|
| `provider` | string | `local` when saved Local settings apply. |
| `model_path` | string | Path from saved settings. |
| `category` | enum | Determines transcription or text-generation adapter. |

### Invariants

- Missing owner/settings keeps operator fallback.
- Saved Local settings never fall back silently on runtime failure.
