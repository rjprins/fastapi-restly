from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import sessionmaker


class FA_Globals:
    async_database_url: str | None = None
    async_make_session: async_sessionmaker | None = None
    database_url: str | None = None
    make_session: sessionmaker | None = None


fa_globals = FA_Globals()
