from collections.abc import AsyncIterator, Callable, Iterator
from contextvars import ContextVar, Token
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Session as SA_Session
from sqlalchemy.orm import sessionmaker


class RestlyContext:
    """Container for Restly runtime state.

    Most applications use the default process-wide context by calling
    ``fr.configure(...)`` directly. Create a ``RestlyContext`` when you need
    isolated Restly state in the same process, for example for tests or
    multiple FastAPI apps.
    """

    __slots__ = (
        "async_database_url",
        "async_make_session",
        "database_url",
        "make_session",
        "session_generator",
        "sync_session_generator",
    )

    async_database_url: str | None
    async_make_session: async_sessionmaker[Any] | None
    database_url: str | None
    make_session: sessionmaker[Any] | None
    session_generator: Callable[[], AsyncIterator[SA_AsyncSession]] | None
    sync_session_generator: Callable[[], Iterator[SA_Session]] | None

    def __init__(self) -> None:
        self.async_database_url = None
        self.async_make_session = None
        self.database_url = None
        self.make_session = None
        self.session_generator = None
        self.sync_session_generator = None

    def __enter__(self) -> "RestlyContext":
        token = _restly_context_ctx.set(self)
        _restly_context_token_stack.set(_restly_context_token_stack.get() + (token,))
        return self

    def __exit__(self, *exc_info: object) -> None:
        token_stack = _restly_context_token_stack.get()
        if not token_stack:
            raise RuntimeError("RestlyContext was exited without being entered.")
        token = token_stack[-1]
        _restly_context_token_stack.set(token_stack[:-1])
        _restly_context_ctx.reset(token)


_default_context = RestlyContext()
_restly_context_ctx: ContextVar[RestlyContext | None] = ContextVar(
    "fastapi_restly_context", default=None
)
_restly_context_token_stack: ContextVar[tuple[Token[RestlyContext | None], ...]] = (
    ContextVar("fastapi_restly_context_token_stack", default=())
)


def _get_restly_context() -> RestlyContext:
    return _restly_context_ctx.get() or _default_context


def get_fr_globals() -> RestlyContext:
    return _get_restly_context()


class _FRGlobalsProxy:
    def __getattr__(self, name: str):
        return getattr(_get_restly_context(), name)

    def __setattr__(self, name: str, value):
        setattr(_get_restly_context(), name, value)


_fr_globals = _FRGlobalsProxy()
