from ._globals import FRGlobals, fr_globals, get_fr_globals, use_fr_globals
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
# ``async_generate_session`` and ``generate_session`` remain importable for
# advanced users (and existing tests) who plug a custom session generator
# into ``fr_globals``, but they are not part of the supported public API
# and may move into a private module in a future release.
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
    "FRGlobals",
    "fr_globals",
    "get_fr_globals",
    "use_fr_globals",
]
