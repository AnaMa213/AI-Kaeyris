# Contracts: REST API - User Password Authentication

**Phase 1 du `/speckit-plan`**. Surface REST ajoutee ou modifiee par la feature.

All error bodies below use `Content-Type: application/problem+json`. Login keeps exact bodies required by the front.

## 1. First-run setup

### `GET /services/jdr/auth/setup/status`

Public endpoint used by the front before login.

**Success: 200**

```json
{
  "required": true
}
```

`required=true` when no user exists. `required=false` when at least one user exists.

### `POST /services/jdr/auth/setup`

Public endpoint, but available only while no user exists.

**Request**

```json
{
  "username": "admin",
  "password": "chosen-password"
}
```

`profile` is not accepted from the client here: the created user is always `gm`.

**Success: 201**

Headers:

```http
Set-Cookie: session=<opaque-token>; HttpOnly; Path=/; SameSite=Lax
```

Body:

```json
{
  "id": "uuid",
  "username": "admin",
  "profile": "gm",
  "status": "active",
  "created_at": "2026-05-27T10:00:00Z",
  "updated_at": "2026-05-27T10:00:00Z",
  "last_login_at": "2026-05-27T10:00:00Z"
}
```

**Already initialized: 409**

```json
{
  "type": "https://errors.ai-kaeyris.local/setup-closed",
  "title": "Setup closed",
  "status": 409,
  "detail": "First-run setup is only available before the first user exists."
}
```

## 2. Login

### `POST /services/jdr/auth/login`

Public endpoint. Does not require existing auth.

**Request**

```json
{
  "username": "alice",
  "profile": "gm",
  "password": "string"
}
```

`profile` accepted values: `gm`, `user`.

**Success: 200**

Headers:

```http
Set-Cookie: session=<opaque-token>; HttpOnly; Path=/; SameSite=Lax
```

Body optional and may be empty.

**Invalid credentials: 401**

```json
{
  "type": "about:blank",
  "title": "Invalid credentials",
  "status": 401
}
```

Returned for wrong username/password/profile combination, inactive user, deleted user, expired bootstrap mismatch, or any case where revealing account existence would leak information.

**Forbidden/unsupported profile: 403**

```json
{
  "type": "about:blank",
  "title": "Forbidden",
  "status": 403
}
```

Returned when `profile` is not supported by the backend contract.

## 3. Logout

### `POST /services/jdr/auth/logout`

Requires a valid web session cookie.

**Success: 204**

Effects:

- marks the current web session revoked,
- expires/deletes the `session` cookie in the response.

**Unauthorized: 401**

Standard platform unauthorized Problem Details. Exact body does not need to match login's `about:blank` contract unless the front requires it later.

## 4. User management

All endpoints below require GM privileges. A web user with profile `gm` is accepted. Existing GM API-key auth may also be accepted for backward compatibility.

### `POST /services/jdr/users`

Create a user.

**Request**

```json
{
  "username": "bob",
  "profile": "user",
  "password": "string"
}
```

**Success: 201**

```json
{
  "id": "uuid",
  "username": "bob",
  "profile": "user",
  "status": "active",
  "created_at": "2026-05-27T10:00:00Z",
  "updated_at": "2026-05-27T10:00:00Z",
  "last_login_at": null
}
```

**Errors**

- `409 duplicate-user` when username already exists.
- `422 validation-error` for invalid profile, username, or password.

### `GET /services/jdr/users`

List users. Password hashes and session tokens are never exposed.

**Success: 200**

```json
{
  "items": [
    {
      "id": "uuid",
      "username": "alice",
      "profile": "gm",
      "status": "active",
      "created_at": "2026-05-27T10:00:00Z",
      "updated_at": "2026-05-27T10:00:00Z",
      "last_login_at": "2026-05-27T11:00:00Z"
    }
  ]
}
```

### `PATCH /services/jdr/users/{user_id}`

Modify profile, password, or status.

**Request**

```json
{
  "profile": "gm",
  "password": "new-password",
  "status": "active"
}
```

All fields optional, but at least one must be present.

**Success: 200**

Returns the public user representation.

**Errors**

- `404 user-not-found`.
- `409 last-gm` when change would leave zero active GM users.
- `422 validation-error`.

### `DELETE /services/jdr/users/{user_id}`

Logical deletion only.

**Success: 204**

Effects:

- sets user status to `deleted`,
- sets `deleted_at`,
- revokes active web sessions for that user.

**Errors**

- `404 user-not-found`.
- `409 last-gm` when deletion would leave zero active GM users.

## 5. Authenticated request behavior

Protected endpoints accept:

1. existing `Authorization: Bearer <api_key>` for API clients;
2. `Cookie: session=<opaque-token>` for browser clients.

If both are provided, Authorization header takes precedence for backward compatibility and explicit API usage.
