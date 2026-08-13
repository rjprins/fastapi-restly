"""Validated environment settings for the SaaS example."""

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or ``.env``."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://postgres:postgres@localhost:5432/saas"
    )
    db_pool_size: int = Field(default=5, gt=0)
    db_max_overflow: int = Field(default=10, ge=0)

    @field_validator("database_url")
    @classmethod
    def require_asyncpg(cls, value: PostgresDsn) -> PostgresDsn:
        """Keep the application and async Alembic environment on one driver."""
        if value.scheme != "postgresql+asyncpg":
            raise ValueError("DATABASE_URL must use postgresql+asyncpg")
        return value

    @property
    def sqlalchemy_database_url(self) -> str:
        """Return the validated URL in the form SQLAlchemy accepts."""
        return str(self.database_url)
