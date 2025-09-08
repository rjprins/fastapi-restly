"""Pytest configuration and shared fixtures."""

import asyncio
import pytest

import fastapi_restly as fd
from fastapi_restly._globals import fa_globals

pytest_plugins = ["fastapi_restly.pytest_fixtures"]


@pytest.fixture(autouse=True)
def reset_metadata():
    """Reset SQLAlchemy metadata to prevent table redefinition conflicts."""
    fd.Base.metadata.clear()


@pytest.fixture(autouse=True)
def setup_database_connection():
    fd.setup_async_database_connection("sqlite+aiosqlite:///:memory:")


def create_tables():
    async def create_tables():
        engine = fd.AsyncSession.kw["bind"]
        async with engine.begin() as conn:
            await conn.run_sync(fd.Base.metadata.create_all)

    asyncio.run(create_tables())
