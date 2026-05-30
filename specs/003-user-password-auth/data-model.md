# Data Model: User Password Authentication

**Phase 1 du `/speckit-plan`**. Modele cible pour le login web, les comptes applicatifs, les sessions serveur et la premiere initialisation.

## 1. Schema overview

```text
core_users
  1 |---- N core_web_sessions
```

Les tables sont placees dans le domaine `core` car l'authentification est transverse. Les tables JDR existantes (`jdr_api_keys`, `jdr_sessions`, etc.) ne sont pas supprimees par cette feature.

## 2. `core_users`

Compte humain/web-facing.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | UUID | yes | Primary key |
| `username` | string(150) | yes | Unique, normalized lowercase/trimmed |
| `profile` | enum/string | yes | `gm` or `user` |
| `password_hash` | string(255) | yes | Argon2id hash, never plaintext |
| `status` | enum/string | yes | `active`, `inactive`, `deleted` |
| `created_at` | datetime tz | yes | Creation timestamp |
| `updated_at` | datetime tz | yes | Last profile/password/status update |
| `deleted_at` | datetime tz | no | Set for logical deletion |
| `last_login_at` | datetime tz | no | Updated after successful login |

### Constraints

- `username` is globally unique.
- `profile` accepted values: `gm`, `user`.
- `status` accepted values: `active`, `inactive`, `deleted`.
- `password_hash` must start with a valid Argon2id marker before persistence.
- At least one `active` GM must remain after any update/delete.

### State transitions

```text
active --> inactive
active --> deleted
inactive --> active
inactive --> deleted
deleted --> (terminal for v1)
```

`deleted` is logical deletion, not physical row removal. A deleted user cannot login.

## 3. `core_web_sessions`

Server-side state for browser sessions.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | UUID | yes | Primary key |
| `user_id` | UUID | yes | FK to `core_users.id` |
| `token_hash` | string(255) | yes | Hash of opaque cookie token |
| `created_at` | datetime tz | yes | Session creation time |
| `expires_at` | datetime tz | yes | Configurable duration |
| `revoked_at` | datetime tz | no | Set on logout |
| `last_seen_at` | datetime tz | no | Optional update on authenticated request |
| `user_agent` | string(512) | no | Optional audit/debug context |
| `client_ip` | string(64) | no | Optional audit/debug context |

### Constraints

- `token_hash` is unique.
- A session is valid only when:
  - linked user exists,
  - user status is `active`,
  - `expires_at` is in the future,
  - `revoked_at` is NULL.

### State transitions

```text
created/active --> expired (time-based)
created/active --> revoked (logout)
created/active --> invalid (user inactive/deleted)
```

Expiration does not require a background job. Validation rejects expired sessions. Cleanup can be a later maintenance task.

## 4. First-run setup

No dedicated table is needed. Setup availability is derived from `core_users` count.

| State | Meaning | Allowed action |
|---|---|---|
| `needs_setup` | `core_users` is empty | `POST /services/jdr/auth/setup` may create the first GM |
| `ready` | at least one user exists | setup endpoint is closed/refused |

The first-run setup request creates:

| Field | Value |
|---|---|
| `username` | chosen by the installer/front user |
| `profile` | forced to `gm` |
| `password_hash` | Argon2id hash of the submitted password |
| `status` | `active` |

The setup endpoint must create the user atomically and must re-check emptiness inside the same transaction to avoid two concurrent first-GM creations.

## 5. Configuration

Configuration values:

| Setting | Purpose |
|---|---|
| `SESSION_COOKIE_NAME` | Defaults to `session` |
| `SESSION_COOKIE_SECURE` | `false` in local HTTP, `true` behind HTTPS |
| `SESSION_COOKIE_SAMESITE` | Defaults to `lax` |
| `WEB_SESSION_TTL_SECONDS` | Default proposed: 28800 (8 hours) |

## 6. Relationship with existing auth

Existing `jdr_api_keys` remain for API clients and current JDR ownership. New `core_users` are for browser login and user management.

During implementation, the authenticated identity should carry:

| Field | Meaning |
|---|---|
| `id` | Existing API key id or user id depending on auth source |
| `name` | API key name or username |
| `role/profile` | Existing `gm`/`player` role or new `gm`/`user` profile |
| `source` | `api_key` or `web_session` |

Routes that require GM privileges accept a web user with profile `gm`. Player-specific `/me/*` behavior should remain tied to existing player API keys unless separately specified later.
