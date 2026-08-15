import warnings
from collections.abc import AsyncIterator, Callable, Iterator
from inspect import signature
from typing import Annotated, Any, cast

from fastapi import Depends, FastAPI
from sqlalchemy import Connection, Engine, MetaData, create_engine, event
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.orm import Session as SA_Session

from .._exception_handlers import register_default_exception_handlers
from ..exc import RestlyConfigurationError, RestlyUncommittedChangesWarning
from ._engine_defaults import apply_connect_hooks, engine_options
from ._globals import _fr_globals


def _setup_async_database_connection(
    async_database_url: str | None = None,
    *,
    async_engine: AsyncEngine | None = None,
    async_make_session: async_sessionmaker[Any] | None = None,
) -> async_sessionmaker[Any]:
    if not async_make_session:
        if not async_engine:
            async_engine = create_async_engine(
                async_database_url,  # type: ignore[arg-type]
                **engine_options(async_database_url),  # type: ignore[arg-type]
            )
            apply_connect_hooks(async_engine)
        async_make_session = async_sessionmaker(
            bind=async_engine, autoflush=False, expire_on_commit=False
        )

    factory_kw = getattr(async_make_session, "kw", None)
    if factory_kw is not None and factory_kw.get("expire_on_commit", True):
        warnings.warn(
            "The async session factory passed to fr.configure() has "
            "expire_on_commit=True. Restly's write handlers commit inside the "
            "request, so the commit expires every loaded attribute on the "
            "object the response is built from. Reading one back then happens in "
            "plain async context, where SQLAlchemy raises MissingGreenlet: always "
            "in the response serializer, and earlier too if an after_commit hook "
            "reads the committed object. Pass expire_on_commit=False to your "
            "async_sessionmaker.",
            stacklevel=3,
        )

    _fr_globals.async_database_url = async_database_url
    _fr_globals.async_make_session = async_make_session
    return async_make_session


def _setup_database_connection(
    database_url: str | None = None,
    *,
    engine: Engine | None = None,
    make_session: sessionmaker[Any] | None = None,
) -> sessionmaker[Any]:
    if make_session is None:
        if engine is None:
            # create_engine is overloaded, so the options splat costs pyright
            # the return type; the cast restores it.
            engine = cast(
                Engine,
                create_engine(
                    database_url,  # type: ignore[arg-type]
                    **engine_options(database_url),  # type: ignore[arg-type]
                ),
            )
            apply_connect_hooks(engine)
        make_session = sessionmaker(bind=engine, expire_on_commit=False)

    _fr_globals.database_url = database_url
    _fr_globals.make_session = make_session
    return make_session


async def _health() -> dict[str, str]:
    """Liveness response for the route ``fr.configure(health=...)`` mounts."""
    return {"status": "ok"}


def _register_health_route(app: FastAPI, path: str) -> None:
    """Mount the liveness endpoint at ``path``.

    Skips if ``app`` already has a route at that path, whatever its method: a
    repeated :func:`configure` call does not mount a second one, and an
    application's own endpoint there is left in place.
    """
    if any(getattr(route, "path", None) == path for route in app.routes):
        return
    # name= keeps the private handler out of the generated operationId.
    app.add_api_route(path, _health, methods=["GET"], name="health")


