from collections.abc import AsyncIterator, Callable, Iterator

from pydantic_settings import BaseSettings
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


class Settings(BaseSettings):
    async_database_url: str = "sqlite+aiosqlite:///:memory:"
    database_url: str = "sqlite+pysqlite:///:memory:"
    session_generator: Callable[[], AsyncIterator[AsyncSession]] | None = None
    sync_session_generator: Callable[[], Iterator[Session]] | None = None


settings = Settings()
