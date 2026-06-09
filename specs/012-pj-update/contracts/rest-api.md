# REST API Contract: BD-12 PJ Update

## New Endpoint

`PATCH /services/jdr/pjs/{pj_id}`

### Purpose

Partially update a player character owned by the current GM.

### Authentication

Same as existing PJ management endpoints:

- Requires an authenticated GM-compatible caller.
- Player-role callers are forbidden by the existing auth dependency.
- A PJ outside the caller's ownership/campaign scope returns the not-found
  behavior instead of exposing foreign resource existence.

### Path Parameters

| Name | Type | Required | Description |
|---|---|---:|---|
| `pj_id` | UUID | Yes | Existing PJ identifier |

### Request Body: `PjUpdate`

All fields are optional.

```json
{
  "name": "Aragorn",
  "user_id": "00000000-0000-0000-0000-000000000000"
}
```

Clear the user link:

```json
{
  "user_id": null
}
```

No-op payload:

```json
{}
```

### Response: `200 OK`

Returns the existing `PjOut` shape.

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "name": "Aragorn",
  "campaign_id": "11111111-1111-1111-1111-111111111111",
  "user_id": null,
  "created_at": "2026-06-09T12:00:00Z"
}
```

### Error Responses

| Status | Type | Condition |
|---:|---|---|
| `401` | existing auth error | Missing or invalid authentication |
| `403` | existing forbidden behavior | Authenticated caller is not GM-compatible |
| `404` | `pj-not-found` | PJ does not exist or does not belong to current GM scope |
| `409` | `duplicate-pj` | Requested `name` duplicates another PJ for the same GM |
| `422` | `invalid-user` | Non-null `user_id` does not reference an existing user |
| `422` | FastAPI validation error | Payload validation fails |

## OpenAPI Requirements

The generated schema must expose:

- `PATCH /services/jdr/pjs/{pj_id}`
- request model `PjUpdate`
- optional `name` with string bounds matching `PjCreate.name`
- optional nullable `user_id`
- response model `PjOut`

The synced frontend artifact `docs/context/api/openapi.json` must be regenerated
after implementation.

## Out of Scope

- `DELETE /services/jdr/pjs/{pj_id}`
- changing `campaign_id`
- changing `owner_gm_key_id`
- adding update history or audit columns
