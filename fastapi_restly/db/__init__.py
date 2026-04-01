from ._globals import FRGlobals, fr_globals, get_fr_globals, use_fr_globals
from ._proxy import async_session, session
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

__all__ = [
    # Session context managers
    "async_session",
    "session",
    # Dependencies
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
    # Internal generators (for custom session_generator settings)
    "async_generate_session",
    "generate_session",
]
