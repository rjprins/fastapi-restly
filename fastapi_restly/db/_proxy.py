from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.orm import Session as SA_Session

from ..exc import RestlyConfigurationError
from ._globals import _fr_globals


@asynccontextmanager
async def open_async_session() -> AsyncGenerator[SA_AsyncSession]:
    """Open an async database session for use outside of request context.

    Resolves the same source as ``AsyncSessionDep``: a custom
    ``session_generator`` passed to :func:`fastapi_restly.configure` if one is
    configured, otherwise the built-in async session factory. (The request-only
    uncommitted-changes check is not armed here -- off-HTTP code owns its commit,
    exactly as a custom write route does.)

    Example::

        async with fr.open_async_session() as session:
            result = await session.execute(select(User))
    """
    generator = _fr_globals.session_generator
    if generator is not None:
        async with asynccontextmanager(generator)() as sess:
            yield sess
        return
    if _fr_globals.async_make_session is None:
        raise RestlyConfigurationError(
            "Call fr.configure() before using open_async_session()."
        )
    async with _fr_globals.async_make_session() as sess:
        yield sess


@contextmanager
def open_session() -> Generator[SA_Session]:
    """Open a sync database session for use outside of request context.

    Resolves the same source as ``SessionDep``: a custom
    ``sync_session_generator`` passed to :func:`fastapi_restly.configure` if one
    is configured, otherwise the built-in sync session factory. (The request-only
    uncommitted-changes check is not armed here -- off-HTTP code owns its commit,
    exactly as a custom write route does.)

    Example::

        with fr.open_session() as session:
            result = session.execute(select(User))
    """
    generator = _fr_globals.sync_session_generator
    if generator is not None:
        with contextmanager(generator)() as sess:
            yield sess
        return
    if _fr_globals.make_session is None:
        raise RestlyConfigurationError(
            "Call fr.configure() before using open_session()."
        )
    with _fr_globals.make_session() as sess:
        yield sess
