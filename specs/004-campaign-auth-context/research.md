# Research: Campaign Auth Context

**Phase 0 du `/speckit-plan`**. Decisions below resolve the design unknowns from BD-4 before generating implementation tasks.

## Decision 1: V1 role vocabulary remains `gm | player`

**Decision**: Return `active_campaign.role` as `gm` or `player`.

**Rationale**: The BD-4 handoff uses `mj` in the narrative, but the current frontend runtime type expects `gm | player`. Keeping `gm` avoids breaking the already delivered frontend session hook and remains consistent with the backend `Profile.GM` and JDR `Role.GM` values. This is a compatibility decision, not a product vocabulary preference.

**Alternatives considered**:

- `mj | player`: closer to French UI language, but would break the typed frontend contract unless the frontend changed too.
- Return both `gm` and `mj`: creates ambiguity and duplicate authorization vocabulary for no V1 value.

## Decision 2: `/services/jdr/auth/me` is based on web sessions

**Decision**: The current-context endpoint resolves a browser user from the `session` cookie and returns that user's active campaign context. Missing, expired, revoked, or deleted-user sessions return the existing unauthorized Problem Details shape.

**Rationale**: The endpoint exists for the web front after login. API keys do not always map to a `core_users` row, and inventing a user identity for legacy API keys would weaken the contract. Existing API-key clients remain supported on JDR operational endpoints through `require_api_key`; they do not need `/auth/me`.

**Alternatives considered**:

- Reuse `require_api_key` directly and accept Bearer tokens on `/auth/me`: convenient, but an API key lacks stable public user identity and default campaign preference.
- Add a new token type or JWT: outside BD-4 and explicitly rejected by previous auth specs.

## Decision 3: Campaign tables live in the JDR service domain

**Decision**: Add `Campaign` and `CampaignMember` to the JDR service model layer, while adding only a nullable `default_campaign_id` bridge to `core_users`.

**Rationale**: BD-4 defines campaign as the JDR tenancy unit, not as a platform-wide organization. Keeping campaign entities in `app/services/jdr` respects the modular monolith boundary. The `core_users.default_campaign_id` bridge is a small cross-boundary pointer needed to resolve the active context for a logged-in user; it mirrors the existing `core_users.api_key_id` compatibility bridge.

**Alternatives considered**:

- Put all campaign tables in `app/core`: over-generalizes a JDR-only concept and would make future services inherit a domain they may not need.
- Avoid `default_campaign_id` entirely and always choose the first membership: simpler schema, but loses the explicit default-selection semantics required by BD-4 and future campaign switching.

## Decision 4: V1 default campaign is created/backfilled by adoption path

**Decision**: The migration/adoption path creates one default campaign when users already exist, attaches existing users to it, and points their default campaign to it. If the database is empty, first-run setup creates the first GM, default campaign, and first membership atomically.

**Rationale**: Existing deployments must keep working after migration, and empty installs must still work without a script or `.env` edit. This preserves the current first-run setup UX while making `/auth/me` useful immediately after setup.

**Alternatives considered**:

- Require a manual seed script: operator friction and easy to forget; violates the current no-manual-first-user setup behavior.
- Create a campaign row with no owner in an empty DB: weakens ownership invariants and creates cleanup questions.

## Decision 5: Scope primary JDR aggregate roots by campaign

**Decision**: Add campaign scope to `jdr_sessions` and `jdr_pjs`, then derive scope for audio, transcriptions, mappings, chunks, session players, artifacts, and jobs through their owning session or PJ.

**Rationale**: Sessions and characters are the user-visible roots. Most other JDR tables are child rows keyed by `session_id` or `pj_id`, so duplicating `campaign_id` everywhere would add denormalized state and more sync risk. Filtering at repository boundaries keeps route code readable and consistent.

**Alternatives considered**:

- Add `campaign_id` to every JDR table: makes queries direct but increases migration size and consistency rules.
- Filter only by existing `gm_key_id`: preserves current owner model but does not support campaign-level membership or future multi-GM campaigns.

## Decision 6: Existing API-key operations stay compatible

**Decision**: API-key-authenticated GM operations resolve to the V1 default campaign when no web user context exists. Player API keys resolve scope from their linked PJ. Web-session operations resolve scope from the user's active campaign membership.

**Rationale**: Current CLI/manual API usage should not break simply because web campaign context was introduced. At the same time, the browser path gets the strict membership-based scope needed by BD-4.

**Alternatives considered**:

- Reject API-key operations until they are mapped to users: secure but breaking for existing machine clients.
- Ignore campaign scope for API-key operations: preserves compatibility but creates a bypass of the new isolation model.

## Decision 7: No public campaign management in BD-4

**Decision**: Do not add create/list/update/delete campaign endpoints, campaign switch endpoints, tenants, or organizations in this feature.

**Rationale**: The spec and handoff explicitly frame V1 as product-single-campaign. Extra management surfaces would increase auth, validation, and UI scope before there is a user workflow.

**Alternatives considered**:

- Add admin-only campaign CRUD now: tempting for testing and future-proofing, but YAGNI for BD-4.
- Add a default-campaign patch endpoint: useful later for switching, but no V1 UI consumes it.

## Sources

- Project constitution and locked stack: `AGENTS.md`.
- 12-Factor config and backing-service principles: https://12factor.net/config and https://12factor.net/backing-services.
- OWASP API Security Top 10 authorization risk framing: https://owasp.org/API-Security/editions/2023/en/0x11-t10/.
- RFC 9457 Problem Details error format: https://www.rfc-editor.org/rfc/rfc9457.
- SQLAlchemy relationship and enum behavior used by the existing codebase: https://docs.sqlalchemy.org/en/20/orm/basic_relationships.html and https://docs.sqlalchemy.org/en/20/core/type_basics.html#sqlalchemy.types.Enum.
