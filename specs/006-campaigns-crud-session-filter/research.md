# Research: Campaigns CRUD and Session Campaign Filter

## Decision 1: Reuse BD-4 campaign tables and helpers

**Decision**: Build BD-6 on top of the existing `Campaign`, `CampaignMember`, `User.default_campaign_id`, and campaign-context helpers from BD-4.

**Rationale**: The current data model already represents campaign membership, role, default campaign, and session campaign ownership. Reusing it keeps the feature inside the modular monolith and avoids duplicating scope logic.

**Alternatives considered**:

- Create a new campaign management subsystem: rejected as premature for a single JDR feature.
- Add a separate "selected campaign" runtime store: rejected because BD-4 already has default/active campaign behavior.

## Decision 2: Campaign CRUD belongs to the JDR service surface

**Decision**: Expose campaign management under the JDR service contract and implement it alongside existing JDR routes, schemas, logic, and repositories.

**Rationale**: Campaigns are currently meaningful only for RPG sessions, memberships, and characters. Keeping the surface local preserves the project’s service-folder ownership rule and avoids making `app/core/` responsible for feature behavior.

**Alternatives considered**:

- Put campaigns in `app/core/`: rejected because campaigns are not a platform-wide concern yet.
- Create `app/services/campaigns/`: rejected because it would introduce cross-service JDR dependencies immediately.

## Decision 3: Authorization is membership-based, with GM-only mutations

**Decision**: Campaign read/list requires membership. Campaign update/delete and session creation in a campaign require `gm` role for that campaign.

**Rationale**: The handoff defines campaign roles as `gm` and `player`; scope must come from server-side membership rather than trusting client-provided identifiers. This aligns with OWASP API authorization guidance: object-level authorization must be checked for each accessed object. Source: https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/

**Alternatives considered**:

- Allow any member to update campaigns: rejected because players should not control shared campaign metadata.
- Allow Bearer GM keys to bypass campaign membership: rejected for web campaign CRUD; BD-6 is driven by authenticated web campaign context.

## Decision 4: New sessions require explicit `campaign_id`

**Decision**: `POST /services/jdr/sessions` must require a campaign identifier in the request body for BD-6, while the list endpoint keeps unfiltered behavior for backward compatibility.

**Rationale**: The frontend pivot needs deterministic campaign selection. Creation without an explicit campaign would keep relying on active/default state and make it easy to write sessions to the wrong campaign.

**Alternatives considered**:

- Continue deriving campaign from `active_campaign`: rejected because the frontend now has a selected campaign concept and must be able to create in that campaign.
- Make `campaign_id` optional on create for compatibility: rejected by BD-6 AC3, which requires validation failure when absent.

## Decision 5: Refuse campaign deletion when sessions exist

**Decision**: Deleting a campaign with one or more sessions returns a conflict and preserves data.

**Rationale**: The handoff marks deletion behavior as needing product confirmation but proposes this as the V1 assumption. It is safer because session history is the primary value of the JDR service, and accidental cascade deletion would be hard to recover without a restore workflow.

**Alternatives considered**:

- Cascade-delete sessions and artifacts: rejected as too destructive for V1.
- Soft-delete campaigns: rejected as extra lifecycle scope not requested by BD-6.

## Decision 6: Keep PJs user-global in BD-6

**Decision**: Do not add campaign-scoped PJ behavior to public endpoints in BD-6.

**Rationale**: The handoff explicitly confirms PJs remain global to the user for V1. This avoids widening BD-6 into a larger character-management migration. The existing transitional `Pj.campaign_id` from BD-4 should not drive new campaign-scoped UX in this feature.

**Alternatives considered**:

- Scope PJs by campaign now: rejected because the handoff names it as BD-7 if product direction changes.

## Decision 7: Date serialization follows BD-5

**Decision**: Campaign response date fields use the existing explicit timezone JSON serialization contract.

**Rationale**: BD-5 established the public contract for datetime fields. Campaign `created_at` and `last_session_at` are new datetime outputs and must not reintroduce timezone-naive JSON.

**Alternatives considered**:

- Rely on default serializer behavior: rejected because BD-5 fixed that exact class of bug.

## References

- FastAPI response models and validation concepts: https://fastapi.tiangolo.com/tutorial/response-model/
- Pydantic field validation and constraints: https://docs.pydantic.dev/latest/concepts/fields/
- SQLAlchemy relationship/query patterns: https://docs.sqlalchemy.org/en/20/orm/queryguide/select.html
- OWASP API Security 2023, Broken Object Level Authorization: https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/
