from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from app.main import app
from app.views._base import get_current_org_id, get_current_user_id

import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient

# The framework under test must live in this checkout, else a leaked VIRTUAL_ENV
# (e.g. the main framework .venv) silently validates the wrong source in a worktree.
_checkout = Path(__file__).resolve().parents[3]
_frl = Path(fr.__file__).resolve()
if _checkout not in _frl.parents:
    raise RuntimeError(
        f"fastapi_restly under test is {_frl}, outside this checkout ({_checkout}). "
        f"This example's venv isn't synced to this tree — run `uv sync` here."
    )


@pytest.fixture
def client() -> RestlyTestClient:
    """Create a test client backed by the saas FastAPI app."""
    return RestlyTestClient(app)


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
        *, org_id: int | None = None, user_id: int | None = None
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
    await fr.db.async_create_all(fr.DataclassBase)
    yield
    # Dispose while the event loop is still open — otherwise aiosqlite's
    # worker thread tries to call back into a closed loop on next test.
    await fr.db.get_async_engine().dispose()
