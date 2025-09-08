import traceback
from pathlib import Path
from typing import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.orm import Session as SA_Session

import alembic
import alembic.config

from ._globals import fa_globals
from ._session import activate_savepoint_only_mode
from .testing import DingTestClient


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Return the project root directory."""
    # Try to find the project root by looking for pyproject.toml
    current = Path.cwd()
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    raise Exception("Could not find a pyproject.toml to establish project root")


@pytest.fixture(autouse=True, scope="session")
def autouse_alembic_upgrade(project_root):
    # Only run alembic migrations if the alembic directory exists
    alembic_dir = project_root / "alembic"
    if not alembic_dir.exists():
        return  # Skip if no alembic directory

    # TODO: Move project_root to Settings?
    alembic_cfg = alembic.config.Config(project_root / "alembic.ini")
    alembic_cfg.set_main_option("script_location", str(alembic_dir))
    try:
        alembic.command.upgrade(alembic_cfg, "head")
    except Exception as exc:
        tb = traceback.format_exc()
        pytest.exit(
            f"Alembic migrations failed: {exc}\n\nTraceback:\n{tb}", returncode=1
        )


@pytest.fixture(autouse=True, scope="session")
def autouse_savepoint_only_mode_sessions() -> None:
    # Only run if database connections are set up
    if not fa_globals.async_make_session and not fa_globals.make_session:
        return  # Skip if no database connections

    if fa_globals.async_make_session:
        activate_savepoint_only_mode(fa_globals.async_make_session)
    if fa_globals.make_session:
        activate_savepoint_only_mode(fa_globals.make_session)


@pytest.fixture
def _shared_connection():
    # Only run if database connections are set up
    if not fa_globals.make_session:
        pytest.skip("Database connection not set up")

    engine = fa_globals.make_session.kw["bind"]
    with engine.connect() as conn:
        yield conn


@pytest.fixture
async def async_session(_shared_connection) -> AsyncIterator[SA_AsyncSession]:
    """
    Mock async_make_session and sqlalchemy AsyncSession to always return the same
    session instance. This fixture ensures test isolation by using savepoints via
    nested transactions. Commits inside the test won't persist to the DB.
    """
    # Only run if database connections are set up
    if not fa_globals.async_make_session:
        pytest.skip("Database connection not set up")

    async_engine = fa_globals.async_make_session.kw["bind"]
    async_conn = AsyncConnection(async_engine, sync_connection=_shared_connection)
    async with fa_globals.async_make_session(bind=async_conn) as sess:

        async def begin_nested():
            await sess.begin_nested()
            return sess

        mock_sessionmaker = AsyncMock()
        mock_sessionmaker.side_effect = begin_nested
        mock_sessionmaker.begin.return_value.__aenter__.side_effect = begin_nested
        # TODO: begin.return_value.__aexit__ should flush.

        async def passthrough_exit(self, exc_type, exc_value, traceback):
            await sess.flush()
            return False  # re-raise any exception

        async def patched_commit(self):
            await sess.flush()
            await sess.begin_nested()

        with (
            patch.object(fa_globals, "async_make_session", mock_sessionmaker),
            patch.object(SA_AsyncSession, "__aexit__", passthrough_exit),
            patch.object(SA_AsyncSession, "commit", patched_commit),
        ):
            yield sess


@pytest.fixture
def session(_shared_connection) -> Iterator[SA_Session]:
    """Use this fixture if you want to use the database in tests.
    TODO: Describe what this function does exactly
    """
    # Only run if database connections are set up
    if not fa_globals.make_session:
        pytest.skip("Database connection not set up")

    with fa_globals.make_session(bind=_shared_connection) as sess:

        def begin_nested():
            sess.begin_nested()
            return sess

        mock_sessionmaker = MagicMock()
        mock_sessionmaker.side_effect = begin_nested
        mock_sessionmaker.begin.return_value.__enter__.side_effect = begin_nested
        # TODO: begin.return_value.__exit__ should flush.

        def passthrough_exit(self, exc_type, exc_value, traceback):
            sess.flush()
            return False  # re-raise any exception

        def patched_commit(self):
            sess.flush()
            sess.begin_nested()

        with (
            patch.object(fa_globals, "make_session", mock_sessionmaker),
            patch.object(SA_Session, "__exit__", passthrough_exit),
            patch.object(SA_Session, "commit", patched_commit),
        ):
            yield sess


@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI app instance for testing."""
    return FastAPI()


@pytest.fixture
def client(app) -> DingTestClient:
    """Create a DingTestClient instance for testing."""
    return DingTestClient(app)
