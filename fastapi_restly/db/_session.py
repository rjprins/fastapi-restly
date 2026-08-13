import warnings
from collections.abc import AsyncIterator, Callable, Iterator
from inspect import signature
from typing import Annotated, Any, cast

from fastapi import Depends, FastAPI
from fastapi.requests import HTTPConnection
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
from ._globals import (
    RestlyContext,
    _fr_globals,
    _get_restly_context,
    _restly_context_ctx,
)

#: Request-scope key carrying the nearest configured app's RestlyContext.
_SCOPE_KEY = "fastapi_restly.context"


class _AppContextScopeMiddleware:
    """Stamp the owning app's context into every request scope.

    Starlette re-stamps ``scope["app"]`` with the innermost app on the way into
    a mounted sub-application, so ``app.state`` alone cannot answer "which
    configured app does this request belong to". The stamp can: every
    configured app writes it on the way in, innermost last, so a request is
    served by the nearest enclosing configured app and an unconfigured mounted
    sub-app inherits its parent's configuration.
    """

    def __init__(self, app: Any, *, context: RestlyContext) -> None:
        self.app = app
        self._context = context

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            scope[_SCOPE_KEY] = self._context
        await self.app(scope, receive, send)


def _setup_async_database_connection(
    async_database_url: str | None = None,
    *,
    async_engine: AsyncEngine | None = None,
    async_make_session: async_sessionmaker[Any] | None = None,
) -> async_sessionmaker[Any]:
    if not async_make_session:
        if not async_engine:
            async_engine = create_async_engine(
                async_database_url  # type: ignore[arg-type]
            )
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
            engine = create_engine(database_url)  # type: ignore[arg-type]
        make_session = sessionmaker(bind=engine, expire_on_commit=False)

    _fr_globals.database_url = database_url
    _fr_globals.make_session = make_session
    return make_session


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
) -> None:
    """Configure FastAPI-Restly. Call once at startup.

    Pass async parameters (``async_database_url``, ``async_engine``, or
    ``async_make_session``) to enable async support, sync parameters
    (``database_url``, ``engine``, or ``make_session``) for sync support,
    or both if your application uses both.

    A test suite that calls :func:`fastapi_restly.testing.configure_tests`
    freezes these database sources for the rest of that pytest process. Select
    the test settings and configure the application before enabling managed
    testing; changing database sources afterwards would make schema setup,
    cleanup, and requests disagree and therefore raises.

    Use ``session_generator`` / ``sync_session_generator`` (or ``engine`` /
    ``make_session``) to construct sessions your way -- a custom engine,
    isolation level, ``search_path``, logging, an existing ``sessionmaker``. A
    custom generator's job is to **construct, yield, and clean up** (close /
    roll back on the way out); it must **not** commit. Customizing how a session
    is built never takes the commit away from Restly.

    Restly owns the commit. Every write -- the CRUD handlers (``handle_create``
    / ``handle_update`` / ``handle_delete``) and ``write_action`` -- runs
    ``before_commit`` -> commit -> ``after_commit`` around your domain logic;
    the commit is the framework's single responsibility. A custom (non-CRUD)
    write route either brackets its mutation with ``write_action(...)``
    (recommended) or commits the session itself with ``await
    self.session.commit()``.

    By default Restly warns (:class:`RestlyUncommittedChangesWarning`) when a
    request finishes with uncommitted changes still in the session -- the tell
    of a custom write route that forgot to commit. This applies to every session
    source, built-in or custom. A route that intentionally leaves a flush
    uncommitted (a validate-then-rollback dry run) should suppress the warning
    for just that request with ``session.info["_fr_suppress_uncommitted"] =
    True``. ``warn_on_uncommitted=False`` turns the check off for the
    configuration being written -- process-wide, or just for ``app`` when one
    is passed; that is rarely the right response to the warning -- prefer
    fixing the missing commit or the per-route suppression.

    Pass ``warn_on_misuse=True`` to enable opt-in registration-time misuse
    warnings (:class:`RestlyMisuseWarning`): when a view class is registered
    via ``include_view``, the framework flags route-shell overrides, direct
    ``session.commit()`` calls in view methods, CRUD route sets hand-rolled
    on a bare ``View``, and scalar foreign-key columns typed as an
    ``IDRef`` / ``IDSchema`` reference instead of ``fr.MustExist``. Off by
    default; intended for development, templates, and CI. Enable it before
    registering views.

    Pass your :class:`FastAPI` ``app`` to install fastapi-restly's default
    exception handlers (currently: a translator that turns SQLAlchemy
    :class:`~sqlalchemy.exc.IntegrityError` into HTTP 409 Conflict). Set
    ``install_default_exception_handlers=False`` to opt out. If you do not
    pass ``app`` here, the handlers are registered the first time a view is
    mounted via :func:`fastapi_restly.include_view` instead.

    Passing ``app`` together with any other setting also **scopes that
    setting to the app**: the app keeps its own copy of exactly what each
    ``configure(app, ...)`` call set, its requests are served from that copy,
    and a later ``configure(...)`` without ``app`` does not re-point it. Two
    apps in one process can therefore hold different databases. An app that
    carries its own session sources never borrows a source kind it was not
    configured with; a request needing the missing kind raises this error
    instead of reading another app's database. Requests to a mounted
    sub-application resolve the nearest enclosing configured app, so mount
    targets need no configuration of their own unless they should differ.
    App-scoped configuration is carried by a middleware, so ``configure(app,
    ...)`` must run before the app handles its first request. Configuration
    without ``app`` remains process-wide and keeps serving everything else:
    apps never passed to ``configure()``, scripts, and the ``fr.db`` helpers
    (which also accept ``app=`` where the distinction matters).
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
            "fr.testing.configure_tests() recorded it. Configure the application "
            "for its test database before calling configure_tests(), and do not "
            "reconfigure its database later during collection or application "
            "lifespan startup."
        )

    if not any(
        (
            configures_database,
            warn_on_misuse is not None,
            warn_on_uncommitted is not None,
            app is not None and install_default_exception_handlers,
        )
    ):
        raise TypeError("fr.configure() requires at least one setup argument.")

    # Warn flags passed together with ``app`` are app-scoped (merged into the
    # app context below); without ``app`` they set the process-wide flags.
    if app is None:
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
    if app is not None and (
        configures_database
        or warn_on_misuse is not None
        or warn_on_uncommitted is not None
    ):
        _configure_app_context(
            app,
            configured_async=async_database_url is not None
            or async_engine is not None
            or async_make_session is not None,
            configured_sync=database_url is not None
            or engine is not None
            or make_session is not None,
            session_generator=session_generator,
            sync_session_generator=sync_session_generator,
            warn_on_misuse=warn_on_misuse,
            warn_on_uncommitted=warn_on_uncommitted,
        )
    if app is not None and install_default_exception_handlers:
        register_default_exception_handlers(app)


def _configure_app_context(
    app: FastAPI,
    *,
    configured_async: bool,
    configured_sync: bool,
    session_generator: Callable[[], AsyncIterator[SA_AsyncSession]] | None,
    sync_session_generator: Callable[[], Iterator[SA_Session]] | None,
    warn_on_misuse: bool | None,
    warn_on_uncommitted: bool | None,
) -> None:
    """Merge exactly what this configure() call set onto the app's own context.

    Only this call's arguments travel; sources the process context happens to
    hold for other apps are never inherited. The warn flags are seeded from
    the current context when the app context is first created, and follow
    explicit per-app settings from then on. The current context was just
    written by configure(), so reading the freshly configured fields back from
    it reuses the exact factory objects; nothing is rebuilt.
    """
    current = _get_restly_context()
    app_context = getattr(app.state, "_fr_context", None)
    if app_context is None:
        if app.middleware_stack is not None:
            raise RestlyConfigurationError(
                "fr.configure(app, ...) must run before the application "
                "handles its first request: app-scoped configuration is "
                "carried by a middleware, and the app has already built its "
                "middleware stack."
            )
        app_context = RestlyContext()
        app_context.warn_on_misuse = current.warn_on_misuse
        app_context.warn_on_uncommitted = current.warn_on_uncommitted
        app.add_middleware(_AppContextScopeMiddleware, context=app_context)
        app.state._fr_context = app_context
    if configured_async:
        app_context.async_database_url = current.async_database_url
        app_context.async_make_session = current.async_make_session
    if configured_sync:
        app_context.database_url = current.database_url
        app_context.make_session = current.make_session
    if session_generator is not None:
        app_context.session_generator = session_generator
    if sync_session_generator is not None:
        app_context.sync_session_generator = sync_session_generator
    if warn_on_misuse is not None:
        app_context.warn_on_misuse = warn_on_misuse
    if warn_on_uncommitted is not None:
        app_context.warn_on_uncommitted = warn_on_uncommitted


def _resolve_contexts(
    conn: HTTPConnection | None, *, sync: bool
) -> tuple[RestlyContext, RestlyContext]:
    """The ``(sources, flags)`` contexts a request should use.

    A test override on the current context always wins; the fixtures install
    it there. So does an explicitly entered ``RestlyContext``: it is a
    deliberately more specific scope (framework tests, the pooled-async test
    wrapper) and stays authoritative. Otherwise a request that reached a
    configured app resolves that app's context: its warn flags always, and its
    session sources whenever it carries any. Serving a session kind the app
    was never configured for raises instead of borrowing whatever the process
    default currently holds, which in a multi-app process is another app's
    database. Everything else resolves the current context, as before
    app-scoping existed.
    """
    current = _get_restly_context()
    if sync:
        if current.test_make_session is not None:
            return current, current
    elif current.test_async_make_session is not None:
        return current, current
    if conn is None or _restly_context_ctx.get() is not None:
        return current, current
    app_context = conn.scope.get(_SCOPE_KEY)
    if app_context is None:
        return current, current
    has_sync_source = (
        app_context.sync_session_generator is not None
        or app_context.make_session is not None
    )
    has_async_source = (
        app_context.session_generator is not None
        or app_context.async_make_session is not None
    )
    if not has_sync_source and not has_async_source:
        # A flags-only app context (e.g. configure(app, warn_on_misuse=True))
        # scopes the flags but leaves the sources to the process default.
        return current, app_context
    if sync and not has_sync_source:
        raise RestlyConfigurationError(
            "This app was configured through fr.configure(app, ...) without a "
            "synchronous session source, so SessionDep does not borrow one "
            "from the process-wide configuration. Pass database_url, engine, "
            "make_session, or sync_session_generator to fr.configure(app, ...)."
        )
    if not sync and not has_async_source:
        raise RestlyConfigurationError(
            "This app was configured through fr.configure(app, ...) without an "
            "async session source, so AsyncSessionDep does not borrow one "
            "from the process-wide configuration. Pass async_database_url, "
            "async_engine, async_make_session, or session_generator to "
            "fr.configure(app, ...)."
        )
    return app_context, app_context


def _app_context_of(app: FastAPI | None) -> RestlyContext | None:
    """The per-app context configure(app, ...) stored, if the app has one."""
    if app is None:
        return None
    return getattr(app.state, "_fr_context", None)


def get_async_engine(app: FastAPI | None = None) -> AsyncEngine:
    """Return the async engine registered via configure().

    In a process serving several configured apps, pass the ``app`` whose
    engine you mean; without it the process-wide configuration answers.
    """
    context = _app_context_of(app) or _get_restly_context()
    if context.async_make_session is None:
        raise RestlyConfigurationError(
            "Call fr.configure() before using get_async_engine()."
        )
    # This is a read-only lookup of application configuration. Test overrides may
    # be bound to a pinned connection or deliberately refuse session creation;
    # neither changes the engine that configure() registered.
    bind = context.async_make_session.kw["bind"]
    if isinstance(bind, AsyncConnection):
        return bind.engine
    return bind


def get_engine(app: FastAPI | None = None) -> Engine:
    """Return the sync engine registered via configure().

    In a process serving several configured apps, pass the ``app`` whose
    engine you mean; without it the process-wide configuration answers.
    """
    context = _app_context_of(app) or _get_restly_context()
    if context.make_session is None:
        raise RestlyConfigurationError("Call fr.configure() before using get_engine().")
    bind = (_fr_globals.test_make_session or context.make_session).kw["bind"]
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


def create_all(
    base_or_metadata: type[DeclarativeBase] | MetaData, app: FastAPI | None = None
) -> None:
    """Create all tables for ``base_or_metadata`` on the configured sync engine.

    A dev/demo convenience over ``metadata.create_all(engine)`` so a quickstart
    can create its schema without reaching for the raw engine::

        fr.db.create_all(Base)  # or fr.db.create_all(Base.metadata)

    Accepts a ``DeclarativeBase`` subclass (its ``.metadata`` is used) or a
    ``MetaData``. Requires :func:`configure` first. In a process serving
    several configured apps, pass the ``app`` whose database the schema
    belongs on. Use Alembic migrations in production.
    """
    metadata = _resolve_metadata(base_or_metadata)
    context = _app_context_of(app) or _get_restly_context()
    if context.make_session is None:
        raise RestlyConfigurationError("Call fr.configure() before using create_all().")
    # Create against the configured bind: the engine in production, or the pinned
    # Connection under restly_session so the schema is visible to the test's
    # isolated sessions instead of silently landing on a throwaway connection.
    metadata.create_all((_fr_globals.test_make_session or context.make_session).kw["bind"])


async def async_create_all(
    base_or_metadata: type[DeclarativeBase] | MetaData, app: FastAPI | None = None
) -> None:
    """Async equivalent of :func:`create_all`, on the configured async engine.

    Usage::

        await fr.db.async_create_all(Base)
    """
    metadata = _resolve_metadata(base_or_metadata)
    context = _app_context_of(app) or _get_restly_context()
    if context.async_make_session is None:
        raise RestlyConfigurationError(
            "Call fr.configure() before using async_create_all()."
        )
    bind = (_fr_globals.test_async_make_session or context.async_make_session).kw["bind"]
    if isinstance(bind, AsyncConnection):
        # Under restly_async_session: create on the pinned connection, inside the
        # outer transaction, so the tables are visible to the test's sessions.
        await bind.run_sync(metadata.create_all)
    else:
        async with bind.begin() as conn:
            await conn.run_sync(metadata.create_all)


def _should_warn_uncommitted(context: RestlyContext | None = None) -> bool:
    """The uncommitted-changes check applies whenever ``warn_on_uncommitted`` is
    on. Restly owns the commit, so changes still pending when a request ends are
    the tell of a custom write route that never committed.
    """
    return (context or _get_restly_context()).warn_on_uncommitted


def _mark_uncommitted(session: SA_Session, flush_context: Any = None) -> None:
    session.info["_fr_uncommitted"] = True


def _clear_uncommitted(session: SA_Session, *args: Any) -> None:
    session.info.pop("_fr_uncommitted", None)


def _arm_uncommitted_warning(
    session: SA_AsyncSession | SA_Session, context: RestlyContext | None = None
) -> None:
    """Register flush/commit/rollback listeners so an uncommitted flush at the
    end of a request can be detected. Async sessions delegate to a sync
    ``Session``; that is where ORM events fire (and whose ``info`` is shared).
    """
    if not _should_warn_uncommitted(context):
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


def _warn_if_uncommitted(
    session: SA_AsyncSession | SA_Session, context: RestlyContext | None = None
) -> None:
    """Warn if the request is ending with changes that were flushed but never
    committed (the ``_fr_uncommitted`` flag), or added but never flushed
    (``new``/``dirty``/``deleted``) -- all about to be rolled back. Called only
    on the success path; an endpoint that raised never reaches this point.
    """
    if not _should_warn_uncommitted(context):
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


async def _async_generate_session(
    conn: HTTPConnection = None,  # type: ignore[assignment]
) -> AsyncIterator[SA_AsyncSession]:
    """FastAPI dependency for async database session."""
    ctx, flag_ctx = _resolve_contexts(conn, sync=False)
    if ctx.test_async_make_session is not None:
        async with ctx.test_async_make_session() as session:
            _arm_uncommitted_warning(session, flag_ctx)
            yield session
            _warn_if_uncommitted(session, flag_ctx)
        return
    if ctx.session_generator is not None:
        async for session in ctx.session_generator():
            _arm_uncommitted_warning(session, flag_ctx)
            yield session
            _warn_if_uncommitted(session, flag_ctx)
        return
    if ctx.async_make_session is None:
        raise RestlyConfigurationError(
            "Call fr.configure() before using AsyncSessionDep."
        )

    # FastAPI does not support contextmanagers as dependency directly,
    # but it does support generators. Restly owns the commit (the handle
    # design runs it inside ``handle_<verb>`` / ``write_action``), so this
    # dependency only manages the session lifecycle: the context manager rolls
    # back and closes on the way out, and any change a custom route flushed but
    # never committed is discarded (and warned about).
    async with ctx.async_make_session() as session:
        _arm_uncommitted_warning(session, flag_ctx)
        yield session
        _warn_if_uncommitted(session, flag_ctx)


def _session_dependency(dependency: Callable[..., Any]) -> Any:
    depends = cast(Callable[..., Any], Depends)
    if "scope" in signature(Depends).parameters:
        return depends(dependency, scope="function")
    return depends(dependency)


AsyncSessionDep = Annotated[
    SA_AsyncSession, _session_dependency(_async_generate_session)
]


def _generate_session(
    conn: HTTPConnection = None,  # type: ignore[assignment]
) -> Iterator[SA_Session]:
    """FastAPI dependency for sync database session."""
    # A test's session source wins over everything, including a generator and
    # anything reconfigured after the test started.
    ctx, flag_ctx = _resolve_contexts(conn, sync=True)
    if ctx.test_make_session is not None:
        with ctx.test_make_session() as session:
            _arm_uncommitted_warning(session, flag_ctx)
            yield session
            _warn_if_uncommitted(session, flag_ctx)
        return
    if ctx.sync_session_generator is not None:
        for session in ctx.sync_session_generator():
            _arm_uncommitted_warning(session, flag_ctx)
            yield session
            _warn_if_uncommitted(session, flag_ctx)
        return
    if ctx.make_session is None:
        raise RestlyConfigurationError("Call fr.configure() before using SessionDep.")

    with ctx.make_session() as session:
        _arm_uncommitted_warning(session, flag_ctx)
        yield session
        _warn_if_uncommitted(session, flag_ctx)


SessionDep = Annotated[SA_Session, _session_dependency(_generate_session)]