def configure(
    app: FastAPI | None = None,
    *,
    async_database_url: str | None = None,
    async_engine: AsyncEngine | None = None,
    async_make_session: async_sessionmaker[Any] | None = None,
    database_url: str | None = None,
    engine: Engine | None = None,
    make_session: sessionmaker[Any] | None = None,
    session_generator: Callable[[], AsyncIterator[SA_AsyncSession]] | None = None,
    sync_session_generator: Callable[[], Iterator[SA_Session]] | None = None,
    warn_on_misuse: bool | None = None,
    warn_on_uncommitted: bool | None = None,
    install_default_exception_handlers: bool = True,
    health: str | None = None,
) -> None:
    """Configure FastAPI-Restly. Call once at startup.

    Async support comes from ``async_database_url``, ``async_engine``, or
    ``async_make_session``; sync support from ``database_url``, ``engine``, or
    ``make_session``. Pass both sets if the application uses both.

    A URL is the one form Restly builds an engine from, and that engine gets
    defaults suited to a web application: ``StaticPool`` for in-memory SQLite,
    ``PRAGMA foreign_keys=ON`` on every SQLite connection, and ``pool_pre_ping``
    with ``pool_recycle`` on PostgreSQL. Pool sizing is left alone, and so is
    anything written into the database itself, which is why ``journal_mode=WAL``
    is not among them. Every other form is used as given, so passing ``engine=``
    declines all of this.

    Restly owns the commit: the CRUD handlers and ``write_action`` run
    ``before_commit`` -> commit -> ``after_commit`` around your domain logic. A
    custom session generator constructs, yields, and cleans up, and must not
    commit. A custom write route brackets its mutation with ``write_action(...)``
    or commits the session itself.

    :func:`fastapi_restly.testing.configure_tests` freezes the database sources
    for the rest of that pytest process, so configure the application before
    enabling managed testing; changing them afterwards raises.

    :param app: Application to install the default exception handlers and the
        health route on.
    :param async_database_url: Async URL to build an
        :class:`~sqlalchemy.ext.asyncio.AsyncEngine` from.
    :param async_engine: Async engine to use as given.
    :param async_make_session: ``async_sessionmaker`` to use as given.
    :param database_url: Sync URL to build an
        :class:`~sqlalchemy.engine.Engine` from.
    :param engine: Sync engine to use as given.
    :param make_session: ``sessionmaker`` to use as given.
    :param session_generator: Callable yielding the
        :class:`~sqlalchemy.ext.asyncio.AsyncSession` for each request.
    :param sync_session_generator: Callable yielding the
        :class:`~sqlalchemy.orm.Session` for each request.
    :param warn_on_misuse: Emit
        :class:`~fastapi_restly.exc.RestlyMisuseWarning` when ``include_view``
        registers a view with a route-shell override, a direct
        ``session.commit()``, a hand-rolled CRUD route set on a bare ``View``,
        or a scalar foreign key typed as an ``IDRef`` / ``IDSchema`` reference
        instead of ``fr.MustExist``. Off by default; set it before registering
        views.
    :param warn_on_uncommitted: Emit
        :class:`~fastapi_restly.exc.RestlyUncommittedChangesWarning` when a
        request finishes with uncommitted changes. On by default. Suppress one
        deliberate case with ``session.info["_fr_suppress_uncommitted"] = True``
        rather than turning the check off.
    :param install_default_exception_handlers: Install the translator that
        turns :class:`~sqlalchemy.exc.IntegrityError` into HTTP 409. On by
        default. Without ``app``, the first
        :func:`~fastapi_restly.views.include_view` installs them instead.
    :param health: Path to mount a liveness endpoint on, such as ``"/health"``.
        It answers ``200`` with ``{"status": "ok"}``, appears in the OpenAPI
        schema, and makes no database round-trip. Off unless set, and requires
        ``app``. A route already mounted at that path is left in place.
        Readiness is a separate endpoint of your own, not a mode of this one.
    :raises TypeError: No setup argument was given.
    :raises RestlyConfigurationError: ``health`` is not an absolute path or was
        passed without ``app``, or the database configuration changed after
        ``configure_tests()`` recorded it.
    """
    configures_database = any(
        (
            async_database_url is not None,
            async_engine is not None,
            async_make_session is not None,
            database_url is not None,
            engine is not None,
            make_session is not None,
            session_generator is not None,
            sync_session_generator is not None,
        )
    )
    if configures_database and _fr_globals.database_configuration_locked:
        raise RestlyConfigurationError(
            "Restly's database configuration cannot be changed after "
            "fr.testing.configure_tests() recorded it. This is often a "
            "per-test factory fixture (e.g. a @pytest.fixture that calls "
            "create_app(...) again for every test) calling fr.configure() "
            "after collection. Build the application once in conftest.py "
            "before calling configure_tests(), and do not reconfigure its "
            "database later during collection or application lifespan "
            "startup."
        )

    if not any(
        (
            configures_database,
            warn_on_misuse is not None,
            warn_on_uncommitted is not None,
            health is not None,
            app is not None and install_default_exception_handlers,
        )
    ):
        raise TypeError("fr.configure() requires at least one setup argument.")

    # Validate before applying anything, so a bad health path cannot leave a
    # half-configured process behind.
    if health is not None:
        if not health.startswith("/"):
            raise RestlyConfigurationError(
                f"fr.configure(health={health!r}) needs a path starting with "
                "'/', such as '/health'."
            )
        if app is None:
            raise RestlyConfigurationError(
                f"fr.configure(health={health!r}) also needs the app to mount "
                "the route on: fr.configure(app, health=...)."
            )

    if warn_on_misuse is not None:
        _fr_globals.warn_on_misuse = warn_on_misuse
    if warn_on_uncommitted is not None:
        _fr_globals.warn_on_uncommitted = warn_on_uncommitted
    if (
        async_database_url is not None
        or async_engine is not None
        or async_make_session is not None
    ):
        _setup_async_database_connection(
            async_database_url=async_database_url,
            async_engine=async_engine,
            async_make_session=async_make_session,
        )
    if database_url is not None or engine is not None or make_session is not None:
        _setup_database_connection(
            database_url=database_url, engine=engine, make_session=make_session
        )
    if session_generator is not None:
        _fr_globals.session_generator = session_generator
    if sync_session_generator is not None:
        _fr_globals.sync_session_generator = sync_session_generator
    if app is not None:
        if install_default_exception_handlers:
            register_default_exception_handlers(app)
        if health is not None:
            _register_health_route(app, health)


