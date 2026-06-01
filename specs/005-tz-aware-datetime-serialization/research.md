# Research: Timezone-Aware Datetime Serialization

## Decision 1: Normalize response datetimes to aware UTC during serialization

**Decision**: Every non-empty datetime emitted by public response schemas should be serialized as an aware UTC ISO-8601 value. Naive values are treated as UTC before formatting; aware non-UTC values are converted to UTC before formatting.

**Rationale**: The user-facing bug is a response contract issue: clients receive strings without timezone information and JavaScript interprets them as local time. Python's `datetime.isoformat()` includes an offset only when the value has timezone information, so normalizing just before JSON serialization directly addresses the missing suffix. Source: Python datetime docs, `datetime.isoformat()` examples and aware/naive model: https://docs.python.org/3/library/datetime.html#datetime.datetime.isoformat

**Alternatives considered**:

- Preserve original offsets in responses. Rejected for now because the backend already treats UTC as canonical and the handoff accepts either `Z` or `+00:00`.
- Emit local server timezone. Rejected because server-local time is environment-dependent and would violate the API contract.
- Keep naive output and rely on frontend parsing workaround. Rejected because the backend contract remains ambiguous.

## Decision 2: Use Pydantic v2 serialization hooks instead of route-level formatting

**Decision**: Implement a small shared helper plus Pydantic v2 serialization wiring for response models that expose datetime fields.

**Rationale**: Pydantic v2 supports field and model serialization customization, which keeps JSON-shape concerns near schemas and avoids duplicating date formatting in every route. Source: Pydantic serialization docs: https://docs.pydantic.dev/latest/concepts/serialization/

**Alternatives considered**:

- Format each datetime directly in route handlers. Rejected because it spreads a cross-cutting contract across endpoints and is easy to miss.
- Replace FastAPI response serialization globally. Rejected because it is broader than needed and harder to test locally.
- Add a third-party datetime library. Rejected by YAGNI; the Python standard library is enough.

## Decision 3: Keep input compatibility and interpret naive inputs as UTC

**Decision**: Accepted datetime inputs remain compatible with existing clients: `Z`, numeric offsets, and naive values. Naive inputs are interpreted as UTC before storage or response.

**Rationale**: The feature spec requires no input contract break. Python 3.12 supports parsing ISO-8601 datetimes with offsets, and Pydantic already validates datetime fields for request models. Normalizing naive inputs at the boundary prevents future writes from reintroducing ambiguity.

**Alternatives considered**:

- Reject naive datetime inputs. Rejected because it would break the explicit compatibility requirement.
- Interpret naive inputs as server local time. Rejected because server-local interpretation depends on deployment environment.

## Decision 4: Do not plan a database migration as the default fix

**Decision**: The default implementation should not add an Alembic migration for datetime columns. Add one only if tests prove stored values cannot be normalized correctly at read/serialization time.

**Rationale**: Current models and migrations already declare `DateTime(timezone=True)` in the inspected codebase. The handoff's probable cause may describe an earlier state or a SQLite behavior where timezone awareness is not preserved on round trip. A response-boundary helper is smaller, reversible, and directly testable.

**Alternatives considered**:

- Alter every datetime column again. Rejected because it risks churn without evidence and may be redundant.
- Store timezone names alongside timestamps. Rejected as out of scope; the feature requires explicit instant serialization, not user timezone preference.

## Decision 5: Cover representative public payloads, not every schema in one test

**Decision**: Add a focused helper/unit test and route-level regression tests for session create/detail/list, PJ create/list, users list/create, and auth/me where available.

**Rationale**: A helper test catches formatting rules exhaustively; route tests prove FastAPI/Pydantic integration on public contracts. This follows the project's test pyramid preference while still protecting the endpoints named by the handoff.

**Alternatives considered**:

- Add a single broad snapshot test over all routes. Rejected because setup would be fragile and failures would be noisy.
- Only unit-test the helper. Rejected because the original bug is visible in HTTP JSON responses.

## Sources

- Python `datetime` documentation: https://docs.python.org/3/library/datetime.html
- Pydantic v2 serialization documentation: https://docs.pydantic.dev/latest/concepts/serialization/
- FastAPI JSON compatible encoder documentation: https://fastapi.tiangolo.com/tutorial/encoder/
