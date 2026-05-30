# Implementation Plan: User Password Authentication

**Branch**: `003-user-password-auth` | **Date**: 2026-05-27 | **Spec**: [`spec.md`](./spec.md)
**Input**: Feature specification from `specs/003-user-password-auth/spec.md`

## Summary

Replace the temporary web login that treated a GM API token as a password with a real user/password model for the web front-end. The MVP is: create a profile with `username + profile + password`, login with that profile through `POST /services/jdr/auth/login`, receive an HTTP-only session cookie, then use that cookie to call protected endpoints.

Technical approach: add a small core authentication model (`users` + `web_sessions`) beside the existing DB-backed API-key registry. API keys remain available for machine clients and existing JDR ownership, but browser sessions authenticate users via server-side opaque session tokens. User profile creation is part of the MVP and remains GM-only; broader lifecycle operations arrive after the MVP.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x async, Alembic, `argon2-cffi`, Redis/RQ already present, structlog
**Storage**: SQLite via `aiosqlite` in dev, PostgreSQL via `asyncpg` in target deployment; one `DATABASE_URL`
**Testing**: pytest + pytest-asyncio + httpx ASGITransport; DB fixtures already present
**Target Platform**: Local development on Windows/Linux; Raspberry Pi 5 deployment later behind Caddy
**Project Type**: Web-service API in a modular monolith
**Performance Goals**: Login, logout, and user CRUD complete within normal interactive web latency (< 500 ms local p95, excluding Argon2 cost variance)
**Constraints**: Preserve `/services/jdr/auth/login`; no plaintext passwords; no hardcoded/default credentials; no required `.env` edit for first use; exact front error bodies; no OAuth/OIDC/JWT expansion in this feature
**Scale/Scope**: Personal sandbox scale: tens of users/sessions, not public multi-tenant SaaS; one feature slice focused on web user auth

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Justification |
|---|---|---|
| Honesty over speed | PASS | Existing login changes were removed; plan starts from current `main`. Security choices cite OWASP, RFC 6265, 12-Factor and existing ADRs. |
| Pedagogy over output volume | PASS | Feature is split into spec, research, data model, contracts, quickstart; implementation should proceed test-first. |
| YAGNI | PASS | No OAuth/OIDC, JWT, email reset, invitations, self-service signup, or scopes beyond `gm`/`user`. |
| Strict separation of concerns | PASS | Core auth/session belongs in `app/core`; JDR keeps only the compatibility route prefix needed by the front. No vendor names in business services. |
| Test discipline | PASS | Every new public endpoint has a contract/integration test target; password/session logic gets unit tests. |
| Security by default | PASS | Argon2id password hashing, HTTP-only cookie, server-side opaque session, expiry, logout, no user enumeration, last-GM guard. |
| 12-Factor | PASS | Session duration and cookie flags are config/env-driven. User/session state lives in the database. |
| Locked stack | PASS | Uses existing stack only: FastAPI, Pydantic, SQLAlchemy, Alembic, argon2-cffi, pytest/httpx. No new dependency. |

**Verdict**: PASS. No constitution violation requires Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/003-user-password-auth/
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
|   |-- auth.py            # extend AuthenticatedKey/user session auth
|   |-- config.py          # cookie/session settings
|   |-- db.py              # unchanged shared SQLAlchemy base/session
|   `-- users.py           # user/session business helpers (new)
|-- services/
|   `-- jdr/
|       |-- auth_router.py  # /services/jdr/auth/login + logout compatibility prefix
|       `-- router.py      # user management endpoints may be mounted here or via sub-router
`-- main.py                # mount public auth router outside protected JDR router

migrations/versions/
`-- 0005_user_password_auth.py

tests/
|-- core/
|   |-- test_user_auth.py
|   `-- test_web_sessions.py
`-- services/
    `-- jdr/
        |-- test_auth_login.py
        `-- test_user_management.py
```

**Structure Decision**: Put user/password/session mechanics in `app/core` because they are cross-cutting authentication concerns, not JDR business logic. Keep the HTTP route under `/services/jdr/auth` only to preserve the current front contract. This is a controlled exception to "feature code in services" because authentication is already a core concern in the constitution.

## Complexity Tracking

> Empty. No constitution violation.
