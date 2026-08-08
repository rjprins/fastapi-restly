import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

# Select and clear the disposable database before importing the application,
# because app.main owns the call to fr.configure().
_test_database = Path(__file__).resolve().parents[1] / "test.db"
for leftover in _test_database.parent.glob(f"{_test_database.name}*"):
    leftover.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_test_database}"

from app.main import app  # noqa: E402
from app.views._base import get_current_org_id, get_current_user_id  # noqa: E402

import fastapi_restly as fr  # noqa: E402
from fastapi_restly.testing import RestlyTestClient  # noqa: E402

# The framework under test must live in this checkout, else a leaked VIRTUAL_ENV
# (e.g. the main framework .venv) silently validates the wrong source in a worktree.
_checkout = Path(__file__).resolve().parents[3]
_frl = Path(fr.__file__).resolve()
if _checkout not in _frl.parents:
    raise RuntimeError(
        f"fastapi_restly under test is {_frl}, outside this checkout ({_checkout}). "
        f"This example's venv isn't synced to this tree — run `uv sync` here."
    )

# Dog-food the one-call setup against the database app.main already configured.
fr.testing.configure_tests(app=app, base=fr.DataclassBase, create_all=True)


@pytest.fixture
def client(restly_client) -> RestlyTestClient:
    """The suite's existing name for the isolated Restly test client."""
    return restly_client


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
