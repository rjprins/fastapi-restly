from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Session as SA_Session
from sqlalchemy.orm import sessionmaker


class FRGlobals:
    async_database_url: str | None = None
    async_make_session: async_sessionmaker[SA_AsyncSession] | None = None
    database_url: str | None = None
    make_session: sessionmaker[SA_Session] | None = None


fr_globals = FRGlobals()
