# Implementation Plan: Campaign Auth Context

**Branch**: `codex/bd4-campaign-auth-context` | **Date**: 2026-05-30 | **Spec**: [`spec.md`](./spec.md)
**Input**: Feature specification from `specs/004-campaign-auth-context/spec.md`

## Summary

BD-4 adds campaigns as the JDR multi-tenancy boundary requested by the frontend: authenticated users can call `GET /services/jdr/auth/me` to receive `{ user, active_campaign }`, existing users are attached to one V1 default campaign, newly created users are enrolled automatically, and JDR data access is scoped by the active campaign derived server-side.

Technical approach: add minimal campaign/membership persistence and active-campaign resolution around the existing browser session auth. Preserve existing route contracts except for the new `/auth/me` endpoint. Add `campaign_id` to campaign-owned JDR data where the backend must filter directly, while dependent tables continue to inherit scope through their parent session or PJ. No V1 campaign-management endpoints are introduced.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x async, Alembic, pytest, httpx, structlog
**Storage**: SQLite via `aiosqlite` in dev; PostgreSQL via `asyncpg` for target deployment; one `DATABASE_URL`
**Testing**: pytest + pytest-asyncio + httpx ASGITransport; existing in-memory SQLite fixtures create all ORM tables
**Target Platform**: Local development on Windows/Linux; Raspberry Pi 5 deployment later via Docker Compose and Caddy
**Project Type**: Web-service API in a modular monolith
**Performance Goals**: `/auth/me` and campaign-context resolution stay within normal interactive web latency; list endpoints avoid cross-campaign scans where `campaign_id` is available
**Constraints**: No campaign management UI/API in V1; no `campaign_id` in frontend create bodies; preserve existing user-management contracts; preserve Bearer API-key compatibility for existing JDR workflows; no new framework or service split
**Scale/Scope**: Personal sandbox scale: one normal V1 campaign, tens of users/sessions; design allows multiple campaigns in DB for isolation tests and later Hub work

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Justification |
|---|---|---|
| Honesty over speed | PASS | The plan is based on the current auth/JDR code, not the frontend handoff alone. Open endpoint coverage is deferred to route inspection during tasks. |
| Pedagogy over output volume | PASS | Design artifacts separate decisions, data model, contracts, and manual validation before implementation. |
| YAGNI | PASS | No campaign CRUD, campaign switcher, tenant/organization layer, membership admin API, or default-campaign edit endpoint. |
| Strict separation of concerns | PASS | Campaign context is exposed through JDR auth routes and helpers; external providers are untouched; no cross-service imports are introduced. |
| Test discipline | PASS | New public endpoint and campaign scoping behavior have explicit contract/integration test targets. |
| Security by default | PASS | Campaign identity is derived server-side from auth context, not trusted from request bodies/query params. `/auth/me` is planned with `Cache-Control: no-store`. |
| 12-Factor | PASS | No hardcoded secret or environment-specific config. State is persisted in the database, keeping processes stateless (https://12factor.net/processes). |
| Locked stack | PASS | Uses existing FastAPI/Pydantic/SQLAlchemy/Alembic/pytest stack only. No new dependency. |

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
|   |-- auth.py             # keep session/API-key auth; may expose current user for /auth/me
|   |-- models.py           # core_users gains default_campaign_id if chosen in implementation
|   |-- user_schemas.py     # add /auth/me response schemas or import from JDR schema module
|   `-- users.py            # user creation/update hooks for campaign membership
|-- services/
|   `-- jdr/
|       |-- auth_router.py   # add GET /services/jdr/auth/me and campaign-aware user flow
|       |-- logic.py         # campaign-aware session/PJ/user orchestration
|       |-- schemas.py       # JDR-facing output schemas if not kept in core
|       `-- db/
|           |-- models.py    # Campaign, CampaignMember, campaign_id columns on JDR data
|           `-- repositories.py
`-- main.py                 # existing router mounting remains

migrations/versions/
`-- 0006_campaign_auth_context.py

tests/
|-- core/
|   `-- test_campaign_context.py
`-- services/
    `-- jdr/
        |-- test_auth_me.py
        |-- test_campaign_memberships.py
        |-- test_campaign_scoping.py
        `-- test_user_campaign_membership.py
```

**Structure Decision**: Keep BD-4 inside the existing modular monolith and JDR service. Campaigns are the JDR multi-tenancy boundary, so routing and campaign-scoped business behavior stay under `app/services/jdr/`. The existing `core_users` table may receive `default_campaign_id` because browser identity is cross-cutting, but campaign-owned data remains part of the JDR data model. This is smaller than introducing a new `campaigns` service and matches the current "monolith first" architecture.

## Post-Design Constitution Check

| Principle | Status | Justification |
|---|---|---|
| YAGNI | PASS | Phase 1 artifacts keep campaign CRUD, campaign switching, tenant/org hierarchy, and membership admin APIs out of scope. |
| Separation of concerns | PASS | Contracts and data model keep campaign behavior inside JDR and reuse existing core auth only for browser identity. |
| Test discipline | PASS | Quickstart and contracts define tests for `/auth/me`, migration/backfill, two-campaign isolation, and user membership side effects. |
| Security by default | PASS | Campaign context remains server-derived; `/auth/me` uses no-store; request bodies do not accept `campaign_id`. |

**Verdict**: PASS. Design artifacts introduce no new constitution violation.

## Complexity Tracking

> Empty. No constitution violation.
