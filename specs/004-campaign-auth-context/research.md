# Research: Campaign Auth Context

**Phase 0 du `/speckit-plan`**. Objectif : verrouiller les decisions techniques avant les contrats et le modele de donnees.

## 1. Campaign as V1 multi-tenancy boundary

### Decision

Represent campaign as the JDR multi-tenancy boundary and seed exactly one V1 default campaign for normal usage.

### Rationale

The frontend needs a stable `{ user, active_campaign }` contract now, while the product remains effectively single-campaign in V1. Keeping the multi-tenancy boundary at campaign level avoids introducing a broader tenant/organization abstraction that is explicitly out of scope. This follows the project's YAGNI rule and the 12-Factor preference for storing state in backing services rather than process memory (https://12factor.net/processes).

### Alternatives considered

| Alternative | Pourquoi rejetee |
|---|---|
| Tenant/organization table above campaigns | Hors scope BD-4 and explicitly rejected by the frontend handoff. |
| Front hardcodes a fake campaign | Forces future refactors in consuming components once live multi-campaign context exists. |
| Store active campaign in process memory | Lost on restart and incompatible with multiple API processes. |

## 2. Campaign and membership storage placement

### Decision

Add campaign and campaign-membership persistence to the JDR data model, while allowing `core_users` to reference a default campaign.

### Rationale

Campaigns are business concepts of the JDR service, but the authenticated user has a default campaign used immediately after login. A string foreign key from `core_users.default_campaign_id` to the campaign table keeps the database relation without forcing `app/core` to import service code at runtime. Alembic already imports both core and JDR models before metadata inspection.

### Alternatives considered

| Alternative | Pourquoi rejetee |
|---|---|
| Put all campaign models in `app/core` | Blurs `core` with JDR business concepts and makes future services inherit JDR terminology. |
| Create a new `app/services/campaigns` service | Too broad for BD-4 and creates service boundaries before a second service actually needs them. |
| Store campaign only as config/env | Not per-user, not queryable, and not suitable for membership checks. |

## 3. Active campaign resolution

### Decision

Resolve active campaign in this order:

1. Use `core_users.default_campaign_id` when it points to a membership owned by the authenticated user.
2. Otherwise use the user's first membership ordered by `joined_at ASC`.
3. Otherwise return authenticated context with `active_campaign: null`.

### Rationale

This matches the frontend handoff and gives deterministic behavior when the default is absent or stale. Returning `active_campaign: null` for an authenticated user preserves the distinction between "not logged in" and "logged in but no JDR context".

### Alternatives considered

| Alternative | Pourquoi rejetee |
|---|---|
| Always fail 401/403 when no campaign exists | Conflates authentication with product context and prevents the frontend from showing a precise no-campaign state. |
| Let the frontend pass `campaign_id` | Trusts client input for tenancy and increases leakage risk. |
| Pick most recently joined campaign | Less stable for V1 seed/backfill and surprising after future membership changes. |

## 4. API-key compatibility

### Decision

Keep existing Bearer API-key behavior. For campaign-scoped JDR data, web sessions resolve through the linked `core_users` row; existing API-key requests resolve to the V1 default campaign when no user membership can be identified.

### Rationale

Current tests and machine/API workflows use Bearer keys. Removing that path would expand BD-4 into an auth migration. The frontend runtime requirement is cookie-driven `/auth/me`, but backend compatibility keeps the existing monolith stable while campaign scoping is introduced.

### Alternatives considered

| Alternative | Pourquoi rejetee |
|---|---|
| Require web sessions for all JDR endpoints | Breaks existing API clients and many current tests. |
| Migrate all ownership from API keys to users now | Large data/model refactor unrelated to the frontend BD-4 contract. |
| Ignore campaign scoping for API-key requests | Would leave a real bypass once multiple campaigns exist in DB. |

## 5. Campaign-owned data

### Decision

Add direct `campaign_id` ownership where lists and creations need direct filtering: JDR sessions, PJs/characters, and user membership queries. Dependent tables such as audio sources, transcriptions, chunks, mappings, artifacts, jobs, and session players inherit scope through their parent session or PJ.

### Rationale

Direct `campaign_id` on root aggregate tables keeps filters simple and indexable. Adding it to every child table would duplicate data and increase migration risk without improving the public contract. SQLAlchemy relationships and joins can enforce scope through the root aggregate when reading child resources; SQLAlchemy documents relationship loading and explicit joins as standard ORM patterns (https://docs.sqlalchemy.org/en/20/orm/queryguide/select.html).

### Alternatives considered

| Alternative | Pourquoi rejetee |
|---|---|
| Add `campaign_id` to every JDR table | More migration surface and more consistency invariants to maintain. |
| Keep only `gm_key_id` ownership | Does not support one GM across multiple campaigns. |
| Filter only in Python after loading rows | Less safe and less efficient than filtering in SQL. |

## 6. `/auth/me` cache behavior

### Decision

Return `Cache-Control: no-store` on `/services/jdr/auth/me`.

### Rationale

The response contains identity and authorization context. Preventing shared/browser cache reuse is the conservative default for auth context. OWASP's API Security guidance highlights broken authentication and authorization as top risks, so auth context should not be accidentally reused across users or stale memberships (https://owasp.org/API-Security/editions/2023/en/0x11-t10/).

### Alternatives considered

| Alternative | Pourquoi rejetee |
|---|---|
| Rely only on frontend TanStack Query caching | Front cache behavior does not control HTTP/shared caches. |
| Long-lived HTTP cache | Membership/role changes would stay stale and hard to reason about. |

## 7. OpenAPI and frontend contract

### Decision

Document `/services/jdr/auth/me` in backend OpenAPI and keep existing user-management request/response bodies stable.

### Rationale

The frontend regenerates API types from backend OpenAPI. FastAPI generates OpenAPI from route decorators and Pydantic schemas (https://fastapi.tiangolo.com/features/#automatic-docs), so adding explicit response models is the lowest-scope way to publish the contract.

### Alternatives considered

| Alternative | Pourquoi rejetee |
|---|---|
| Keep `/auth/me` undocumented and hand-write frontend types | Creates drift between runtime and client contract. |
| Change user-management contracts to include `campaign_id` | Front handoff explicitly says V1 bodies must not include `campaign_id`. |
