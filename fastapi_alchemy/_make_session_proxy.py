from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker

from ._globals import fa_globals
from ._session import async_setup_database_connection, setup_database_connection


class AsyncMakeSessionProxy:
    def _get_real_make_session(self) -> async_sessionmaker:
        if not fa_globals.async_make_session:
            if not fa_globals.async_database_url:
                raise RuntimeError(
                    "Sessionmaker 'async_make_session' is not initialized. "
                    "Call async_setup_database_connection() first."
                )
            else:
                async_setup_database_connection(fa_globals.async_database_url)
        return fa_globals.async_make_session

    def __call__(self) -> AsyncSession:
        return self._get_real_make_session()()

    def begin(self) -> AsyncSession:
        return self._get_real_make_session().begin()


async_make_session: async_sessionmaker[AsyncSession] = AsyncMakeSessionProxy()


class MakeSessionProxy:
    def _get_real_make_session(self) -> sessionmaker:
        if not fa_globals.make_session:
            if not fa_globals.database_url:
                raise RuntimeError(
                    "Sessionmaker 'make_session' is not initialized. "
                    "Call setup_database_connection() first."
                )
            else:
                setup_database_connection(fa_globals.database_url)
        return fa_globals.make_session

    def __call__(self) -> Session:
        return self._get_real_make_session()()

    def begin(self) -> Session:
        return self._get_real_make_session().begin()


make_session: sessionmaker[Session] = MakeSessionProxy()
