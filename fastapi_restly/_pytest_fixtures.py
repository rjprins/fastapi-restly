from __future__ import annotations

import os
import weakref
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, event
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, async_sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.orm import Session as SA_Session
from sqlalchemy.orm import sessionmaker
from sqlalchemy.util.concurrency import greenlet_spawn

from ._test_setup import (
    DB_CLEANUP_ENV_VAR,
    DB_CLEANUP_MODES,
    DELETE,
    NONE,
    ROLLBACK,
    _clean_database_async,
    _clean_database_sync,
    _create_schema,
    _current_setup,
    _is_memory_sqlite,
    _resolve_db_cleanup,
    _resolve_engine,
    _source_factories,
    _validate_database_sources,
)
from .db._globals import RestlyContext, _fr_globals, _get_restly_context
from .exc import RestlyConfigurationError

if TYPE_CHECKING:
    from .testing._client import AsyncRestlyTestClient, RestlyTestClient

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


async def _dispose_async_test_engine(
    engine: AsyncEngine, *, former_pool: Any | None = None
) -> None:
    """Empty a test engine's pool before its current event loop goes away.

    SQLAlchemy requires a pooled ``AsyncEngine`` to be disposed before it is
    reused on another loop.  Restly creates those loop boundaries, so it also
    closes the checked-in connections it used.  In-memory SQLite is the one
    exception: its single connection is the database itself.
    """
    if not _is_memory_sqlite(engine.url):
        current_pool = engine.sync_engine.pool
        if former_pool is not None and former_pool is not current_pool:
            # Application shutdown may have called engine.dispose() while the
            # test's connection was checked out. SQLAlchemy replaces the pool;
            # dispose the former pool after our connection has returned to it.
            await greenlet_spawn(former_pool.dispose)
        await engine.dispose()


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
        "The session factory passed to fr.configure() has per-mapper binds "
        f"({mapped}), which the test fixtures cannot isolate: those models would "
        "be routed to their own engine, outside the connection this test pins, "
        "and their writes would be committed rather than rolled back. Configure "
        "the tests with a single-bind sessionmaker."
    )


def _cleanup_mode() -> str:
    """The cleanup mode in force, or ``rollback`` when the suite never opted in.

    A suite that does not call ``configure_tests()`` still gets the rollback
    behavior the session fixtures have always had when it requests one.
    """
    setup = _current_setup()
    if setup is None:
        return ROLLBACK
    return _resolve_db_cleanup(setup, _db_cleanup_override)


def _application_context_copy() -> RestlyContext:
    """Copy application configuration without test-loop overrides.

    A synchronous TestClient runs ASGI work in a portal thread. Giving that
    execution domain its own context lets it install a portal-owned async
    transaction without exposing that loop-bound factory to the test thread.
    """
    source = _get_restly_context()
    copied = RestlyContext()
    for name in RestlyContext.__slots__:
        setattr(copied, name, getattr(source, name))
    copied.test_make_session = None
    copied.test_async_make_session = None
    return copied


class _BlockedAsyncSessionFactory:
    """Fail direct async access while TestClient owns the async execution loop."""

    _message = (
        "This test cannot open an async database session outside restly_client: "
        "the synchronous client runs the application and its transaction on a "
        "separate event loop. Use restly_async_client when the test or one of "
        "its fixtures needs direct async database access."
    )

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise RestlyConfigurationError(self._message)

    @property
    def kw(self) -> Any:
        raise RestlyConfigurationError(self._message)

    def begin(self) -> Any:
        raise RestlyConfigurationError(self._message)


@contextmanager
def _blocked_async_test_access() -> Iterator[None]:
    context = _get_restly_context()
    previous = context.test_async_make_session
    context.test_async_make_session = _BlockedAsyncSessionFactory()  # type: ignore[assignment]
    try:
        yield
    finally:
        context.test_async_make_session = previous


@pytest.fixture
def _shared_connection():
    # One pinned connection shared by the sync and async fixtures, so a test that
    # uses both sees a single database. Each request during the test joins this
    # connection's outer transaction through a SAVEPOINT (create_savepoint mode);
    # the outer transaction is never committed and rolls back at teardown, so no
    # test data is ever persisted. Async-only projects have no sync sessionmaker;
    # the async test scope pins its own connection in that case.
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


