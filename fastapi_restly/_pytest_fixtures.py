from __future__ import annotations

import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import alembic
import alembic.command
import alembic.config
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.orm import Session as SA_Session

from .db import activate_savepoint_only_mode, get_fr_globals
from .db._globals import _fr_globals

if TYPE_CHECKING:
    from .testing._client import RestlyTestClient

try:
    import pytest_asyncio
except ModuleNotFoundError as exc:
    if exc.name != "pytest_asyncio":
        raise
    pytest_asyncio = None

_TESTING_EXTRA_MESSAGE = (
    "fastapi_restly.pytest_fixtures requires optional testing dependencies. "
    'Install them with: pip install "fastapi-restly[testing]"'
)


@pytest.fixture(scope="session")
def restly_project_root() -> Path:
    """Return the project root directory."""
    # Try to find the project root by looking for pyproject.toml
    current = Path.cwd()
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    raise Exception("Could not find a pyproject.toml to establish project root")


def _run_alembic_upgrade(project_root: Path) -> None:
    # Only run alembic migrations if the alembic directory exists
    alembic_dir = project_root / "alembic"
    if not alembic_dir.exists():
        return  # Skip if no alembic directory

    # TODO: Move project root discovery to Settings?
    alembic_cfg = alembic.config.Config(project_root / "alembic.ini")
    alembic_cfg.set_main_option("script_location", str(alembic_dir))
    try:
        alembic.command.upgrade(alembic_cfg, "head")
    except Exception as exc:
        tb = traceback.format_exc()
        pytest.exit(
            f"Alembic migrations failed: {exc}\n\nTraceback:\n{tb}", returncode=1
        )


def _activate_savepoint_only_mode_sessions() -> None:
    # Only run if database connections are set up
    if not _fr_globals.async_make_session and not _fr_globals.make_session:
        return  # Skip if no database connections

    if _fr_globals.async_make_session:
        activate_savepoint_only_mode(_fr_globals.async_make_session)
    if _fr_globals.make_session:
        activate_savepoint_only_mode(_fr_globals.make_session)


@pytest.fixture
def _shared_connection():
    # Sync tests need a sync sessionmaker, but async-only projects should still
    # be able to use the restly_async_session fixture without one.
    if not _fr_globals.make_session:
        yield None
        return

    engine = _fr_globals.make_session.kw["bind"]
    with engine.connect() as conn:
        yield conn


if pytest_asyncio is None:

    @pytest.fixture
    def restly_async_session(_shared_connection) -> None:
        raise ModuleNotFoundError(_TESTING_EXTRA_MESSAGE, name="pytest_asyncio")

else:

    @pytest_asyncio.fixture
    async def restly_async_session(_shared_connection) -> AsyncIterator[SA_AsyncSession]:
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
        if not _fr_globals.async_make_session:
            pytest.skip("Database connection not set up")

        async_engine = _fr_globals.async_make_session.kw["bind"]

        @asynccontextmanager
        async def get_bound_async_connection():
            if _shared_connection is None:
                async with async_engine.connect() as async_conn:
                    yield async_conn
                return

            async_conn = AsyncConnection(
                async_engine, sync_connection=_shared_connection
            )
            async with async_conn:
                yield async_conn

        async with get_bound_async_connection() as async_conn:
            async with _fr_globals.async_make_session(bind=async_conn) as sess:

                async def begin_nested():
                    await sess.begin_nested()
                    return sess

                mock_sessionmaker = AsyncMock()
                mock_sessionmaker.side_effect = begin_nested
                # session.begin() is used as a context manager (async with session.begin():)
                # We need it to also return our savepoint session so explicit transaction
                # blocks work correctly with our isolation mechanism
                mock_sessionmaker.begin.return_value.__aenter__.side_effect = (
                    begin_nested
                )
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
def restly_session(_shared_connection) -> Iterator[SA_Session]:
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
    if not _fr_globals.make_session:
        pytest.skip("Database connection not set up")

    with _fr_globals.make_session(bind=_shared_connection) as sess:

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
def restly_app() -> FastAPI:
    """Create a FastAPI app instance for testing."""
    return FastAPI()


@pytest.fixture
def restly_client(restly_app) -> RestlyTestClient:
    """Create a RestlyTestClient instance for testing."""
    try:
        from .testing._client import RestlyTestClient
    except ModuleNotFoundError as exc:
        if exc.name == "httpx":
            raise ModuleNotFoundError(_TESTING_EXTRA_MESSAGE, name="httpx") from exc
        raise

    return RestlyTestClient(restly_app)
