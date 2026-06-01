# Implementation Plan: Campaigns CRUD and Session Campaign Filter

**Branch**: `main` | **Date**: 2026-06-01 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/006-campaigns-crud-session-filter/spec.md`

## Summary

BD-6 exposes campaign management for the JDR web frontend and makes session creation/listing explicitly campaign-aware. The implementation extends the existing JDR modular monolith: add campaign schemas and routes under the JDR service, expand the existing campaign repository helpers, require `campaign_id` on new session creation, support `GET /sessions?campaign_id=...`, and keep legacy unfiltered session listing available for compatibility. Existing BD-4 campaign tables and membership/default-campaign helpers are reused; no new bounded context, service split, or ORM change is introduced.

## Technical Context

**Language/Version**: Python 3.12+  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async, Alembic, Redis/RQ already present for surrounding JDR workflows  
**Storage**: SQLite in development and PostgreSQL target via existing SQLAlchemy/Alembic setup  
**Testing**: pytest + httpx ASGI tests, plus ruff  
**Target Platform**: Linux web API runtime, developed locally on Windows/PowerShell  
**Project Type**: Modular-monolith REST web service  
**Performance Goals**: Campaign list and session filter should stay within one normal request round trip for the small personal-platform scale; aggregate counts must avoid N+1 queries for normal campaign lists  
**Constraints**: Preserve backward-compatible unfiltered session listing; require explicit campaign on new sessions; reject cross-campaign access; keep PJs global for BD-6; preserve BD-5 timezone-aware JSON output  
**Scale/Scope**: Personal/local-network RPG platform, low campaign/user counts, frontend requires machine-readable contract for type generation

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Honesty over speed**: PASS. Plan documents known BD-4 constraints and explicitly calls out no new DB table unless current schema gaps require an Alembic adjustment.
- **Pedagogy over output volume**: PASS. Design artifacts explain why each decision is chosen and keep trade-offs visible.
- **YAGNI**: PASS. No member invitation workflow, campaign switch endpoint, role expansion, cascade delete, search/sort, or campaign-scoped PJs.
- **Strict separation of concerns**: PASS. Work stays in `app/services/jdr/`, with shared auth/config/db concerns reused from `app/core/`.
- **Test discipline**: PASS. Public endpoints and cross-campaign authorization require endpoint tests before implementation.
- **Security by default**: PASS. All campaign access is derived from authenticated session/key context and membership checks, never from trusting client scope alone.
- **12-Factor compliance**: PASS. No secrets or environment-specific config are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/006-campaigns-crud-session-filter/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── rest-api.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
app/
├── core/
│   ├── models.py                 # User.default_campaign_id already exists
│   └── user_schemas.py           # /auth/me schemas stay compatible
└── services/
    └── jdr/
        ├── campaign_context.py   # membership/default campaign helpers reused/extended
        ├── router.py             # sessions routes gain campaign_id query/body behavior
        ├── schemas.py            # Campaign* schemas + SessionCreate campaign_id
        ├── logic.py              # session/campaign business operations
        └── db/
            ├── models.py         # Campaign, CampaignMember, Session already exist
            └── repositories.py   # campaign/session query helpers expanded

migrations/
└── versions/                     # add migration only if current schema lacks required BD-6 fields

tests/
├── core/
│   └── test_campaign_context.py  # migration/backfill/default campaign guards
└── services/
    └── jdr/
        ├── test_campaigns_crud.py
        ├── test_campaign_sessions.py
        ├── test_campaign_isolation.py
        └── campaign_fixtures.py
```

**Structure Decision**: Keep the feature inside the existing JDR service. Campaigns are a JDR business concept, not a platform-wide module yet. This follows the project’s modular-monolith rule and avoids a premature cross-service abstraction.

## Complexity Tracking

No constitution violations.

## Phase 0 Research Summary

See [research.md](./research.md).

Key decisions:

- Reuse BD-4 `jdr_campaigns`, `jdr_campaign_members`, and `core_users.default_campaign_id`.
- Add campaign CRUD inside the JDR router/service surface.
- Require campaign membership for reads, GM role for write/delete/session creation.
- Refuse deleting campaigns that already have sessions.
- Keep PJs global for BD-6 even though current BD-4 code has transitional `Pj.campaign_id`; endpoint behavior should not introduce campaign-scoped PJ UX.

## Phase 1 Design Summary

See [data-model.md](./data-model.md), [contracts/rest-api.md](./contracts/rest-api.md), and [quickstart.md](./quickstart.md).

Post-design constitution re-check:

- **YAGNI** remains PASS: delete cascade, invitations, campaign switch, search/sort, and PJ campaign scope remain out.
- **Security** remains PASS: all endpoints include membership/role authorization criteria.
- **Test discipline** remains PASS: quickstart and contracts identify public endpoint tests and regression tests.
- **Separation of concerns** remains PASS: no vendor logic, no frontend code, no new service boundary.