def _active_make_session():
    """The sync factory in force: a test's, if one installed it."""
    return _fr_globals.test_make_session or _fr_globals.make_session


def _active_async_make_session():
    """The async factory in force: a test's, if one installed it."""
    return _fr_globals.test_async_make_session or _fr_globals.async_make_session


def get_async_engine() -> AsyncEngine:
    """Return the async engine registered via configure()."""
    if _fr_globals.async_make_session is None:
        raise RestlyConfigurationError(
            "Call fr.configure() before using get_async_engine()."
        )
    # This is a read-only lookup of application configuration. Test overrides may
    # be bound to a pinned connection or deliberately refuse session creation;
    # neither changes the engine that configure() registered.
    bind = _fr_globals.async_make_session.kw["bind"]
    if isinstance(bind, AsyncConnection):
        return bind.engine
    return bind


def get_engine() -> Engine:
    """Return the sync engine registered via configure()."""
    if _fr_globals.make_session is None:
        raise RestlyConfigurationError("Call fr.configure() before using get_engine().")
    bind = _active_make_session().kw["bind"]
    # Under restly_session the factory is bound to a pinned Connection; resolve
    # its engine so this keeps returning the real Engine.
    if isinstance(bind, Connection):
        return bind.engine
    return bind


def _resolve_metadata(base_or_metadata: type[DeclarativeBase] | MetaData) -> MetaData:
    if isinstance(base_or_metadata, MetaData):
        return base_or_metadata
    metadata = getattr(base_or_metadata, "metadata", None)
    if isinstance(metadata, MetaData):
        return metadata
    raise TypeError(
        "create_all() expects a DeclarativeBase subclass or a MetaData; got "
        f"{base_or_metadata!r}"
    )


def create_all(base_or_metadata: type[DeclarativeBase] | MetaData) -> None:
    """Create all tables for ``base_or_metadata`` on the configured sync engine.

    A dev/demo convenience over ``metadata.create_all(engine)`` so a quickstart
    can create its schema without reaching for the raw engine::

        fr.db.create_all(Base)  # or fr.db.create_all(Base.metadata)

    Accepts a ``DeclarativeBase`` subclass (its ``.metadata`` is used) or a
    ``MetaData``. Requires :func:`configure` first. Use Alembic migrations in
    production.
    """
    metadata = _resolve_metadata(base_or_metadata)
    if _fr_globals.make_session is None:
        raise RestlyConfigurationError("Call fr.configure() before using create_all().")
    # Create against the configured bind: the engine in production, or the pinned
    # Connection under restly_session so the schema is visible to the test's
    # isolated sessions instead of silently landing on a throwaway connection.
    metadata.create_all(_active_make_session().kw["bind"])


async def async_create_all(base_or_metadata: type[DeclarativeBase] | MetaData) -> None:
    """Async equivalent of :func:`create_all`, on the configured async engine.

    Usage::

        await fr.db.async_create_all(Base)
    """
    metadata = _resolve_metadata(base_or_metadata)
    if _fr_globals.async_make_session is None:
        raise RestlyConfigurationError(
            "Call fr.configure() before using async_create_all()."
        )
    bind = _active_async_make_session().kw["bind"]
    if isinstance(bind, AsyncConnection):
        # Under restly_async_session: create on the pinned connection, inside the
        # outer transaction, so the tables are visible to the test's sessions.
        await bind.run_sync(metadata.create_all)
    else:
        async with bind.begin() as conn:
            await conn.run_sync(metadata.create_all)


def _should_warn_uncommitted() -> bool:
    """The uncommitted-changes check applies whenever ``warn_on_uncommitted`` is
    on. Restly owns the commit, so changes still pending when a request ends are
    the tell of a custom write route that never committed.
    """
    return _fr_globals.warn_on_uncommitted


def _mark_uncommitted(session: SA_Session, flush_context: Any = None) -> None:
    session.info["_fr_uncommitted"] = True


