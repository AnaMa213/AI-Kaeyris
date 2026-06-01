# ADR 0012 — Campaign Auth Context

Date: 2026-05-31

## Status

Accepted

## Context

The web frontend expects `GET /services/jdr/auth/me` after login. The previous user/password auth feature delivered setup, login, logout, and user management, but did not expose the current JDR campaign context. BD-4 also needs JDR data isolation by campaign without adding campaign CRUD or changing existing request bodies.

## Decision

- Add `jdr_campaigns` and `jdr_campaign_members` inside the JDR service domain.
- Add only `core_users.default_campaign_id` to core auth state.
- Create/adopt one V1 default campaign during first-run setup or migration/backfill.
- Return `/services/jdr/auth/me` from the web session cookie only.
- Scope JDR sessions and PJs with server-derived `campaign_id`; clients never send it.
- Keep Bearer API keys working. Bearer remains higher priority than cookies.

## Consequences

- The frontend can replace its `/auth/me` mock with a real endpoint.
- Existing login/setup/users contracts stay campaign-free.
- V1 remains product-single-campaign, while the membership table allows future multi-campaign work.
- Campaign CRUD, campaign switching, tenants, OAuth, and JWT remain out of scope.

## Alternatives Rejected

- Put campaigns in `app/core`: too broad for a JDR-only concept.
- Accept Bearer API keys on `/auth/me`: legacy keys do not always identify a public web user.
- Add campaign CRUD now: useful later, but YAGNI for BD-4.