@pytest.fixture
def _restly_sync_scope(_shared_connection):
    """Own sync cleanup and the per-test factory; create no public session."""
    original = _source_factories()[0]
    mode = _cleanup_mode()
    setup = _current_setup()

    if mode != ROLLBACK:
        if mode == DELETE and setup is not None and original is not None:
            _clean_database_sync(setup)
        yield original
        return

    if original is None:
        yield None
        return

    _reject_per_mapper_binds(original)
    isolated_make_session = sessionmaker(
        class_=original.class_,
        **{
            **original.kw,
            "bind": _shared_connection,
            "join_transaction_mode": "create_savepoint",
        },
    )
    context = _get_restly_context()
    previous = context.test_make_session
    context.test_make_session = isolated_make_session
    try:
        yield isolated_make_session
    finally:
        context.test_make_session = previous


if pytest_asyncio is None:

    @pytest.fixture
    def _restly_async_preclean() -> None:  # pyright: ignore[reportRedeclaration]
        raise ModuleNotFoundError(_TESTING_EXTRA_MESSAGE, name="pytest_asyncio")

    @pytest.fixture
    def _restly_async_scope() -> None:  # pyright: ignore[reportRedeclaration]
        raise ModuleNotFoundError(_TESTING_EXTRA_MESSAGE, name="pytest_asyncio")

    @pytest.fixture
    def restly_async_session(_restly_async_scope) -> None:  # pyright: ignore[reportRedeclaration]
        # The else-branch defines the real async fixture; this stub only
        # runs when the optional ``pytest_asyncio`` extra isn't installed.
        # Pyright cannot model mutually exclusive module-level branches.
        raise ModuleNotFoundError(_TESTING_EXTRA_MESSAGE, name="pytest_asyncio")

    @pytest.fixture
    def restly_async_client() -> None:  # pyright: ignore[reportRedeclaration]
        raise ModuleNotFoundError(_TESTING_EXTRA_MESSAGE, name="pytest_asyncio")

