"""Application-owned SQLAlchemy resources."""

from sqlalchemy.ext.asyncio import create_async_engine

from .settings import Settings

settings = Settings()

engine = create_async_engine(
    settings.sqlalchemy_database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
)
