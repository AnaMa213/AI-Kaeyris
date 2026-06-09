# Research: BD-12 PJ Update

## Decision 1: Add `PATCH /services/jdr/pjs/{pj_id}`

**Decision**: Expose partial PJ editing with HTTP `PATCH`.

**Rationale**: The handoff asks for a partial update where all request fields
are optional. RFC 5789 defines PATCH as the method for applying partial
modifications to an existing resource: https://www.rfc-editor.org/rfc/rfc5789

**Alternatives considered**:

- `PUT /pjs/{pj_id}`: rejected because the client is not replacing the whole PJ
  representation.
- `POST /pjs/{pj_id}/rename` and `POST /pjs/{pj_id}/link-user`: rejected because
  two action endpoints add surface area for one resource update.

## Decision 2: Preserve omitted-vs-null field semantics

**Decision**: Implement `PjUpdate` so the logic can distinguish absent fields
from explicit `null`, especially for `user_id`.

**Rationale**: BD-12 needs `user_id: null` to unlink a PJ. If the backend treats
an omitted field the same as `null`, a rename-only request could accidentally
clear the account link.

**Alternatives considered**:

- Make `user_id` required: rejected because PATCH must allow rename-only.
- Add a separate `unlink_user` boolean: rejected because JSON `null` already
  expresses clearing a nullable relationship cleanly.

## Decision 3: Reuse existing PJ ownership and campaign boundaries

**Decision**: Load the target PJ through the existing owner/campaign-scoped
repository pattern and return the existing not-found behavior for cross-owner
updates.

**Rationale**: `GET/POST /pjs` already scope PJs by GM and campaign membership.
BD-12 should not introduce a second authorization model for the same resource.

**Alternatives considered**:

- Load by raw `pj_id` and then check ownership: rejected because it makes it
  easier to leak whether another GM's PJ exists.
- Add role-specific update rules beyond GM ownership: rejected as broader than
  the frontend handoff.

## Decision 4: Reuse duplicate-name behavior and align invalid-user behavior

**Decision**: Reuse the current duplicate-name path for `name` changes and the
same `core_users` existence check used by PJ creation for `user_id` changes.
Expose unknown non-null `user_id` as `422 invalid-user` for both PJ creation and
PJ update.

**Rationale**: The handoff explicitly asks for the same public error behavior as
`POST /pjs`: duplicate PJ names stay `409 duplicate-pj`, unknown users are
reported as `422 invalid-user`. Current code has the domain exception
(`PjAssignmentError`) but appears to map it to `pj-not-found`; BD-12 should fix
that contract mismatch while adding the PATCH route.

**Alternatives considered**:

- Add new name-conflict types such as `pj-name-conflict`: rejected because it would
  force unnecessary frontend branching.
- Preserve the apparent current `pj-not-found` mapping for unknown users:
  rejected because it conflicts with the BD-12 handoff and makes user assignment
  errors indistinguishable from PJ ownership lookup failures.
- Silently ignore unknown users: rejected because it would hide data-entry
  errors and produce surprising state.

## Decision 5: No database migration and no delete endpoint

**Decision**: Do not change schema and do not implement deletion.

**Rationale**: `jdr_pjs.user_id` already exists as a nullable FK to
`core_users.id`, and the product need is edit/link/unlink only. Deletion needs a
separate invariant for session mappings and non-diarised player lists.

**Alternatives considered**:

- Add `updated_at`: rejected because the feature only requires returning current
  PJ details and no audit/ordering behavior depends on update time.
- Implement DELETE now: rejected as scope creep; the handoff marks it optional
  and not required by the frontend.

## Decision 6: Regenerate the checked-in OpenAPI artifact

**Decision**: Regenerate `docs/context/api/openapi.json` after adding the route.

**Rationale**: The frontend consumes the backend OpenAPI contract for type
generation. OpenAPI is the standard API description format used by FastAPI's
generated schema: https://spec.openapis.org/oas/latest.html

**Alternatives considered**:

- Rely only on runtime `/openapi.json`: rejected because this repository already
  tracks `docs/context/api/openapi.json` as the frontend sync artifact.
- Hand-edit OpenAPI JSON: rejected because the generated schema must reflect the
  actual FastAPI route and Pydantic models.
