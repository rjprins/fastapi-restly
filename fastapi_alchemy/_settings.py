from typing import AsyncIterator

from pydantic_settings import BaseSettings
from sqlalchemy.ext.asyncio import AsyncSession


class Settings(BaseSettings):
    async_database_url: str = "sqlite+aiosqlite:///:memory:"
    session_generator: AsyncIterator[AsyncSession] | None = None


settings = Settings()
