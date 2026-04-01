# Import app.main to set up database connection before fixtures run
import asyncio
from pathlib import Path

import pytest

import app.main  # noqa: F401  -- registers fr.configure() before fixtures run
from app.main import app as saas_app

import fastapi_restly as fr

pytest_plugins = ["fastapi_restly.pytest_fixtures"]


async def _create_tables():
    """Create all tables asynchronously."""
    async with fr.get_async_engine().begin() as conn:
        await conn.run_sync(fr.DataclassBase.metadata.create_all)


# Delete the database file if it exists and create fresh tables
db_file = Path("saas.db")
if db_file.exists():
    db_file.unlink()

asyncio.run(_create_tables())


@pytest.fixture
def app():
    return saas_app
