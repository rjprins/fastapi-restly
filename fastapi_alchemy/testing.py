import traceback
from unittest.mock import MagicMock, patch

import alembic
import alembic.config
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ._globals import fa_globals
from ._session import activate_savepoint_only_mode


@pytest.fixture(autouse=True, scope="session")
def autouse_alembic_upgrade(project_root):
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
            "Database connection not yet set up. Ensure async_setup_database_connection() or "
            "setup_database_connection() is called before tests are started."
        )
    if fa_globals.async_make_session:
        activate_savepoint_only_mode(fa_globals.async_make_session)
    if fa_globals.make_session:
        activate_savepoint_only_mode(fa_globals.make_session)


@pytest.fixture
def async_session() -> AsyncSession:
    """Use this fixture if you want to use the database in tests."""
    with fa_globals.async_make_session() as sess:
        mock_sessionmaker = MagicMock()
        mock_sessionmaker.return_value = sess
        mock_sessionmaker.begin.return_value.__enter__.return_value = sess

        with patch.object(fa_globals, "async_make_session", mock_sessionmaker):
            yield sess


@pytest.fixture
def session() -> Session:
    """Use this fixture if you want to use the database in tests."""
    with fa_globals.make_session() as sess:

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
            patch.object(Session, "__exit__", passthrough_exit),
            patch.object(fa_globals, "make_session", mock_sessionmaker),
            patch.object(Session, "commit", patched_commit),
        ):
            yield sess
