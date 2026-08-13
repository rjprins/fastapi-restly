"""Configuration contract for the production-shaped SaaS example."""

import pytest
from app.settings import Settings
from pydantic import ValidationError


def test_settings_accept_asyncpg_and_validate_pool_bounds() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/saas",
        db_pool_size=5,
        db_max_overflow=10,
        _env_file=None,
    )

    assert str(settings.database_url).startswith("postgresql+asyncpg://")
    assert settings.db_pool_size == 5
    assert settings.db_max_overflow == 10


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite+aiosqlite:///saas.db",
        "postgresql://postgres:postgres@localhost:5432/saas",
    ],
)
def test_settings_reject_non_asyncpg_database_urls(database_url: str) -> None:
    with pytest.raises(ValidationError, match="postgresql\\+asyncpg"):
        Settings(database_url=database_url, _env_file=None)


@pytest.mark.parametrize(
    ("field", "value"), [("db_pool_size", 0), ("db_max_overflow", -1)]
)
def test_settings_reject_invalid_pool_bounds(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(
            database_url=(
                "postgresql+asyncpg://postgres:postgres@localhost:5432/saas"
            ),
            _env_file=None,
            **{field: value},
        )
