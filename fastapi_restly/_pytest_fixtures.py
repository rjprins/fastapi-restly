from __future__ import annotations

import os
import weakref
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Iterator, cast

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, event
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, async_sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.orm import Session as SA_Session
from sqlalchemy.orm import sessionmaker

from ._test_setup import (
    _ASYNC_LEG_TRIPWIRE,
    _SYNC_LEG_TRIPWIRE,
    DB_CLEANUP_ENV_VAR,
    DB_CLEANUP_MODES,
    DELETE,
    NONE,
    ROLLBACK,
    _clean_database_async,
    _clean_database_sync,
    _create_schema,
    _current_setup,
    _resolve_db_cleanup,
    _resolve_engine,
    _source_factories,
)
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


def _find_project_root(start: Path) -> Path:
    """Walk up from ``start`` to the nearest ancestor holding a ``pyproject.toml``."""
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise Exception(
        f"Could not find a pyproject.toml at or above {start} to establish "
        "the project root"
    )


@pytest.fixture
def restly_project_root(request: pytest.FixtureRequest) -> Path:
    """Return the root of the project that owns the requesting test.

    Walks up from the requesting test file to the nearest ancestor directory
    that holds a ``pyproject.toml``. Discovery is anchored to the test file, not
    the working directory, so it returns the same root no matter where pytest
    was invoked, and in a monorepo each test resolves to its own sub-project's
    root. Use it to locate project files (migration configs, test data) without
    hardcoding absolute paths.
    """
    return _find_project_root(request.path.parent)


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


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register ``--restly-db-cleanup``, which outranks the argument and the
    environment so a debugging run can switch mode without editing the suite."""
    parser.addoption(
        "--restly-db-cleanup",
        dest="restly_db_cleanup",
        choices=list(DB_CLEANUP_MODES),
        default=None,
        help=(
            "How Restly gives each test a clean database. 'rollback' (the "
            "default) rolls every test back and persists nothing; 'delete' "
            "empties the tables before each test and lets writes commit, so the "
            "last test's rows survive the run and can be inspected; 'none' leaves "
            "cleaning to the suite."
        ),
    )


# The flag or environment override, resolved once. A property of the run, not of
# a test: reading the environment again per test would let the mode announced in
# the header and the mode enforced during the run disagree.
_db_cleanup_override: str | None = None


#: Overrides of runs that started before this one, innermost last. A nested
#: in-process run must give the outer run its mode back rather than clear it.
_override_stack: list[str | None] = []


def pytest_configure(config: pytest.Config) -> None:
    global _db_cleanup_override
    _override_stack.append(_db_cleanup_override)
    chosen = config.getoption("restly_db_cleanup")
    if not isinstance(chosen, str):
        chosen = os.environ.get(DB_CLEANUP_ENV_VAR) or None
        if chosen is not None and chosen not in DB_CLEANUP_MODES:
            # Raised here rather than from a later hook, where pytest would
            # report it as an INTERNALERROR with a pluggy traceback.
            raise pytest.UsageError(
                f"{DB_CLEANUP_ENV_VAR} is {chosen!r}; expected one of "
                f"{', '.join(repr(mode) for mode in DB_CLEANUP_MODES)}."
            )
    _db_cleanup_override = chosen


def pytest_unconfigure(config: pytest.Config) -> None:
    # Restore rather than clear: a nested in-process run must neither leak its
    # mode outward nor take the outer run's away.
    global _db_cleanup_override
    _db_cleanup_override = _override_stack.pop() if _override_stack else None


def pytest_report_header(config: pytest.Config) -> str | None:
    """Announce a non-default cleanup mode, so a stale flag or environment
    variable cannot quietly change what the suite does."""
    mode = _cleanup_mode()
    if mode == ROLLBACK:
        return None
    if mode == NONE:
        return "restly: db cleanup mode 'none', nothing is cleaned between tests"
    return f"restly: db cleanup mode {mode!r}, test writes are committed and persist"


def _reject_per_mapper_binds(factory: Any) -> None:
    """Refuse a session factory whose ``binds=`` sends some models elsewhere.

    The isolated factory copies the original's keyword arguments, so a per-mapper
    ``binds`` would ride along and route those models to their own engine, outside
    the pinned connection. Their writes commit for real and survive the rollback,
    which is silent: the rest of the test looks isolated.
    """
    binds = factory.kw.get("binds")
    if not binds:
        return
    mapped = ", ".join(sorted(getattr(key, "__name__", str(key)) for key in binds))
    raise RestlyConfigurationError(
        "The session factory passed to fr.configure() (or "
        "fr.testing.configure_tests()) has per-mapper binds "
        f"({mapped}), which the test fixtures cannot isolate: those models would "
        "be routed to their own engine, outside the connection this test pins, "
        "and their writes would be committed rather than rolled back. Configure "
        "the tests with a single-bind sessionmaker."
    )


def _cleanup_mode() -> str:
    """The cleanup mode in force, or ``rollback`` when the suite never opted in.

    A suite that does not call ``configure_tests()`` still gets the rollback
    behaviour the session fixtures have always had when it requests one.
    """
    setup = _current_setup()
    if setup is None:
        return ROLLBACK
    return _resolve_db_cleanup(setup, _db_cleanup_override)


@pytest.fixture
def _shared_connection():
    # One pinned connection shared by the sync and async fixtures, so a test that
    # uses both sees a single database. Each request during the test joins this
    # connection's outer transaction through a SAVEPOINT (create_savepoint mode);
    # the outer transaction is never committed and rolls back at teardown, so no
    # test data is ever persisted. Async-only projects have no sync sessionmaker;
    # restly_async_session pins its own connection in that case.
    if _cleanup_mode() != ROLLBACK:
        # Only rollback mode pins a connection. The other modes let writes commit,
        # so there is no outer transaction to hold open.
        yield None
        return

    make_session, _ = _source_factories()
    if not make_session:
        yield None
        return

    # The bind may be a Connection, as fr.configure(make_session=...) allows.
    # Pin a connection of our own from the engine behind it; events and connect()
    # are engine-level, and a caller's Connection carries its own transaction.
    engine = _resolve_engine(make_session.kw["bind"])
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

        All of that describes the default ``db_cleanup="rollback"``. Under the
        other modes this yields a plain session on the configured database, since
        rolling it back would undo writes those modes mean to commit.
        """
        original = _source_factories()[1]
        if _cleanup_mode() != ROLLBACK:
            # The fixture follows the suite's cleanup mode. Rolling this session
            # back regardless would undo writes the mode means to commit, and
            # leave nothing behind to inspect.
            if original is None:
                pytest.skip("Database connection not set up")
            session = original()
            try:
                yield session
            finally:
                await session.close()
            return

        if original is None:
            if _fr_globals.session_generator is not None:
                raise RestlyConfigurationError(
                    "restly_async_session cannot isolate a session built by "
                    "your session_generator: AsyncSessionDep reads the "
                    "generator before the session factory this fixture swaps, "
                    "so each request would get its own session, with no "
                    "isolation. Configure an async sessionmaker for the tests "
                    "as well: pass async_database_url=, async_engine= or "
                    "async_make_session= to fr.configure(), or to "
                    "fr.testing.configure_tests() if the suite uses it. The "
                    "fixture then builds the isolated session from it and "
                    "ignores the generator during each test."
                )
            pytest.skip("Database connection not set up")

        _reject_per_mapper_binds(original)
        async_engine = _resolve_engine(original.kw["bind"])

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
            # Layered over the run-wide routing installed by
            # _restly_managed_routing; restore it rather than clear the slot.
            previous = globals_obj.test_async_make_session
            globals_obj.test_async_make_session = isolated_make_session
            session = None
            try:
                session = isolated_make_session()
                yield session
            finally:
                # Restore before closing: a teardown-time close() failure must
                # not leave the per-test factory in place of the run-wide one.
                globals_obj.test_async_make_session = previous
                if session is not None:
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

    All of that describes the default ``db_cleanup="rollback"``. Under the other
    modes this yields a plain session on the configured database, since rolling it
    back would undo writes those modes mean to commit.
    """
    original = _source_factories()[0]
    if _cleanup_mode() != ROLLBACK:
        # The fixture follows the suite's cleanup mode. Rolling this session back
        # regardless would undo writes the mode means to commit, and leave nothing
        # behind to inspect.
        if original is None:
            pytest.skip("Database connection not set up")
        session = original()
        try:
            yield session
        finally:
            session.close()
        return

    if original is None:
        if _fr_globals.sync_session_generator is not None:
            raise RestlyConfigurationError(
                "restly_session cannot isolate a session built by your "
                "sync_session_generator: SessionDep reads the generator before "
                "the session factory this fixture swaps, so each request "
                "would get its own session, with no isolation. Configure a sync "
                "sessionmaker for the tests as well: pass database_url=, "
                "engine= or make_session= to fr.configure(), or to "
                "fr.testing.configure_tests() if the suite uses it. The "
                "fixture then builds the isolated session from it and ignores "
                "the generator during each test."
            )
        pytest.skip("Database connection not set up")

    _reject_per_mapper_binds(original)
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
    # One field, consulted first by every session source. Nothing the application
    # configures later can displace it, and no generator is read around it.
    # Layered over the run-wide routing installed by _restly_managed_routing;
    # restore it rather than clear the slot.
    previous = globals_obj.test_make_session
    globals_obj.test_make_session = isolated_make_session
    session = None
    try:
        session = isolated_make_session()
        yield session
    finally:
        # Restore before closing: a teardown-time close() failure must not
        # leave the per-test factory in place of the run-wide one.
        globals_obj.test_make_session = previous
        if session is not None:
            session.close()


#: What the test override slots held before each run installed its routing,
#: tagged with the run's Session so a run that never pushed cannot pop another
#: run's entry. Innermost run last.
_routing_stack: list[tuple[Any, Any, Any]] = []


def pytest_collection_finish(session: pytest.Session) -> None:
    """Route every Restly session source through the recorded setup, run-wide,
    and build the schema.

    Installed after collection -- every ``conftest.py`` has been imported, so
    the recorded setup is final -- and before any fixture of any scope runs. A
    hook rather than a session-scoped autouse fixture, because pytest runs a
    suite's own autouse fixtures before a plugin's: a session-scoped seed
    fixture calling ``fr.open_session()`` must already be routed to the suite's
    database, and must find the schema there, whichever way the plugin was
    registered. The per-test fixtures layer rollback's pinned factories on top
    and restore this layer afterwards. pytest's rootdir anchors relative
    Alembic paths, so the invocation directory does not decide which config is
    found.
    """
    if getattr(session.config.option, "collectonly", False):
        return
    if _routing_stack and _routing_stack[-1][0] is session:
        # A second perform_collect in one session must not push twice.
        return
    globals_obj = _get_restly_context()
    _routing_stack.append(
        (session, globals_obj.test_make_session, globals_obj.test_async_make_session)
    )
    setup = _current_setup()
    if setup is None:
        return

    recorded_sync, recorded_async = _source_factories()
    globals_obj.test_make_session = (
        recorded_sync if recorded_sync is not None else cast(Any, _SYNC_LEG_TRIPWIRE)
    )
    globals_obj.test_async_make_session = (
        recorded_async
        if recorded_async is not None
        else cast(Any, _ASYNC_LEG_TRIPWIRE)
    )
    try:
        _create_schema(setup, root=Path(session.config.rootpath))
    except RestlyConfigurationError as error:
        # As a UsageError pytest prints the message and stops; anything else
        # raised from a hook is reported as an INTERNALERROR with a pluggy
        # traceback.
        raise pytest.UsageError(str(error)) from error


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    # Restore rather than clear, for the same reason as the mode override: a
    # nested in-process run must neither leak its routing outward nor take the
    # outer run's away. A run that never pushed -- collection was skipped, or
    # an earlier hookimpl raised before ours ran -- must not pop another run's
    # entry either, so the top entry has to be this session's own.
    if not _routing_stack or _routing_stack[-1][0] is not session:
        return
    globals_obj = _get_restly_context()
    _, previous_sync, previous_async = _routing_stack.pop()
    globals_obj.test_make_session = previous_sync
    globals_obj.test_async_make_session = previous_async


@pytest.fixture(autouse=True)
def _restly_managed_isolation(request: pytest.FixtureRequest) -> Iterator[None]:
    """Give every test a clean database when ``configure_tests()`` was called.

    Inert (rather than skipping) when nothing is configured, so tests that never
    touch the database still run.
    """
    setup = _current_setup()
    if setup is None:
        yield
        return

    yield from _managed_isolation(request, setup, _cleanup_mode())


def _managed_isolation(
    request: pytest.FixtureRequest, setup: Any, mode: str
) -> Iterator[None]:
    """The per-test half of managed isolation.

    Routing is run-wide (``_restly_managed_routing`` holds the recorded
    factories and the unnamed-leg tripwires in the test override for the whole
    session); what remains per test is rollback's pinned factories, or delete
    mode's cleaning.
    """
    if mode == ROLLBACK:
        recorded_sync, recorded_async = _source_factories()
        # Both legs, not the first one found: each session fixture swaps only
        # its own factory, so activating one in a suite that configured both
        # leaves the other's routes committing for real. The fixtures install
        # their pinned factory and restore the run-wide layer themselves.
        if recorded_sync is not None:
            request.getfixturevalue("restly_session")
        if recorded_async is not None:
            request.getfixturevalue("restly_async_session")
    elif mode == DELETE:
        # Before, not after: whatever the last test wrote is still there when
        # the run ends, which is the point of choosing this mode.
        if not _clean_database_sync(setup):
            # Async-only: cleaning must run on the loop the test uses, so it
            # goes through a fixture rather than a loop of its own.
            request.getfixturevalue("_restly_async_delete")
    yield


@pytest.fixture
def restly_app() -> FastAPI:
    """Return the application under test.

    ``fr.testing.configure_tests(app=...)`` sets this. Without it you get a bare
    ``FastAPI()``; override the fixture in your ``conftest.py`` to supply your own.
    """
    setup = _current_setup()
    if setup is not None and setup.app is not None:
        return setup.app
    return FastAPI()


def _reject_loop_bound_pinning() -> None:
    """Refuse the one combination rollback mode cannot serve: client requests
    over a connection asyncpg bound to the test's loop.

    asyncpg ties every connection to the event loop that created it. With only
    the async leg named, rollback pins one asyncpg connection on the test's
    loop, and ``restly_client``'s requests -- run on the client's own portal
    thread -- would die mid-test with "attached to a different loop". A named
    sync leg changes the picture: the pinned connection is then a sync one any
    loop may use, so only the async-only shape is refused.
    """
    setup = _current_setup()
    if setup is None or _cleanup_mode() != ROLLBACK:
        return
    if setup.make_session is not None or setup.async_make_session is None:
        return
    engine = _resolve_engine(setup.async_make_session.kw.get("bind"))
    sync_engine = getattr(engine, "sync_engine", engine)
    if getattr(getattr(sync_engine, "dialect", None), "driver", "") != "asyncpg":
        return
    raise RestlyConfigurationError(
        'db_cleanup="rollback" pins one connection for the whole test, and '
        "asyncpg binds every connection to the event loop that created it; "
        "requests driven through restly_client run on the client's own loop "
        'and would fail mid-test with "attached to a different loop". Name '
        "the sync database as well (database_url= alongside "
        "async_database_url=, pointing at the same database) -- the pinned "
        "connection is then a sync one any loop may use -- or switch to "
        'db_cleanup="delete", where every session opens its own connection '
        "on the loop that runs it."
    )


@pytest.fixture
def restly_client(restly_app) -> Iterator[RestlyTestClient]:
    """A test client for ``restly_app``, entered so the app's lifespan runs.

    Starlette's client only runs startup and shutdown inside its context manager.
    Returning an unentered one meant a ``lifespan=`` that opens a pool, warms a
    cache or registers a dependency never ran, and the failure surfaced far from
    here as a missing resource.
    """
    _reject_loop_bound_pinning()
    try:
        from .testing._client import RestlyTestClient
    except ModuleNotFoundError as exc:
        # Newer Starlette's testclient requires httpx2 (name="httpx2"); our own
        # _client.py import raises name="httpx". Both mean the test client is
        # missing.
        if exc.name in {"httpx", "httpx2"}:
            raise ModuleNotFoundError(_TESTING_EXTRA_MESSAGE, name=exc.name) from exc
        raise

    # Bound before the block: __enter__ returns self at runtime, but Starlette
    # annotates it as the base TestClient, so `with ... as client` would type
    # the fixture without the subclass's status-code assertions.
    client = RestlyTestClient(restly_app)
    with client:
        yield client


if pytest_asyncio is None:

    @pytest.fixture
    def _restly_async_delete() -> None:  # pyright: ignore[reportRedeclaration]
        # Only reachable in an async suite, which needs the extra anyway.
        raise ModuleNotFoundError(_TESTING_EXTRA_MESSAGE, name="pytest_asyncio")

else:

    @pytest_asyncio.fixture
    async def _restly_async_delete() -> AsyncIterator[None]:
        """Empty the tables over the async leg, on the test's own event loop.

        Requested by the autouse fixture only for an async-only suite in delete
        mode. Running it on a loop of its own would hand a pooled connection to a
        loop that closes before the test does, which is how asyncpg fails.
        """
        setup = _current_setup()
        if setup is not None:
            await _clean_database_async(setup)
        yield