else:

    @asynccontextmanager
    async def _async_rollback_factory(
        shared_connection=None,
    ) -> AsyncIterator[async_sessionmaker[SA_AsyncSession]]:
        """Install one savepoint factory on the caller's event loop."""
        original = _source_factories()[1]
        if original is None:
            raise RestlyConfigurationError(
                "Async rollback isolation needs an async sessionmaker configured "
                "through fr.configure()."
            )

        _reject_per_mapper_binds(original)
        async_engine = _resolve_engine(original.kw["bind"])

        @asynccontextmanager
        async def pinned_connection() -> AsyncIterator[AsyncConnection]:
            if shared_connection is not None:
                yield AsyncConnection(async_engine, sync_connection=shared_connection)
                return

            _install_sqlite_savepoint_fix(async_engine)
            owned_pool = async_engine.sync_engine.pool
            try:
                async with async_engine.connect() as connection:
                    transaction = await connection.begin()
                    try:
                        yield connection
                    finally:
                        if transaction.is_active:
                            await transaction.rollback()
            finally:
                await _dispose_async_test_engine(async_engine, former_pool=owned_pool)

        async with pinned_connection() as async_connection:
            isolated_make_session = async_sessionmaker(
                class_=original.class_,
                **{
                    **original.kw,
                    "bind": async_connection,
                    "join_transaction_mode": "create_savepoint",
                },
            )
            globals_obj = _get_restly_context()
            previous = globals_obj.test_async_make_session
            globals_obj.test_async_make_session = isolated_make_session
            try:
                yield isolated_make_session
            finally:
                globals_obj.test_async_make_session = previous

    @asynccontextmanager
    async def _async_test_scope(
        shared_connection=None, *, clean_database: bool = True
    ) -> AsyncIterator[async_sessionmaker[SA_AsyncSession] | None]:
        """Own one async execution domain's cleanup, override, and disposal."""
        original = _source_factories()[1]
        if original is None:
            yield None
            return

        mode = _cleanup_mode()
        if mode == ROLLBACK:
            async with _async_rollback_factory(shared_connection) as isolated:
                yield isolated
            return

        setup = _current_setup()
        if (
            clean_database
            and mode == DELETE
            and setup is not None
            and setup.make_session is None
        ):
            await _clean_database_async(setup)

        async_engine = _resolve_engine(original.kw["bind"])
        try:
            yield original
        finally:
            await _dispose_async_test_engine(async_engine)

    @pytest_asyncio.fixture
    async def _restly_async_preclean() -> AsyncIterator[None]:
        """Clean an async-only database before any user fixture can seed it."""
        setup = _current_setup()
        original = _source_factories()[1]
        if setup is not None and original is not None:
            try:
                await _clean_database_async(setup)
            finally:
                await _dispose_async_test_engine(_resolve_engine(original.kw["bind"]))
        yield

    @pytest_asyncio.fixture
    async def _restly_async_scope(
        _shared_connection, request: pytest.FixtureRequest | None = None
    ) -> AsyncIterator[async_sessionmaker[SA_AsyncSession] | None]:
        """Own async database state on pytest's event loop."""
        if (
            _current_setup() is None
            and request is not None
            and "restly_async_session" not in request.fixturenames
        ):
            # Merely requesting the HTTP client must not dispose or replace an
            # application's live engine before the suite opts into management.
            yield None
            return
        async with _async_test_scope(_shared_connection) as make_session:
            yield make_session

    @pytest_asyncio.fixture
    async def restly_async_session(
        _restly_async_scope,
    ) -> AsyncIterator[SA_AsyncSession]:
        """
        Pytest fixture providing the suite's async database session.

        The async equivalent of :func:`restly_session`, and like it, what it
        yields follows the suite's ``db_cleanup`` mode: under the default
        ``"rollback"`` it is the savepoint-isolated session described below;
        under ``"delete"`` and ``"none"`` it is a plain session on the
        configured database, since rolling it back would undo writes those
        modes mean to commit.

        Under rollback, each request during the test builds its own real
        ``AsyncSession`` that joins a never-committed outer transaction through
        a SAVEPOINT (SQLAlchemy's ``create_savepoint`` mode), so a request's
        ``commit()`` and ``rollback()`` behave as in production. The outer
        transaction rolls back at teardown, leaving the database clean for the
        next test -- nothing is ever persisted. When a sync sessionmaker is
        also configured, this fixture shares the sync fixture's pinned
        connection, so a test that uses both sees one database.

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
        make_session = _restly_async_scope
        if make_session is None:
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

        session = make_session()
        try:
            yield session
        finally:
            await session.close()

    @pytest_asyncio.fixture
    async def restly_async_client(
        restly_app, _restly_async_scope
    ) -> AsyncIterator[AsyncRestlyTestClient]:
        """An async HTTP client that runs the application's lifespan.

        It uses pytest's event loop, so when ``restly_async_session`` is also
        requested, direct database access and HTTP share one loop and, under
        rollback, one transaction. It remains useful without a database
        configuration.
        """
        try:
            from asgi_lifespan import LifespanManager
        except ModuleNotFoundError as exc:
            if exc.name == "asgi_lifespan":
                raise ModuleNotFoundError(
                    _TESTING_EXTRA_MESSAGE, name=exc.name
                ) from exc
            raise

        try:
            from .testing._client import AsyncRestlyTestClient
        except ModuleNotFoundError as exc:
            if exc.name in {"httpx", "httpx2"}:
                raise ModuleNotFoundError(
                    _TESTING_EXTRA_MESSAGE, name=exc.name
                ) from exc
            raise

        async with LifespanManager(
            restly_app, startup_timeout=None, shutdown_timeout=None
        ) as manager:
            client = AsyncRestlyTestClient(restly_app, _transport_app=manager.app)
            async with client:
                yield client


class _AsyncEngineLifespanApp:
    """Own async database resources on TestClient's ASGI portal loop."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.context = _application_context_copy()

    async def __call__(self, scope, receive, send) -> None:
        # Lifespan and request tasks each receive this same portal-owned context.
        # The lifespan installs the factory on the context object; requests then
        # resolve it while entering their own ContextVar scope below.
        with self.context:
            if scope["type"] != "lifespan":
                await self.app(scope, receive, send)
                return

            if pytest_asyncio is None:  # pragma: no cover - testing extra guard
                raise ModuleNotFoundError(_TESTING_EXTRA_MESSAGE, name="pytest_asyncio")
            async with _async_test_scope(clean_database=False):
                await self.app(scope, receive, send)


