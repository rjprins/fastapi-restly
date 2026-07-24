import asyncio
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

# Dog-food Restly's shipped testing fixtures (fznb.5): configure once against a
# file-backed SQLite database, create the schema once, and let
# ``restly_async_session`` isolate every test with a savepoint. This is the
# recipe documented in docs/howto_testing.md. ``app.main`` configures ``saas.db``
# for real runs; the line below repoints the suite at a throwaway ``test.db``
# (gitignored) that the savepoint fixture keeps clean between tests.
fr.configure(async_database_url="sqlite+aiosqlite:///./test.db")


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Create the schema once for the whole session; tests roll back their data.

    Start from a clean database file so a leftover ``test.db`` (an interrupted
    run, or an older schema) cannot seed rows or stale tables that would sit
    below the per-test transaction and never roll back.
    """
    for leftover in Path().glob("test.db*"):
        leftover.unlink()
    asyncio.run(fr.db.async_create_all(fr.DataclassBase))


@pytest.fixture
def restly_app():
    """The app ``restly_client`` wraps."""
    return app


@pytest.fixture
def client(restly_client) -> RestlyTestClient:
    """The suite's existing name for the isolated Restly test client."""
    return restly_client


@pytest.fixture(autouse=True)
async def _isolate_every_test(restly_async_session):
    """Give every test savepoint isolation, client requests included."""


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
