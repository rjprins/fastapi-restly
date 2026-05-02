# Import blog.main to set up database connection before fixtures run
import blog.main  # noqa: F401
import pytest

import fastapi_restly as fr

pytest_plugins = ["fastapi_restly.pytest_fixtures"]


@pytest.fixture(autouse=True)
def use_in_memory_database():
    """Switch to a fresh in-memory SQLite database for each test."""
    fr.configure(async_database_url="sqlite+aiosqlite:///:memory:")
