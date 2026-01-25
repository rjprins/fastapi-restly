from ._globals import FRGlobals, fr_globals
from ._proxy import (
    AsyncSession,
    AsyncSessionProxy,
    FRAsyncSession,
    FRAsyncSessionProxy,
    FRSession,
    FRSessionProxy,
    Session,
    SessionProxy,
)
from ._session import (
    AsyncSessionDep,
    SessionDep,
    activate_savepoint_only_mode,
    async_generate_session,
    deactivate_savepoint_only_mode,
    generate_session,
    setup_async_database_connection,
    setup_database_connection,
)

__all__ = [
    # New names (preferred)
    "FRAsyncSession",
    "FRAsyncSessionProxy",
    "FRSession",
    "FRSessionProxy",
    # Dependencies
    "AsyncSessionDep",
    "SessionDep",
    # Backwards compatibility (deprecated)
    "AsyncSession",
    "AsyncSessionProxy",
    "Session",
    "SessionProxy",
    # Other exports
    "FRGlobals",
    "activate_savepoint_only_mode",
    "async_generate_session",
    "deactivate_savepoint_only_mode",
    "fr_globals",
    "generate_session",
    "setup_async_database_connection",
    "setup_database_connection",
]
