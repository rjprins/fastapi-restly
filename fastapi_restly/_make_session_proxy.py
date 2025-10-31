import contextlib
from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio.session import _AsyncSessionContextManager
from sqlalchemy.orm import Session as SA_Session, sessionmaker

from ._globals import fr_globals


class AsyncSessionProxy:
    """
    Proxy for the global async_sessionmaker.
    This enables consistent global imports, e.g.:
        from fastapi_restly import AsyncSession

        async with AsyncSession() as session: ...
    """

    def __call__(self) -> SA_AsyncSession:
        return self._get_async_sessionmaker()()

    def begin(self) -> _AsyncSessionContextManager[SA_AsyncSession]:
        return self._get_async_sessionmaker().begin()

    @property
    def kw(self) -> dict:
        return self._get_async_sessionmaker().kw

    def _get_async_sessionmaker(self) -> async_sessionmaker[SA_AsyncSession]:
        if fr_globals.async_make_session is None:
            raise RuntimeError(
                "Sessionmaker 'AsyncSession' is not initialized. "
                "Call setup_async_database_connection() first."
            )
        return fr_globals.async_make_session


AsyncSession = AsyncSessionProxy()


class SessionProxy:
    """
    Proxy for the global sessionmaker.
    This enables consistent global imports, e.g.:
        from fastapi_restly import Session

        with Session() as session: ...
    """

    def __call__(self) -> SA_Session:
        return self._get_sessionmaker()()

    def begin(self) -> contextlib.AbstractContextManager[SA_Session]:
        return self._get_sessionmaker().begin()

    @property
    def kw(self) -> dict:
        return self._get_sessionmaker().kw

    def _get_sessionmaker(self) -> sessionmaker[SA_Session]:
        if fr_globals.make_session is None:
            raise RuntimeError(
                "Sessionmaker 'make_session' is not initialized. "
                "Call setup_database_connection() first."
            )
        return fr_globals.make_session


Session = SessionProxy()
