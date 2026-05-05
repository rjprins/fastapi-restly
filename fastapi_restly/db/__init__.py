from ._proxy import open_async_session, open_session
from ._session import (
    AsyncSessionDep,
    SessionDep,
    activate_savepoint_only_mode,
    configure,
    deactivate_savepoint_only_mode,
    get_async_engine,
    get_engine,
)

# Public API for ``fastapi_restly.db``.
#
# Session generator internals live in private modules; use ``configure`` to
# configure the process-wide Restly runtime state.
__all__ = [
    # Session context managers
    "open_async_session",
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
]
