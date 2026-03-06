from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Session as SA_Session
from sqlalchemy.orm import sessionmaker


class FRGlobals:
    async_database_url: str | None = None
    async_make_session: async_sessionmaker[SA_AsyncSession] | None = None
    database_url: str | None = None
    make_session: sessionmaker[SA_Session] | None = None


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
