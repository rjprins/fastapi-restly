from __future__ import annotations

import weakref
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, event
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, async_sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.orm import Session as SA_Session
from sqlalchemy.orm import sessionmaker

from .db._globals import _fr_globals, _get_restly_context
from .exc import RestlyConfigurationError

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


# Test engines whose pysqlite legacy-transaction shim has already been neutralised.
_sqlite_savepoint_fixed: weakref.WeakSet = weakref.WeakSet()


def _install_sqlite_savepoint_fix(engine: Engine | AsyncEngine) -> None:
    """Neutralise pysqlite's legacy transaction shim on a test engine.

    stdlib ``sqlite3`` emulates PEP 249 by sniffing SQL keywords and issuing an
    implicit ``BEGIN``, which turns ``RELEASE`` of the outermost ``SAVEPOINT``
    into a real commit. Under ``create_savepoint`` isolation that would leak
    committed test data past the outer-transaction rollback (measured on
    aiosqlite). Hand transaction control to SQLAlchemy: disable the shim and emit
    ``BEGIN`` explicitly, so every ``SAVEPOINT`` nests inside a real transaction.

    Fixtures-only, sqlite-only, idempotent per engine. Deliberately NOT applied to
    production engines (fznb.12): an adopter may pass their own engine, so an
    engine-wide fix could never be complete, and it would change production
    locking and DDL semantics. On Python 3.12+ the connect handler is a one-liner
    (``dbapi_connection.autocommit = False``); the ``isolation_level = None`` form
    stays while ``requires-python`` still includes 3.10/3.11.
    """
    sync_engine = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
    if sync_engine.dialect.name != "sqlite":
        return
    if sync_engine in _sqlite_savepoint_fixed:
        return
    _sqlite_savepoint_fixed.add(sync_engine)

    @event.listens_for(sync_engine, "connect")
    def _disable_legacy_transaction_control(dbapi_connection, connection_record):
        dbapi_connection.isolation_level = None

    @event.listens_for(sync_engine, "begin")
    def _emit_begin(conn):
        conn.exec_driver_sql("BEGIN")


@pytest.fixture
def _shared_connection():
    # One pinned connection shared by the sync and async fixtures, so a test that
    # uses both sees a single database. Each request during the test joins this
    # connection's outer transaction through a SAVEPOINT (create_savepoint mode);
    # the outer transaction is never committed and rolls back at teardown, so no
    # test data is ever persisted. Async-only projects have no sync sessionmaker;
    # restly_async_session pins its own connection in that case.
    if not _fr_globals.make_session:
        yield None
        return

    engine = _fr_globals.make_session.kw["bind"]
    _install_sqlite_savepoint_fix(engine)
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            yield conn
        finally:
            trans.rollback()


if pytest_asyncio is None:

    @pytest.fixture
    def restly_async_session(_shared_connection) -> None:  # pyright: ignore[reportRedeclaration]
        # The else-branch defines the real async fixture; this stub only
        # runs when the optional ``pytest_asyncio`` extra isn't installed.
        # Pyright cannot model mutually exclusive module-level branches.
        raise ModuleNotFoundError(_TESTING_EXTRA_MESSAGE, name="pytest_asyncio")

else:

    @pytest_asyncio.fixture
    async def restly_async_session(
        _shared_connection,
    ) -> AsyncIterator[SA_AsyncSession]:
        """
        Pytest fixture providing an isolated async database session.

        The async equivalent of :func:`restly_session`. Each request during the
        test builds its own real ``AsyncSession`` that joins a never-committed
        outer transaction through a SAVEPOINT (SQLAlchemy's ``create_savepoint``
        mode), so a request's ``commit()`` and ``rollback()`` behave as in
        production. The outer transaction rolls back at teardown, leaving the
        database clean
        for the next test -- nothing is ever persisted. When a sync sessionmaker is
        also configured, this fixture shares the sync fixture's pinned connection,
        so a test that uses both sees one database.

        As with the sync fixture there is no shared identity map: this fixture and
        the request are separate sessions on one connection, so a write made
        directly on this session becomes visible to a request only after a flush or
        commit. Configure an async sessionmaker for the tests (``async_database_url=``,
        ``async_engine=`` or ``async_make_session=`` to ``fr.configure()``); a
        ``session_generator`` alone cannot be isolated, because ``AsyncSessionDep``
        resolves it before the factory this fixture swaps.

        ``fr.open_async_session()`` resolves the same factory, so it also yields an
        isolated session during a test.
        """
        if not _fr_globals.async_make_session:
            if _fr_globals.session_generator is not None:
                raise RestlyConfigurationError(
                    "restly_async_session cannot isolate a session built by "
                    "your session_generator: AsyncSessionDep reads the "
                    "generator before the session factory this fixture swaps, "
                    "so each request would get its own session, with no "
                    "isolation. Configure an async sessionmaker for the tests "
                    "as well: pass async_database_url=, async_engine= or "
                    "async_make_session= to fr.configure(). The fixture then "
                    "builds the isolated session from it and ignores the "
                    "generator during each test."
                )
            pytest.skip("Database connection not set up")

        original = _fr_globals.async_make_session
        async_engine = original.kw["bind"]

        @asynccontextmanager
        async def _pinned_async_connection():
            if _shared_connection is not None:
                # Share the sync fixture's pinned connection and its already-open
                # outer transaction. An AsyncConnection wrapping a live sync
                # connection is already started, so entering it would re-run
                # start() and raise; use it directly and let _shared_connection
                # own the teardown.
                yield AsyncConnection(async_engine, sync_connection=_shared_connection)
                return

            _install_sqlite_savepoint_fix(async_engine)
            async with async_engine.connect() as conn:
                # Begin the outer transaction the request sessions join via
                # savepoint; the connection close rolls it back at teardown.
                await conn.begin()
                yield conn

        async with _pinned_async_connection() as async_conn:
            # A real factory bound to the pinned connection, in create_savepoint
            # mode. Every request (and this fixture) gets its own real session
            # joining the outer transaction via a savepoint -- no method patching,
            # no MagicMock factory, no session shared across requests. (A per-mapper
            # ``binds=`` in the original factory rides along and would escape
            # isolation; unsupported until someone actually needs it.)
            isolated_make_session = async_sessionmaker(
                class_=original.class_,
                **{
                    **original.kw,
                    "bind": async_conn,
                    "join_transaction_mode": "create_savepoint",
                },
            )
            globals_obj = _get_restly_context()
            original_async_make_session = globals_obj.async_make_session
            original_session_generator = globals_obj.session_generator
            globals_obj.async_make_session = isolated_make_session
            # AsyncSessionDep resolves the generator first; clearing it routes
            # requests through the isolated factory.
            globals_obj.session_generator = None
            session = isolated_make_session()
            try:
                yield session
            finally:
                # Restore before closing: a teardown-time close() failure must not
                # leak the swapped factory into the next test.
                globals_obj.async_make_session = original_async_make_session
                globals_obj.session_generator = original_session_generator
                await session.close()


