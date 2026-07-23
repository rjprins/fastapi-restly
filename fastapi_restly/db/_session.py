import warnings
from collections.abc import AsyncIterator, Callable, Iterator
from inspect import signature
from typing import Annotated, Any, cast

from fastapi import Depends, FastAPI
from sqlalchemy import Engine, MetaData, create_engine, event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.orm import Session as SA_Session

from .._exception_handlers import register_default_exception_handlers
from ..exc import RestlyConfigurationError, RestlyUncommittedChangesWarning
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
    True``. ``warn_on_uncommitted=False`` turns the check off globally; that is
    rarely the right response to the warning -- prefer fixing the missing
    commit or the per-route suppression.

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
    """
    if not any(
        (
            async_database_url is not None,
            async_engine is not None,
            async_make_session is not None,
            database_url is not None,
            engine is not None,
            make_session is not None,
            session_generator is not None,
            sync_session_generator is not None,
            warn_on_misuse is not None,
            warn_on_uncommitted is not None,
            app is not None and install_default_exception_handlers,
        )
    ):
        raise TypeError("fr.configure() requires at least one setup argument.")

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
    if app is not None and install_default_exception_handlers:
        register_default_exception_handlers(app)


def activate_savepoint_only_mode(
    make_session: async_sessionmaker[Any] | sessionmaker[Any],
) -> None:
    """
    Intended for use in tests. Puts the session factory into savepoint-only mode so
    that no test data is ever committed to the database. Each test can roll back
    instantly by closing the session, leaving the database clean for the next test.

    This is done with "create_savepoint" mode and a wrapper on engine.connect() that
    begins the outer transaction before the Session can use it.
    https://docs.sqlalchemy.org/en/20/orm/session_transaction.html#session-external-transaction
    """
    engine = _get_sync_engine(make_session)

    # Check if already activated (look for the marker attribute we set)
    if hasattr(engine.connect, "_original_connect"):
        return  # Already activated, skip

    original_connect = engine.connect

    def _begin_on_connect():
        connection = original_connect()
        connection.begin()
        return connection

    # Using setattr to silence pyright
    setattr(_begin_on_connect, "_original_connect", original_connect)

    engine.connect = _begin_on_connect
    make_session.configure(join_transaction_mode="create_savepoint")


def deactivate_savepoint_only_mode(
    make_session: async_sessionmaker[Any] | sessionmaker[Any],
) -> None:
    """
    Reverts the effect of `activate_savepoint_only_mode`.
    Restores the original engine.connect and disables savepoint-only mode.
    """
    engine = _get_sync_engine(make_session)
    _begin_on_connect = cast(Any, engine.connect)
    if hasattr(_begin_on_connect, "_original_connect"):
        # Restore the original connect that was saved by activate_savepoint_only_mode
        engine.connect = _begin_on_connect._original_connect
    # If engine was never activated, there is nothing to restore; this is safe to call

    make_session.configure(join_transaction_mode=None)


def get_async_engine() -> AsyncEngine:
    """Return the async engine registered via configure()."""
    if _fr_globals.async_make_session is None:
        raise RestlyConfigurationError(
            "Call fr.configure() before using get_async_engine()."
        )
    return _fr_globals.async_make_session.kw["bind"]


def get_engine() -> Engine:
    """Return the sync engine registered via configure()."""
    if _fr_globals.make_session is None:
        raise RestlyConfigurationError("Call fr.configure() before using get_engine().")
    return _fr_globals.make_session.kw["bind"]


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
    _resolve_metadata(base_or_metadata).create_all(get_engine())


async def async_create_all(base_or_metadata: type[DeclarativeBase] | MetaData) -> None:
    """Async equivalent of :func:`create_all`, on the configured async engine.

    Usage::

        await fr.db.async_create_all(Base)
    """
    metadata = _resolve_metadata(base_or_metadata)
    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)


def _get_sync_engine(
    make_session: async_sessionmaker[Any] | sessionmaker[Any],
) -> Engine:
    engine = make_session.kw["bind"]
    if isinstance(engine, AsyncEngine):
        return engine.sync_engine
    return engine


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
