# Import app.main to register views and call fr.configure() before fixtures run.
import app.main  # noqa: F401
import pytest

import fastapi_restly as fr

pytest_plugins = ["fastapi_restly.pytest_fixtures"]


@pytest.fixture(autouse=True)
async def use_in_memory_database():
    """Switch to a fresh in-memory SQLite database for each test."""
    fr.configure(async_database_url="sqlite+aiosqlite:///:memory:")
    async with fr.get_async_engine().begin() as conn:
        await conn.run_sync(fr.DataclassBase.metadata.create_all)
