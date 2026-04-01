import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.orm import Session as SA_Session

import alembic
import alembic.command
import alembic.config

from ..db import activate_savepoint_only_mode, fr_globals, get_fr_globals
from ._client import RestlyTestClient


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
    if not fr_globals.async_make_session and not fr_globals.make_session:
        return  # Skip if no database connections

    if fr_globals.async_make_session:
        activate_savepoint_only_mode(fr_globals.async_make_session)
    if fr_globals.make_session:
        activate_savepoint_only_mode(fr_globals.make_session)


@pytest.fixture
def _shared_connection():
    # Sync tests need a sync sessionmaker, but async-only projects should still
    # be able to use the async_session fixture without one.
    if not fr_globals.make_session:
        yield None
        return

    engine = fr_globals.make_session.kw["bind"]
    with engine.connect() as conn:
        yield conn


@pytest_asyncio.fixture
async def async_session(_shared_connection) -> AsyncIterator[SA_AsyncSession]:
    """
    Pytest fixture providing a database session with savepoint-based isolation.

    Each test runs inside a savepoint. At the end of the test, the savepoint is
    rolled back, leaving the database clean for the next test.

    NOTE: Calling session.rollback() inside a test rolls back to the last savepoint
    (created by each patched commit()), NOT to the start of the test. This differs
    from production behavior. To undo all changes in a test, use session.rollback()
    after each commit(), but be aware that data added before the last commit() is
    still visible.
    """
    # Only run if database connections are set up
    if not fr_globals.async_make_session:
        pytest.skip("Database connection not set up")

    async_engine = fr_globals.async_make_session.kw["bind"]

    @asynccontextmanager
    async def get_bound_async_connection():
        if _shared_connection is None:
            async with async_engine.connect() as async_conn:
                yield async_conn
            return

        async_conn = AsyncConnection(async_engine, sync_connection=_shared_connection)
        async with async_conn:
            yield async_conn

    async with get_bound_async_connection() as async_conn:
        async with fr_globals.async_make_session(bind=async_conn) as sess:
            async def begin_nested():
                await sess.begin_nested()
                return sess

            mock_sessionmaker = AsyncMock()
            mock_sessionmaker.side_effect = begin_nested
            # session.begin() is used as a context manager (async with session.begin():)
            # We need it to also return our savepoint session so explicit transaction
            # blocks work correctly with our isolation mechanism
            mock_sessionmaker.begin.return_value.__aenter__.side_effect = begin_nested
            # FIXME: begin().__aexit__ should flush pending changes to make them visible
            # within the test, but currently does not. This may cause visibility issues
            # when using `async with session.begin(): ...` blocks inside tests.
            # Impact: changes inside explicit begin() blocks may not be visible after exit.

            async def passthrough_exit(self, exc_type, exc_value, traceback):
                await sess.flush()
                return False  # re-raise any exception

            async def patched_commit(self):
                await sess.flush()
                await sess.begin_nested()

            globals_obj = get_fr_globals()
            original_async_make_session = globals_obj.async_make_session
            globals_obj.async_make_session = mock_sessionmaker
            try:
                with (
                    patch.object(SA_AsyncSession, "__aexit__", passthrough_exit),
                    patch.object(SA_AsyncSession, "commit", patched_commit),
                ):
                    yield sess
            finally:
                globals_obj.async_make_session = original_async_make_session


@pytest.fixture
def session(_shared_connection) -> Iterator[SA_Session]:
    """
    Pytest fixture providing a database session with savepoint-based isolation.

    Each test runs inside a savepoint. At the end of the test, the savepoint is
    rolled back, leaving the database clean for the next test.

    NOTE: Calling session.rollback() inside a test rolls back to the last savepoint
    (created by each patched commit()), NOT to the start of the test. This differs
    from production behavior. To undo all changes in a test, use session.rollback()
    after each commit(), but be aware that data added before the last commit() is
    still visible.
    """
    # Only run if database connections are set up
    if not fr_globals.make_session:
        pytest.skip("Database connection not set up")

    with fr_globals.make_session(bind=_shared_connection) as sess:

        def begin_nested():
            sess.begin_nested()
            return sess

        mock_sessionmaker = MagicMock()
        mock_sessionmaker.side_effect = begin_nested
        # session.begin() is used as a context manager (with session.begin():)
        # We need it to also return our savepoint session so explicit transaction
        # blocks work correctly with our isolation mechanism
        mock_sessionmaker.begin.return_value.__enter__.side_effect = begin_nested
        # FIXME: begin().__exit__ should flush pending changes to make them visible
        # within the test, but currently does not. This may cause visibility issues
        # when using `with session.begin(): ...` blocks inside tests.
        # Impact: changes inside explicit begin() blocks may not be visible after exit.

        def passthrough_exit(self, exc_type, exc_value, traceback):
            sess.flush()
            return False  # re-raise any exception

        def patched_commit(self):
            sess.flush()
            sess.begin_nested()

        globals_obj = get_fr_globals()
        original_make_session = globals_obj.make_session
        globals_obj.make_session = mock_sessionmaker
        try:
            with (
                patch.object(SA_Session, "__exit__", passthrough_exit),
                patch.object(SA_Session, "commit", patched_commit),
            ):
                yield sess
        finally:
            globals_obj.make_session = original_make_session


@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI app instance for testing."""
    return FastAPI()


@pytest.fixture
def client(app) -> RestlyTestClient:
    """Create a RestlyTestClient instance for testing."""
    return RestlyTestClient(app)