def _clear_uncommitted(session: SA_Session, *args: Any) -> None:
    session.info.pop("_fr_uncommitted", None)


def _arm_uncommitted_warning(session: SA_AsyncSession | SA_Session) -> None:
    """Register flush/commit/rollback listeners so an uncommitted flush at the
    end of a request can be detected. Async sessions delegate to a sync
    ``Session``; that is where ORM events fire (and whose ``info`` is shared).
    """
    if not _should_warn_uncommitted():
        return
    target = getattr(session, "sync_session", session)
    try:
        event.listen(target, "after_flush", _mark_uncommitted)
        event.listen(target, "after_commit", _clear_uncommitted)
        event.listen(target, "after_rollback", _clear_uncommitted)
    except Exception:
        # Best-effort dev aid: unusual sessions (test stubs, or session types
        # without ORM flush events) opt out. Never break a request.
        pass


def _warn_if_uncommitted(session: SA_AsyncSession | SA_Session) -> None:
    """Warn if the request is ending with changes that were flushed but never
    committed (the ``_fr_uncommitted`` flag), or added but never flushed
    (``new``/``dirty``/``deleted``) -- all about to be rolled back. Called only
    on the success path; an endpoint that raised never reaches this point.
    """
    if not _should_warn_uncommitted():
        return
    target = getattr(session, "sync_session", session)
    try:
        if target.info.get("_fr_suppress_uncommitted"):
            return
        uncommitted = bool(
            target.info.get("_fr_uncommitted")
            or target.new
            or target.dirty
            or target.deleted
        )
    except Exception:
        return  # unusual session -> opt out silently
    if uncommitted:
        warnings.warn(
            "Request finished with uncommitted changes in the database session; "
            "they will be rolled back when the session closes. A custom write "
            "route must commit its changes -- bracket the mutation with "
            "write_action(...) (the framework then commits), or reuse "
            "handle_<verb>(). Only if the rollback is intentional (e.g. a "
            "validate-then-rollback dry run), suppress the warning for that "
            'route with session.info["_fr_suppress_uncommitted"] = True.',
            RestlyUncommittedChangesWarning,
            stacklevel=2,
        )


async def _async_generate_session() -> AsyncIterator[SA_AsyncSession]:
    """FastAPI dependency for async database session."""
    if _fr_globals.test_async_make_session is not None:
        async with _fr_globals.test_async_make_session() as session:
            _arm_uncommitted_warning(session)
            yield session
            _warn_if_uncommitted(session)
        return
    if _fr_globals.session_generator is not None:
        async for session in _fr_globals.session_generator():
            _arm_uncommitted_warning(session)
            yield session
            _warn_if_uncommitted(session)
        return
    if _fr_globals.async_make_session is None:
        raise RestlyConfigurationError(
            "Call fr.configure() before using AsyncSessionDep."
        )

    # FastAPI does not support contextmanagers as dependency directly,
    # but it does support generators. Restly owns the commit (the handle
    # design runs it inside ``handle_<verb>`` / ``write_action``), so this
    # dependency only manages the session lifecycle: the context manager rolls
    # back and closes on the way out, and any change a custom route flushed but
    # never committed is discarded (and warned about).
    async with _fr_globals.async_make_session() as session:
        _arm_uncommitted_warning(session)
        yield session
        _warn_if_uncommitted(session)


def _session_dependency(dependency: Callable[..., Any]) -> Any:
    depends = cast(Callable[..., Any], Depends)
    if "scope" in signature(Depends).parameters:
        return depends(dependency, scope="function")
    return depends(dependency)


AsyncSessionDep = Annotated[
    SA_AsyncSession, _session_dependency(_async_generate_session)
]


def _generate_session() -> Iterator[SA_Session]:
    """FastAPI dependency for sync database session."""
    # A test's session source wins over everything, including a generator and
    # anything reconfigured after the test started.
    if _fr_globals.test_make_session is not None:
        with _fr_globals.test_make_session() as session:
            _arm_uncommitted_warning(session)
            yield session
            _warn_if_uncommitted(session)
        return
    if _fr_globals.sync_session_generator is not None:
        for session in _fr_globals.sync_session_generator():
            _arm_uncommitted_warning(session)
            yield session
            _warn_if_uncommitted(session)
        return
    if _fr_globals.make_session is None:
        raise RestlyConfigurationError("Call fr.configure() before using SessionDep.")

    with _fr_globals.make_session() as session:
        _arm_uncommitted_warning(session)
        yield session
        _warn_if_uncommitted(session)


SessionDep = Annotated[SA_Session, _session_dependency(_generate_session)]
