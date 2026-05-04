from ._globals import RestlyContext, get_fr_globals
from ._proxy import async_open_session, open_session
from ._session import (
    AsyncSessionDep,
    SessionDep,
    activate_savepoint_only_mode,
    async_generate_session,
    configure,
    deactivate_savepoint_only_mode,
    generate_session,
    get_async_engine,
    get_engine,
)

# Public API for ``fastapi_restly.db``.
#
# Session generator internals live in private modules; use ``configure`` and
# ``RestlyContext`` to configure or isolate runtime state.
__all__ = [
    # Session context managers
    "async_open_session",
    "open_session",
    # FastAPI dependencies
    "AsyncSessionDep",
    "SessionDep",
    # Engine access
    "get_async_engine",
    "get_engine",
    # Setup
    "configure",
    # Savepoint mode
    "activate_savepoint_only_mode",
    "deactivate_savepoint_only_mode",
    # Globals
    "RestlyContext",
    "get_fr_globals",
]
