"""Top-level router for the JDR service.

Mounted at ``/services/jdr`` (CLAUDE.md §4.2 convention). Every route
attached here inherits the default dependencies declared on the router:

- ``require_api_key`` enforces a valid Bearer token (jalon 2).
- ``enforce_rate_limit`` applies a per-key sliding window (jalon 3).

User-story routes are added incrementally by US1..US5. Sub-routers
(``batch/router.py``, ``live/router.py``) are included from this file.

The two ``/me/*`` endpoints exposed to players (US4) are added directly
to this router rather than to a sibling: their authorisation is
expressed by ``Depends(require_player)`` at the route level, which
takes precedence and replaces the default ``require_api_key`` only by
*wrapping* it (require_player itself depends on require_api_key).
"""

from fastapi import APIRouter, Depends

from app.core.auth import require_api_key
from app.core.rate_limit import enforce_rate_limit

router = APIRouter(
    prefix="/services/jdr",
    tags=["jdr"],
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
)

# Sub-routers (batch / live) will be included as they are added by US1
# and US5. Keeping them out of this file until they exist so static
# analysis stays accurate.