@pytest.fixture
def restly_session(_restly_sync_scope) -> Iterator[SA_Session]:
    """
    Pytest fixture providing the suite's database session.

    What it yields follows the suite's ``db_cleanup`` mode: under the default
    ``"rollback"`` it is the savepoint-isolated session described below; under
    ``"delete"`` and ``"none"`` it is a plain session on the configured
    database, since rolling it back would undo writes those modes mean to
    commit.

    Under rollback, the session joins a never-committed outer transaction
    through a SAVEPOINT (SQLAlchemy's ``create_savepoint`` mode). Every request
    during the test builds its own real session on the same pinned connection,
    so a request's ``commit()`` and ``rollback()`` behave as in production, and
    the outer transaction rolls back at teardown, leaving the database clean
    for the next test -- nothing is ever persisted.

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
    make_session = _restly_sync_scope
    if make_session is None:
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

    session = make_session()
    try:
        yield session
    finally:
        session.close()


def pytest_collection_finish(session: pytest.Session) -> None:
    """Verify the recorded application configuration and build its test schema."""
    if getattr(session.config.option, "collectonly", False):
        return
    setup = _current_setup()
    if setup is None:
        return

    try:
        _validate_database_sources(setup, _cleanup_mode())
        _create_schema(setup, root=Path(session.config.rootpath))
    except RestlyConfigurationError as error:
        # As a UsageError pytest prints the message and stops; anything else
        # raised from a hook is reported as an INTERNALERROR with a pluggy
        # traceback.
        raise pytest.UsageError(str(error)) from error


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

    mode = _cleanup_mode()
    _validate_database_sources(setup, mode)
    yield from _managed_isolation(request, setup, mode)


def _managed_isolation(
    request: pytest.FixtureRequest, setup: Any, mode: str
) -> Iterator[None]:
    """Select isolation scopes by execution domain, independent of consumers."""
    recorded_sync, recorded_async = _source_factories()
    if recorded_sync is not None:
        request.getfixturevalue("_restly_sync_scope")

    portal_owns_async = (
        recorded_async is not None
        and "restly_client" in request.fixturenames
        and (recorded_sync is None or mode != ROLLBACK)
    )
    if portal_owns_async and mode == DELETE and recorded_sync is None:
        request.getfixturevalue("_restly_async_preclean")
    if recorded_async is not None and not portal_owns_async:
        request.getfixturevalue("_restly_async_scope")

    block_split_access = portal_owns_async and (
        mode == ROLLBACK or _async_source_is_loop_bound(setup)
    )
    if block_split_access:
        _reject_split_async_usage(request)
        with _blocked_async_test_access():
            yield
        return

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


def _client_async_engine_policy() -> str | None:
    """Return the async-engine lifecycle the synchronous client must own."""
    setup = _current_setup()
    if setup is None or setup.async_make_session is None:
        return None
    if _cleanup_mode() == ROLLBACK:
        if setup.make_session is None:
            return ROLLBACK
        return None
    return _cleanup_mode()


def _async_source_is_loop_bound(setup: Any) -> bool:
    """Return whether a configured driver rejects pooled cross-loop reuse."""
    factory = setup.async_make_session
    if factory is None:
        return False
    engine = _resolve_engine(factory.kw["bind"])
    # aiosqlite can hand sequential operations on one connection to different
    # loops. Network async drivers generally cannot; SQLAlchemy likewise
    # requires disposing pooled AsyncEngines before moving them across loops.
    return engine.dialect.name != "sqlite"


def _reject_split_async_usage(request: pytest.FixtureRequest) -> None:
    """Keep direct async work and application work on one event loop."""
    conflicts = {"restly_async_client", "restly_async_session"}.intersection(
        request.fixturenames
    )
    if not conflicts:
        return
    raise RestlyConfigurationError(
        "An async database test cannot combine restly_client with "
        f"{', '.join(sorted(conflicts))}: the synchronous client runs the "
        "application on its own event loop, while the async fixtures run on "
        "pytest's event loop. Use restly_async_client for HTTP when a test needs "
        "async fixtures."
    )


@pytest.fixture
def restly_client(
    restly_app, request: pytest.FixtureRequest
) -> Iterator[RestlyTestClient]:
    """A test client for ``restly_app``, entered so the app's lifespan runs.

    Starlette's client only runs startup and shutdown inside its context manager.
    Returning an unentered one meant a ``lifespan=`` that opens a pool, warms a
    cache or registers a dependency never ran, and the failure surfaced far from
    here as a missing resource.
    """
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
    transport_app = None
    async_engine_policy = _client_async_engine_policy()
    setup = _current_setup()
    if async_engine_policy is not None and (
        async_engine_policy == ROLLBACK
        or (setup is not None and _async_source_is_loop_bound(setup))
    ):
        _reject_split_async_usage(request)
    if async_engine_policy is not None:
        transport_app = _AsyncEngineLifespanApp(restly_app)
    client = RestlyTestClient(restly_app, _transport_app=transport_app)
    with client:
        yield client
