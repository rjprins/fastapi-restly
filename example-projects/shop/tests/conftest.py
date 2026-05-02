# Import shop.main to set up database connection before fixtures run
import pytest
import shop.main  # noqa: F401

import fastapi_restly as fr

pytest_plugins = ["fastapi_restly.pytest_fixtures"]


@pytest.fixture(autouse=True)
def use_in_memory_database():
    """Switch to a fresh in-memory SQLite database for each test."""
    fr.configure(async_database_url="sqlite+aiosqlite:///:memory:")
