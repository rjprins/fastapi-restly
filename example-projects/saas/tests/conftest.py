from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest
from app.main import app
from app.views._base import get_current_org_id, get_current_user_id

import fastapi_restly as fr

pytest_plugins = ["fastapi_restly.pytest_fixtures"]


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    """Ensure dependency overrides do not leak between tests."""
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def auth_context() -> Callable[..., Iterator[None]]:
    """Override the example auth dependencies for a request block."""

    @contextmanager
    def override(
        *,
        org_id: int | None = None,
        user_id: int | None = None,
    ) -> Iterator[None]:
        previous = app.dependency_overrides.copy()
        if org_id is not None:
            app.dependency_overrides[get_current_org_id] = lambda: org_id
        if user_id is not None:
            app.dependency_overrides[get_current_user_id] = lambda: user_id
        try:
            yield
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)

    return override


@pytest.fixture(autouse=True)
async def use_in_memory_database():
    """Switch to a fresh in-memory SQLite database for each test."""
    fr.configure(async_database_url="sqlite+aiosqlite:///:memory:")
    async with fr.get_async_engine().begin() as conn:
        await conn.run_sync(fr.DataclassBase.metadata.create_all)
