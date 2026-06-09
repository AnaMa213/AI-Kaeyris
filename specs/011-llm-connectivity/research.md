# Research: BD-11 LLM Connectivity

## Decision 1: Keep the existing OpenAI-compatible adapter

**Decision**: Fix BD-11 within `app/adapters/llm.py`, `app/core/config.py`, and
existing job behavior instead of adding a new provider SDK or provider-specific
service code.

**Rationale**: The current adapter already defines the project boundary for LLM
calls and maps SDK connection failures to project errors. The constitution also
requires business code to avoid vendor-specific dependencies.

**Alternatives considered**:

- Add provider-specific code in `app/services/jdr`: rejected because it breaks
  separation of concerns.
- Add a second LLM abstraction: rejected because BD-11 is connectivity/failure
  handling, not a new business capability.

## Decision 2: Treat Docker-reachable LLM settings as the config boundary

**Decision**: The worker must use the same env-driven LLM settings as the API,
and `LLM_BASE_URL` must be documented/validated as a URL reachable from inside
the worker container.

**Rationale**: The observed failure happens inside the RQ worker while calling
the provider. Docker `localhost` inside a container points to that container,
not the host or another service, so the operational contract must be explicit.

**Alternatives considered**:

- Hardcode a provider URL in code: rejected because it violates 12-Factor config
  rules and makes local/provider swaps harder.
- Add a health-check endpoint that calls the LLM on every API startup: rejected
  for BD-11 because it can block startup and is broader than the reported
  summary-job failure.

## Decision 3: Preserve the existing REST contract and guarantee failure text

**Decision**: Keep `POST /services/jdr/sessions/{session_id}/artifacts/summary`
and `GET /services/jdr/jobs/{job_id}` unchanged, but make/keep the guarantee
that a failed LLM summary job returns `status="failed"` with a non-empty
`failure_reason`.

**Rationale**: The frontend already polls the job endpoint. BD-11 requires
visible failure, not a new UI/backend contract.

**Alternatives considered**:

- Add a dedicated LLM status endpoint: rejected as unnecessary for the current
  frontend handoff.
- Persist a new failure table: rejected because current RQ job metadata and
  existing job schema already expose failure state.

## Decision 4: Test with controlled adapter/network failure

**Decision**: Use deterministic tests for adapter/job failure mapping and route
projection, plus a manual quickstart for real Docker/provider connectivity.

**Rationale**: Unit and integration tests should not depend on an external LLM
being available or on real credentials. The manual check covers the deployed
Docker networking path.

**Alternatives considered**:

- CI test against the real cloud provider: rejected because it requires secrets,
  network availability, and paid/external state.
- Only manual testing: rejected because the failure contract is public API
  behavior and should be guarded by pytest.

## Decision 5: No migration for BD-11

**Decision**: Do not add or alter database schema unless implementation proves
the current job/artifact projection cannot meet the spec.

**Rationale**: The reported problem is connectivity and failure projection, not
missing persistent data shape. Existing `JobOut.failure_reason` and summary
artifact rows are sufficient for the required behavior.

**Alternatives considered**:

- Add `llm_error_code` or provider diagnostics columns: rejected as premature
  operational detail for this jalon.
