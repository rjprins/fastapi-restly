from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Session as SA_Session
from sqlalchemy.orm import sessionmaker


class FRGlobals:
    __slots__ = (
        "async_database_url",
        "async_make_session",
        "database_url",
        "make_session",
        "session_generator",
        "sync_session_generator",
    )

    async_database_url: str | None
    async_make_session: async_sessionmaker[SA_AsyncSession] | None
    database_url: str | None
    make_session: sessionmaker[SA_Session] | None
    session_generator: Callable[[], AsyncIterator[SA_AsyncSession]] | None
    sync_session_generator: Callable[[], Iterator[SA_Session]] | None

    def __init__(self) -> None:
        self.async_database_url = None
        self.async_make_session = None
        self.database_url = None
        self.make_session = None
        self.session_generator = None
        self.sync_session_generator = None


_default_globals = FRGlobals()
_fr_globals_ctx: ContextVar[FRGlobals | None] = ContextVar(
    "fastapi_restly_db_globals", default=None
)


def get_fr_globals() -> FRGlobals:
    return _fr_globals_ctx.get() or _default_globals


@contextmanager
def use_fr_globals(globals_obj: FRGlobals) -> Iterator[None]:
    token = _fr_globals_ctx.set(globals_obj)
    try:
        yield
    finally:
        _fr_globals_ctx.reset(token)


class _FRGlobalsProxy:
    def __getattr__(self, name: str):
        return getattr(get_fr_globals(), name)

    def __setattr__(self, name: str, value):
        setattr(get_fr_globals(), name, value)


fr_globals = _FRGlobalsProxy()
