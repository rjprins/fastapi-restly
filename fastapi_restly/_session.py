from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator, Iterator, cast

from fastapi import Depends
from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio import AsyncSession as SA_AsyncSession
from sqlalchemy.orm import Session as SA_Session
from sqlalchemy.orm import sessionmaker

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


def setup_async_database_connection(
    async_database_url: str | None = None,
    *,
    async_engine: AsyncEngine | None = None,
    async_make_session: async_sessionmaker | None = None,
) -> async_sessionmaker:
    """Create and set an async session maker. Returns the session maker."""
    if not async_make_session:
        if not async_engine:
            if not async_database_url:
                raise Exception(
                    "set_sessionmaker() requires either `async_database_url`, `async_engine`, or `async_make_session` as argument"
                )
            async_engine = create_async_engine(
                async_database_url,
                json_serializer=json_serializer,
                json_deserializer=json_deserializer,
            )
        async_make_session = async_sessionmaker(
            bind=async_engine, autoflush=False, expire_on_commit=False
        )

    fr_globals.async_database_url = async_database_url
    fr_globals.async_make_session = async_make_session
    return async_make_session


def setup_database_connection(
    database_url: str | None = None,
    *,
    engine: Engine | None = None,
    make_session: sessionmaker | None = None,
) -> sessionmaker:
    """Create and set a sync session maker. Returns the session maker."""
    if make_session is None:
        if engine is None:
            if not database_url:
                raise Exception(
                    "setup_database_connection() requires either `database_url`, `engine`, or `make_session` as argument"
                )
            engine = create_engine(
                database_url,
                json_serializer=json_serializer,
                json_deserializer=json_deserializer,
            )
        make_session = sessionmaker(bind=engine, expire_on_commit=False)

    fr_globals.database_url = database_url
    fr_globals.make_session = make_session
    return make_session


def db_lifespan(*, create_tables: bool = False):
    @asynccontextmanager
    async def _db_lifespan(app):
        if create_tables:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        try:
            yield
        finally:
            await engine.dispose()

    return _db_lifespan


def activate_savepoint_only_mode(
    make_session: async_sessionmaker | sessionmaker,
) -> None:
    """
    When `savepoint_only_mode` is enabled, any changes to the database will not be
    committed. This is done with "create_savepoint" mode and a wrapper on
    engine.connect() that begins the transaction before the Session can use it.
    https://docs.sqlalchemy.org/en/20/orm/session_transaction.html#session-external-transaction
    """
    engine = _get_sync_engine(make_session)
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
        engine.connect = _begin_on_connect._original_connect

    make_session.configure(join_transaction_mode=None)


def _get_sync_engine(make_session: async_sessionmaker | sessionmaker) -> Engine:
    engine = make_session.kw["bind"]
    if isinstance(engine, AsyncEngine):
        return engine.sync_engine
    return engine


async def async_generate_session() -> AsyncIterator[SA_AsyncSession]:
    """FastAPI dependency for async database session."""
    # FastAPI does not support contextmanagers as dependency directly,
    # but it does support generators.
    async with fr_globals.async_make_session() as session:
        yield session
        if session.is_active:
            await session.commit()


AsyncSessionDep = Annotated[SA_AsyncSession, Depends(async_generate_session)]


def generate_session() -> Iterator[SA_Session]:
    """FastAPI dependency for sync database session."""
    with fr_globals.make_session() as session:
        yield session
        if session.is_active:
            session.commit()


SessionDep = Annotated[SA_Session, Depends(generate_session)]
