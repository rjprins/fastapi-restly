# Import blog.main to set up database connection before fixtures run
import blog.main  # noqa: F401
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr

pytest_plugins = ["fastapi_restly.pytest_fixtures"]


@pytest.fixture(autouse=True)
def use_in_memory_database():
    """Switch to a fresh in-memory SQLite database for each test."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    fr.configure(engine=engine)
    fr.DataclassBase.metadata.create_all(engine)
    yield
    engine.dispose()
