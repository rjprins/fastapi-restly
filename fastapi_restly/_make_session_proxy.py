from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Session as SA_Session
from sqlalchemy.orm import sessionmaker

from ._globals import fa_globals
from ._session import setup_async_database_connection, setup_database_connection


class AsyncSessionProxy:
    """
    Proxy for the global async_sessionmaker.
    This enables consistent global imports, e.g.:
        from fastapi_restly import AsyncSession

        with AsyncSession() as async_session: ...
    """

    def __call__(self) -> SA_AsyncSession:
        return self._get_async_sessionmaker()()

    def begin(self) -> SA_AsyncSession:
        return self._get_async_sessionmaker().begin()

    @property
    def kw(self):
        return self._get_async_sessionmaker().kw

    def _get_async_sessionmaker(self) -> async_sessionmaker[SA_AsyncSession]:
        if fa_globals.async_make_session is None:
            if not fa_globals.async_database_url:
                raise RuntimeError(
                    "Sessionmaker 'AsyncSession' is not initialized. "
                    "Call setup_async_database_connection() first."
                )
            else:
                setup_async_database_connection(fa_globals.async_database_url)
        return fa_globals.async_make_session


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

    def begin(self) -> SA_Session:
        return self._get_sessionmaker().begin()

    @property
    def kw(self):
        return self._get_sessionmaker().kw

    def _get_sessionmaker(self) -> sessionmaker[SA_Session]:
        if fa_globals.make_session is None:
            if not fa_globals.database_url:
                raise RuntimeError(
                    "Sessionmaker 'make_session' is not initialized. "
                    "Call setup_database_connection() first."
                )
            else:
                setup_database_connection(fa_globals.database_url)
        return fa_globals.make_session


Session = SessionProxy()
