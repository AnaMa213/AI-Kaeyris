# Implementation Plan: Identity Refactor and PJ Campaign Scoping

**Branch**: `007-identity-refacto-pj-scoping` | **Date**: 2026-06-01 | **Spec**: [`spec.md`](spec.md)  
**Input**: Feature specification from `specs/007-identity-refacto-pj-scoping/spec.md`

## Summary

BD-7 separates global portal permissions from campaign roles and makes PJs campaign-scoped. The backend will replace public `profile` usage with `system_role` (`admin` or `user`), rename campaign membership `player` to `pj`, require every PJ to belong to a campaign, allow optional PJ user assignment, preserve V1 PJ creation compatibility through default-campaign fallback, and update current-identity/user/PJ contracts for frontend synchronization.

The implementation approach is deliberately narrow: reuse the existing FastAPI, Pydantic v2, SQLAlchemy async, Alembic, and pytest stack; update the existing JDR auth/user/campaign/PJ surfaces; add one purge-oriented schema migration because the owner accepted local/staging data loss; and keep future PATCH/DELETE PJ management out of the required BD-7 scope unless it falls out naturally.

## Technical Context

**Language/Version**: Python 3.12+  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x async, Alembic, argon2-cffi, Redis/RQ where existing jobs are touched indirectly  
**Storage**: SQLite for dev/tests, PostgreSQL target; Alembic migrations are the source of schema evolution  
**Testing**: pytest + pytest-asyncio + httpx ASGITransport; ruff for lint  
**Target Platform**: Local/LAN FastAPI service, Docker Compose deployable  
**Project Type**: Modular monolith web service  
**Performance Goals**: Keep identity/PJ list operations suitable for interactive frontend flows; no new long-running synchronous work  
**Constraints**: No committed production credentials; all public inputs validated by Pydantic; no vendor-specific code in `app/services/jdr/`; no cross-service imports; no ORM/framework change  
**Scale/Scope**: Personal AI/JDR platform; local/staging purge accepted for impacted identity/JDR data; no production data-preserving migration required for BD-7

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| Honesty over speed | PASS | BD-7 acknowledges the destructive purge assumption explicitly; no claim of data-preserving migration. |
| Pedagogy over output volume | PASS | Plan keeps decisions visible and narrow so implementation can be explained story by story. |
| YAGNI | PASS | PATCH/DELETE PJ management, audit log, invitation flow, fine-grained RBAC, soft-delete redesign, and cross-campaign PJ inheritance stay out of required scope. |
| Strict separation of concerns | PASS | Changes stay in `app/core` for user identity and `app/services/jdr` for JDR business behavior. |
| Test discipline | PASS | Every changed public endpoint requires focused tests plus contract/OpenAPI checks. |
| Security by default | PASS WITH GUARDRAIL | Admin seed must be dev/staging-only and explicit; no production hardcoded credentials or committed secrets. |
| 12-Factor compliance | PASS | Runtime configuration remains environment-based; schema changes are versioned migrations. |
| Locked stack | PASS | No framework, ORM, queue, auth, or deployment stack change. |

## Project Structure

### Documentation (this feature)

```text
specs/007-identity-refacto-pj-scoping/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── rest-api.md
└── tasks.md
```

### Source Code (repository root)

```text
app/
├── core/
│   ├── auth.py              # web/API auth role resolution updates
│   ├── models.py            # user system_role model update
│   ├── user_schemas.py      # user/auth response contract updates
│   └── users.py             # admin/user account behavior updates
├── services/jdr/
│   ├── auth_router.py       # auth/me and admin-only user routes
│   ├── campaign_context.py  # gm/pj campaign role helpers
│   ├── db/
│   │   ├── models.py        # CampaignMember and Pj schema changes
│   │   └── repositories.py  # campaign/PJ membership queries
│   ├── logic.py             # campaign-scoped PJ behavior
│   ├── router.py            # PJ/session/user-facing route behavior
│   └── schemas.py           # PJ and campaign role response schemas
└── main.py                  # route mounting unchanged unless OpenAPI metadata needs refresh

migrations/versions/
└── 0008_identity_refacto_pj_scoping.py

tests/
├── core/
│   └── test_*identity*.py
└── services/jdr/
    ├── test_auth_me.py
    ├── test_campaign_memberships.py
    ├── test_pjs.py
    ├── test_campaign_sessions.py
    ├── test_user_management.py
    └── test_datetime_serialization.py

docs/
├── context/api/openapi.json # if backend repo owns the synced OpenAPI artifact
├── journal.md
├── memo.md
└── services/jdr.md
```

**Structure Decision**: Keep the existing modular monolith layout. Identity concepts that are global to accounts live under `app/core`; JDR-specific campaign/PJ membership behavior remains under `app/services/jdr`. No new service/module boundary is introduced.

## Complexity Tracking

No constitution violations require extra complexity tracking.

## Phase 0: Research Summary

See [`research.md`](research.md). Key decisions:

- Treat BD-7 as a purge/reseed migration for impacted local/staging data.
- Rename public role vocabulary to `system_role` and `pj` while keeping legacy API-key `Role.PLAYER` only where machine/player-token auth still requires it.
- Make `Pj.campaign_id` mandatory and `Pj.user_id` optional.
- Keep `POST /pjs` compatible by falling back to the current user's default campaign when `campaign_id` is omitted.

## Phase 1: Design Summary

See [`data-model.md`](data-model.md), [`contracts/rest-api.md`](contracts/rest-api.md), and [`quickstart.md`](quickstart.md).

Design highlights:

- `User.system_role` separates account administration from campaign mastery.
- `CampaignMember.role` becomes `gm | pj`.
- `Pj` becomes campaign-scoped and optionally assigned to a user.
- `/services/jdr/auth/me` returns `user.system_role` and active campaign role `gm | pj`.
- `/services/jdr/users/*` becomes admin-only.
- `/services/jdr/campaigns/*` remains authenticated/campaign-scoped, and standard users can create campaigns.
- `/services/jdr/pjs` supports both V1 compatibility and campaign filtering.

## Post-Design Constitution Check

| Principle | Status | Notes |
|---|---|---|
| Honesty over speed | PASS | Quickstart calls out purge/reseed and validation commands explicitly. |
| Pedagogy over output volume | PASS | Data model and contracts document why role separation exists. |
| YAGNI | PASS | Future PJ editing and invitation/audit/RBAC work remains out of scope. |
| Strict separation of concerns | PASS | Data model maps cleanly to existing `core` vs `services/jdr` ownership. |
| Test discipline | PASS | Contract and quickstart define tests for every changed public endpoint. |
| Security by default | PASS WITH GUARDRAIL | Seed behavior must be explicit local/staging setup; no committed real secret. |
| 12-Factor compliance | PASS | Config/secrets remain environment-driven and migrations are versioned. |
| Locked stack | PASS | No forbidden stack change introduced. |
