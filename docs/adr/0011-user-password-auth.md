# ADR 0011 — Web user/password auth with server-side sessions

Date: 2026-05-27

## Status

Accepted

## Context

The JDR front needs browser login with `username + profile + password`, then a cookie usable with `credentials: "include"`. The previous temporary login was single-person and reused an API-token-like secret as a web password, which does not scale to multiple users.

We also need a first-run story for a fresh install: the user should not edit `.env` or run a side script to create the first account, and we should not ship a known default password. Hardcoded/default credentials are a documented weakness in OWASP guidance: https://owasp.org/www-community/vulnerabilities/Use_of_hard-coded_password

## Decision

- Add `core_users` for web accounts with `username`, `profile`, password hash, status and timestamps.
- Add `core_web_sessions` for opaque session tokens stored only as hashes server-side.
- Add first-run setup:
  - `GET /services/jdr/auth/setup/status`
  - `POST /services/jdr/auth/setup`
  - setup is accepted only while no user exists.
- Add web login/logout:
  - `POST /services/jdr/auth/login`
  - `POST /services/jdr/auth/logout`
  - success sets `session=...; HttpOnly; Path=/; SameSite=Lax`.
- Keep existing Bearer API-key auth for machine clients. Bearer remains priority when present; cookie auth is fallback.
- For compatibility with existing JDR ownership tables, each web `gm` receives an internal non-exposed `jdr_api_keys` row. Existing `jdr_sessions.gm_key_id` FKs can therefore keep pointing to `jdr_api_keys`.

## Alternatives Considered

- Default `admin/admin`: rejected because it creates a known credential at install time.
- JWT access tokens: rejected for MVP because immediate revocation and logout are simpler with server-side sessions.
- Removing API keys now: rejected because machine clients and existing tests rely on them.
- Refactoring all JDR ownership FKs from `jdr_api_keys` to `core_users`: rejected as too broad for this feature.

## Consequences

- Fresh installs can be initialized from the front without scripts or env edits.
- Sessions are revocable by deleting server-side state or setting `revoked_at`.
- The auth model temporarily has two identity stores: API keys for machine/JDR ownership, users for browser login.
- In a strict multi-process deployment, first-run setup should later be reinforced with a database-level lock or invariant. The current process lock is enough for the current single-process local deployment posture.

## References

- OWASP hard-coded password weakness: https://owasp.org/www-community/vulnerabilities/Use_of_hard-coded_password
- OWASP password storage cheat sheet: https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- HTTP cookies overview: https://developer.mozilla.org/en-US/docs/Web/HTTP/Cookies
