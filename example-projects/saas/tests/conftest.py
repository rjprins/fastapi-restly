import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import make_url

# Select the dedicated test service before importing the application, because
# app.database creates the application-owned engine at import time. CI can point
# the suite at its PostgreSQL service through SAAS_TEST_DATABASE_URL.
_raw_test_url = os.environ.get(
    "SAAS_TEST_DATABASE_URL",
    os.environ.get(
        "RESTLY_TEST_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5433/saas_test",
    ),
)
_test_url = make_url(_raw_test_url).set(drivername="postgresql+asyncpg")
os.environ["DATABASE_URL"] = _test_url.render_as_string(hide_password=False)

from app.main import app  # noqa: E402
from app.views._base import get_current_org_id, get_current_user_id  # noqa: E402

import fastapi_restly as fr  # noqa: E402
from fastapi_restly.testing import AsyncRestlyTestClient, RestlyTestClient  # noqa: E402

# The framework under test must live in this checkout, else a leaked VIRTUAL_ENV
# (e.g. the main framework .venv) silently validates the wrong source in a worktree.
_checkout = Path(__file__).resolve().parents[3]
_frl = Path(fr.__file__).resolve()
if _checkout not in _frl.parents:
    raise RuntimeError(
        f"fastapi_restly under test is {_frl}, outside this checkout ({_checkout}). "
        f"This example's venv isn't synced to this tree — run `uv sync` here."
    )

# Dog-food the migration-backed setup against the database app.main configured.
fr.testing.configure_tests(app=app, base=fr.DataclassBase, alembic_upgrade=True)


@pytest.fixture
def client(restly_client) -> RestlyTestClient:
    """The suite's existing name for the isolated Restly test client."""
    return restly_client


@pytest.fixture
def async_client(restly_async_client) -> AsyncRestlyTestClient:
    """The async client for tests that also inspect the async database."""
    return restly_async_client


@pytest.fixture
async def async_org_id(async_client: AsyncRestlyTestClient) -> int:
    response = await async_client.post(
        "/organizations", json={"name": "Pattern Org", "slug": "pattern-org"}
    )
    return response.json()["id"]


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
