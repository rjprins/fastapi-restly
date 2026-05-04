from collections.abc import AsyncIterator, Callable, Iterator
from typing import Annotated, Any, cast

from fastapi import Depends, FastAPI
from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.orm import Session as SA_Session
from sqlalchemy.orm import sessionmaker

from .._exceptions import register_default_exception_handlers
from ._globals import fr_globals

try:
    import orjson
except ImportError:
    json_deserializer = None
    json_serializer = None
else:

    def orjson_serializer(obj):
        return orjson.dumps(
            obj, option=orjson.OPT_NAIVE_UTC | orjson.OPT_NON_STR_KEYS
        ).decode()

    json_deserializer = orjson.loads
    json_serializer = orjson_serializer


def _setup_async_database_connection(
    async_database_url: str | None = None,
    *,
    async_engine: AsyncEngine | None = None,
    async_make_session: async_sessionmaker | None = None,
) -> async_sessionmaker:
    if not async_make_session:
        if not async_engine:
            async_engine = create_async_engine(
                async_database_url,  # type: ignore[arg-type]
                json_serializer=json_serializer,
                json_deserializer=json_deserializer,
            )
        async_make_session = async_sessionmaker(
            bind=async_engine, autoflush=False, expire_on_commit=False
        )

    fr_globals.async_database_url = async_database_url
    fr_globals.async_make_session = async_make_session
    return async_make_session


def _setup_database_connection(
    database_url: str | None = None,
    *,
    engine: Engine | None = None,
    make_session: sessionmaker | None = None,
) -> sessionmaker:
    if make_session is None:
        if engine is None:
            engine = create_engine(
                database_url,  # type: ignore[arg-type]
                json_serializer=json_serializer,
                json_deserializer=json_deserializer,
            )
        make_session = sessionmaker(bind=engine, expire_on_commit=False)

    fr_globals.database_url = database_url
    fr_globals.make_session = make_session
    return make_session


def configure(
    app: FastAPI | None = None,
    *,
    async_database_url: str | None = None,
    async_engine: AsyncEngine | None = None,
    async_make_session: async_sessionmaker | None = None,
    database_url: str | None = None,
    engine: Engine | None = None,
    make_session: sessionmaker | None = None,
    session_generator: Callable[[], AsyncIterator[SA_AsyncSession]] | None = None,
    sync_session_generator: Callable[[], Iterator[SA_Session]] | None = None,
    install_default_exception_handlers: bool = True,
) -> None:
    """Configure FastAPI-Restly. Call once at startup.

    Pass async parameters (``async_database_url``, ``async_engine``, or
    ``async_make_session``) to enable async support, sync parameters
    (``database_url``, ``engine``, or ``make_session``) for sync support,
    or both if your application uses both.

    Use ``session_generator`` / ``sync_session_generator`` to plug in a
    custom session factory instead of the built-in one.

    Pass your :class:`FastAPI` ``app`` to install fastapi-restly's default
    exception handlers (currently: a translator that turns SQLAlchemy
    :class:`~sqlalchemy.exc.IntegrityError` into HTTP 409 Conflict). Set
    ``install_default_exception_handlers=False`` to opt out. If you do not
    pass ``app`` here, the handlers are registered the first time a view is
    mounted via :func:`fastapi_restly.include_view` instead.
    """
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
        fr_globals.session_generator = session_generator
    if sync_session_generator is not None:
        fr_globals.sync_session_generator = sync_session_generator
    if app is not None and install_default_exception_handlers:
        register_default_exception_handlers(app)


def activate_savepoint_only_mode(
    make_session: async_sessionmaker | sessionmaker,
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
    make_session: async_sessionmaker | sessionmaker,
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
    if fr_globals.async_make_session is None:
        raise RuntimeError("Call fr.configure() before using get_async_engine().")
    return fr_globals.async_make_session.kw["bind"]


def get_engine() -> Engine:
    """Return the sync engine registered via configure()."""
    if fr_globals.make_session is None:
        raise RuntimeError("Call fr.configure() before using get_engine().")
    return fr_globals.make_session.kw["bind"]


def _get_sync_engine(make_session: async_sessionmaker | sessionmaker) -> Engine:
    engine = make_session.kw["bind"]
    if isinstance(engine, AsyncEngine):
        return engine.sync_engine
    return engine


async def async_generate_session() -> AsyncIterator[SA_AsyncSession]:
    """FastAPI dependency for async database session."""
    if fr_globals.session_generator is not None:
        async for session in fr_globals.session_generator():
            yield session
        return

    # FastAPI does not support contextmanagers as dependency directly,
    # but it does support generators.
    async with fr_globals.async_make_session() as session:
        yield session
        if session.is_active:
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise


AsyncSessionDep = Annotated[SA_AsyncSession, Depends(async_generate_session)]


def generate_session() -> Iterator[SA_Session]:
    """FastAPI dependency for sync database session."""
    if fr_globals.sync_session_generator is not None:
        yield from fr_globals.sync_session_generator()
        return

    with fr_globals.make_session() as session:
        yield session
        if session.is_active:
            try:
                session.commit()
            except Exception:
                session.rollback()
                raise


SessionDep = Annotated[SA_Session, Depends(generate_session)]
