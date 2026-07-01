from pathlib import Path

# Import blog.main to set up database connection before fixtures run
import blog.main  # noqa: F401
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr

# The framework under test must live in this checkout, else a leaked VIRTUAL_ENV
# (e.g. the main framework .venv) silently validates the wrong source in a worktree.
_checkout = Path(__file__).resolve().parents[3]
_frl = Path(fr.__file__).resolve()
if _checkout not in _frl.parents:
    raise RuntimeError(
        f"fastapi_restly under test is {_frl}, outside this checkout ({_checkout}). "
        f"This example's venv isn't synced to this tree — run `uv sync` here."
    )


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
