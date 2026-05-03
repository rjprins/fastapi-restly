from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator, Iterator

from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.orm import Session as SA_Session

from ._globals import fr_globals


@asynccontextmanager
async def async_open_session() -> AsyncIterator[SA_AsyncSession]:
    """Open an async database session for use outside of request context.

    Example::

        async with fr.async_open_session() as session:
            result = await session.execute(select(User))
    """
    if fr_globals.async_make_session is None:
        raise RuntimeError(
            "Call fr.configure() before using async_open_session()."
        )
    async with fr_globals.async_make_session() as sess:
        yield sess


@contextmanager
def open_session() -> Iterator[SA_Session]:
    """Open a sync database session for use outside of request context.

    Example::

        with fr.open_session() as session:
            result = session.execute(select(User))
    """
    if fr_globals.make_session is None:
        raise RuntimeError(
            "Call fr.configure() before using open_session()."
        )
    with fr_globals.make_session() as sess:
        yield sess
