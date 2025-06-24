import traceback
from typing import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.orm import Session as SA_Session

import alembic
import alembic.config

from ._globals import fa_globals
from ._session import activate_savepoint_only_mode


@pytest.fixture(autouse=True)
def autouse_session(session):  # noqa: F811
    return session


@pytest.fixture(autouse=True, scope="session")
def autouse_alembic_upgrade(project_root):
    # TODO: Move project_root to Settings?
    alembic_cfg = alembic.config.Config(project_root / "alembic.ini")
    alembic_cfg.set_main_option("script_location", str(project_root / "alembic"))
    try:
        alembic.command.upgrade(alembic_cfg, "head")
    except Exception as exc:
        tb = traceback.format_exc()
        pytest.exit(
            f"Alembic migrations failed: {exc}\n\nTraceback:\n{tb}", returncode=1
        )


@pytest.fixture(autouse=True, scope="session")
def autouse_savepoint_only_mode_sessions() -> None:
    if not fa_globals.async_make_session and not fa_globals.make_session:
        raise RuntimeError(
            "Database connection not yet set up. Ensure setup_async_database_connection() or "
            "setup_database_connection() is called before tests are started."
        )
    if fa_globals.async_make_session:
        activate_savepoint_only_mode(fa_globals.async_make_session)
    if fa_globals.make_session:
        activate_savepoint_only_mode(fa_globals.make_session)


@pytest.fixture
def _shared_connection():
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
    async_engine = fa_globals.async_make_session.kw["bind"]
    async_conn = AsyncConnection(async_engine, sync_connection=_shared_connection)
    async with fa_globals.async_make_session(bind=async_conn) as sess:

        async def begin_nested():
            await sess.begin_nested()
            return sess

        mock_sessionmaker = AsyncMock()
        mock_sessionmaker.side_effect = begin_nested
        mock_sessionmaker.begin.return_value.__aenter__.side_effect = begin_nested

        async def passthrough_exit(self, exc_type, exc_value, traceback):
            return False  # re-raise any exception

        async def patched_commit(self):
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
    with fa_globals.make_session(bind=_shared_connection) as sess:

        def begin_nested():
            sess.begin_nested()
            return sess

        mock_sessionmaker = MagicMock()
        mock_sessionmaker.side_effect = begin_nested
        mock_sessionmaker.begin.return_value.__enter__.side_effect = begin_nested

        def passthrough_exit(self, exc_type, exc_value, traceback):
            return False  # re-raise any exception

        def patched_commit(self):
            sess.begin_nested()

        with (
            patch.object(fa_globals, "make_session", mock_sessionmaker),
            patch.object(SA_Session, "__exit__", passthrough_exit),
            patch.object(SA_Session, "commit", patched_commit),
        ):
            yield sess