@pytest.fixture
def restly_session(_shared_connection) -> Iterator[SA_Session]:
    """
    Pytest fixture providing an isolated database session.

    The session joins a never-committed outer transaction through a SAVEPOINT
    (SQLAlchemy's ``create_savepoint`` mode). Every request during the test builds
    its own real session on the same pinned connection, so a request's
    ``commit()`` and ``rollback()`` behave as in production, and the outer
    transaction rolls back at teardown, leaving the database clean for the next
    test -- nothing is ever persisted.

    Unlike production, this fixture and the request are separate sessions on one
    connection, so a write made directly on this session becomes visible to a
    request only after a flush or commit (there is no shared identity map).
    Configure a sync sessionmaker for the tests (``database_url=``, ``engine=`` or
    ``make_session=`` to ``fr.configure()``); a ``sync_session_generator`` alone
    cannot be isolated, because ``SessionDep`` resolves it before the factory this
    fixture swaps.

    ``fr.open_session()`` resolves the same factory, so it also yields an isolated
    session during a test.
    """
    if not _fr_globals.make_session:
        if _fr_globals.sync_session_generator is not None:
            raise RestlyConfigurationError(
                "restly_session cannot isolate a session built by your "
                "sync_session_generator: SessionDep reads the generator before "
                "the session factory this fixture swaps, so each request "
                "would get its own session, with no isolation. Configure a sync "
                "sessionmaker for the tests as well: pass database_url=, "
                "engine= or make_session= to fr.configure(). The fixture then "
                "builds the isolated session from it and ignores the generator "
                "during each test."
            )
        pytest.skip("Database connection not set up")

    original = _fr_globals.make_session
    # A real factory bound to the pinned connection, in create_savepoint mode.
    # Every request (and this fixture) gets its own real session joining the outer
    # transaction via a savepoint -- no method patching, no MagicMock factory, no
    # session shared across requests. (A per-mapper ``binds=`` in the original
    # factory rides along and would route those models off the pinned connection,
    # escaping isolation; unsupported until someone actually needs it.)
    isolated_make_session = sessionmaker(
        class_=original.class_,
        **{
            **original.kw,
            "bind": _shared_connection,
            "join_transaction_mode": "create_savepoint",
        },
    )

    globals_obj = _get_restly_context()
    original_make_session = globals_obj.make_session
    original_sync_session_generator = globals_obj.sync_session_generator
    globals_obj.make_session = isolated_make_session
    # SessionDep resolves the generator first; clearing it routes requests through
    # the isolated factory.
    globals_obj.sync_session_generator = None
    session = isolated_make_session()
    try:
        yield session
    finally:
        # Restore before closing: a teardown-time close() failure must not leak
        # the swapped factory (bound to the pinned connection) into the next test.
        globals_obj.make_session = original_make_session
        globals_obj.sync_session_generator = original_sync_session_generator
        session.close()


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
        # Newer Starlette's testclient requires httpx2 (name="httpx2"); our own
        # _client.py import raises name="httpx". Both mean the test client is
        # missing.
        if exc.name in {"httpx", "httpx2"}:
            raise ModuleNotFoundError(_TESTING_EXTRA_MESSAGE, name=exc.name) from exc
        raise

    return RestlyTestClient(restly_app)
