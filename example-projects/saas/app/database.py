"""Application-owned SQLAlchemy resources."""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .settings import Settings


def create_engine_from(settings: Settings) -> AsyncEngine:
    """Build the application's pooled asyncpg engine from validated settings."""
    return create_async_engine(
        settings.sqlalchemy_database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )
