"""Testing utilities for fastapi-ding."""

from fastapi.testclient import TestClient
from httpx import AsyncClient

from .pytest_fixtures import (
    DingTestClient,
    app,
    async_session,
    autouse_alembic_upgrade,
    autouse_savepoint_only_mode_sessions,
    client,
    session,
)

__all__ = [
    "app",
    "async_session",
    "autouse_alembic_upgrade",
    "autouse_savepoint_only_mode_sessions",
    "session",
    "create_test_client",
    "client",
    "DingTestClient",
    "get_test_database_url",
    "setup_test_database",
]


def create_test_client(app) -> TestClient:
    """Create a test client for the given FastAPI app."""
    return TestClient(app)


def get_test_database_url() -> str:
    """Get the test database URL."""
    return "sqlite+aiosqlite:///:memory:"


def setup_test_database() -> None:
    """Setup the test database."""
    # This is a no-op for now, but could be used to setup test databases
    pass
