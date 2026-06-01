# Implementation Plan: Campaign Auth Context

**Branch**: `004-campaign-auth-context` | **Date**: 2026-05-31 | **Spec**: [`spec.md`](./spec.md)
**Input**: Feature specification from `specs/004-campaign-auth-context/spec.md`

## Summary

Add the missing BD-4 runtime auth context for the JDR web front: a logged-in browser user can call `GET /services/jdr/auth/me` and receive public user identity plus the active campaign context. The same feature introduces the V1 default campaign, campaign memberships, and campaign scoping for existing JDR data.

Technical approach: keep campaign ownership and JDR data scoping inside the JDR service domain, while extending the already cross-cutting web user model only where it must remember a default campaign. Existing login, setup, logout, user CRUD, and API-key auth remain compatible. V1 remains product-single-campaign, but the data model supports later multi-campaign memberships without adding campaign CRUD now.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x async, Alembic, Redis/RQ already present, structlog
**Storage**: SQLite via `aiosqlite` in dev, PostgreSQL via `asyncpg` in target deployment; one `DATABASE_URL`
**Testing**: pytest + pytest-asyncio + httpx ASGITransport; existing DB fixtures and JDR route tests
**Target Platform**: Local development on Windows/Linux; Raspberry Pi 5 deployment later behind Caddy
**Project Type**: Web-service API in a modular monolith
**Performance Goals**: `/auth/me` and campaign-scoped list/detail reads complete within normal interactive web latency (< 500 ms local p95); no extra LLM or queue latency introduced
**Constraints**: Preserve existing login/logout/setup/users request bodies; no public campaign CRUD; no client-provided campaign scope; keep API-key clients working; no new dependency
**Scale/Scope**: Personal sandbox scale: tens of users, campaigns, sessions, and characters; one V1 default campaign in product use

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Justification |
|---|---|---|
| Honesty over speed | PASS | Current backend was verified: `003-user-password-auth` shipped sessions/users, not BD-4 `/auth/me`. This plan scopes the missing feature explicitly. |
| Pedagogy over output volume | PASS | The feature is split into spec, research, data model, REST contract, and quickstart before implementation. |
| YAGNI | PASS | No campaign CRUD, no tenant/organization layer, no UI, no campaign switch endpoint, no OAuth/JWT expansion. |
| Strict separation of concerns | PASS | Campaign data and JDR scoping live in `app/services/jdr`; `app/core` changes are limited to user default-campaign identity and auth context plumbing. |
| Test discipline | PASS | New public endpoint `/services/jdr/auth/me`, migration backfill, membership creation, and campaign isolation each get targeted tests before implementation. |
| Security by default | PASS | Scope is derived from authenticated session/key, never from request body. Deleted users and invalid sessions are rejected before context resolution. |
| 12-Factor | PASS | No new secret or hardcoded environment-specific credential. State lives in the database; runtime config remains environment-based. |
| Locked stack | PASS | Uses existing FastAPI, Pydantic, SQLAlchemy/Alembic, pytest/httpx stack. No ORM/framework/dependency change. |

**Verdict**: PASS. No constitution violation requires Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/004-campaign-auth-context/
|-- spec.md
|-- plan.md
|-- research.md
|-- data-model.md
|-- quickstart.md
|-- contracts/
|   `-- rest-api.md
|-- checklists/
|   `-- requirements.md
`-- tasks.md              # generated later by /speckit-tasks
```

### Source Code (repository root)

```text
app/
|-- core/
|   |-- auth.py            # carry user-aware web-session auth context
|   |-- models.py          # add default_campaign_id to User only if needed by plan
|   |-- user_schemas.py    # add AuthMe response schemas
|   `-- users.py           # integrate default campaign creation/membership hooks
|-- services/
|   `-- jdr/
|       |-- auth_router.py  # add GET /services/jdr/auth/me and campaign-aware user creation
|       |-- router.py       # derive campaign scope for JDR routes
|       |-- logic.py        # pass campaign scope into business operations
|       `-- db/
|           |-- models.py        # Campaign, CampaignMember, campaign_id columns
|           `-- repositories.py  # campaign/member repositories and scoped query helpers
|-- jobs/
|   `-- jdr.py             # verify jobs cannot cross campaign via session lookup
`-- main.py                # router mounting unchanged

migrations/versions/
`-- 0006_campaign_auth_context.py

tests/
|-- core/
|   `-- test_campaign_context.py
`-- services/
    `-- jdr/
        |-- test_auth_me.py
        |-- test_campaign_memberships.py
        `-- test_campaign_isolation.py
```

**Structure Decision**: Campaign is a JDR business concept, so campaign tables and scoped JDR queries belong under `app/services/jdr`. The only planned `app/core` changes are the minimum needed for cross-cutting authentication: linking a web user to a default campaign and carrying enough identity to resolve context. This keeps the feature aligned with the existing `core_users.api_key_id` compatibility bridge without turning campaign management into a platform-wide module.

## Complexity Tracking

> Empty. No constitution violation.

## Phase 0: Research Summary

See [`research.md`](./research.md). Decisions resolved before design:

- Keep V1 role vocabulary as `gm | player`, not `mj | player`.
- Make `/services/jdr/auth/me` web-session oriented; API keys remain for service operations, but the frontend session context is based on users.
- Store `Campaign` and `CampaignMember` in the JDR service domain.
- Use a single default campaign in V1, created/backfilled by migration or first-run setup.
- Add campaign scope to primary JDR aggregate roots (`Session`, `Pj`) and derive related resource scope through them.

## Phase 1: Design Summary

See [`data-model.md`](./data-model.md), [`contracts/rest-api.md`](./contracts/rest-api.md), and [`quickstart.md`](./quickstart.md).

### Post-Design Constitution Re-check

| Principle | Status | Justification |
|---|---|---|
| Honesty over speed | PASS | Unknowns from the handoff (`mj` vs `gm`, API-key behavior, empty DB setup) are resolved in research with explicit trade-offs. |
| Pedagogy over output volume | PASS | Design docs explain why each boundary and migration choice exists. |
| YAGNI | PASS | V2-ready membership shape is added because BD-4 requires it; campaign CRUD, switching, tenants, and organizations remain out of scope. |
| Strict separation of concerns | PASS | JDR campaign logic stays in service modules; core only exposes identity/session primitives needed by auth. |
| Test discipline | PASS | Contract, migration, integration, and isolation tests are identified before implementation. |
| Security by default | PASS | Campaign scope is server-derived and active-user checked. No client-provided campaign scope can widen access. |
| 12-Factor | PASS | Database-backed state, no new environment-specific config, logs remain stdout via existing structlog setup. |
| Locked stack | PASS | Existing stack only. |

**Verdict**: PASS. Ready for `/speckit-tasks`.
