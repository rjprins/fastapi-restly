from typing import Annotated, AsyncIterator, Iterator

from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from ._globals import fa_globals

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


def async_setup_database_connection(
    async_database_url: str | None = None,
    async_make_session: async_sessionmaker | None = None,
) -> async_sessionmaker:
    """Create and set an async session maker. Returns the session maker."""
    if not async_make_session:
        if not async_database_url:
            raise Exception(
                "set_sessionmaker() requires either `async_make_session` or `async_database_url` as argument"
            )
        async_engine = create_async_engine(
            async_database_url,
            json_serializer=json_serializer,
            json_deserializer=json_deserializer,
        )
        async_make_session = async_sessionmaker(
            bind=async_engine, autoflush=False, expire_on_commit=False
        )

    fa_globals.async_database_url = async_database_url
    fa_globals.async_make_session = async_make_session
    return async_make_session


def setup_database_connection(
    database_url: str | None = None, make_session: sessionmaker | None = None
) -> sessionmaker:
    """Create and set a sync session maker. Returns the session maker."""
    if make_session is None:
        if not database_url:
            raise Exception(
                "setup_database_connection() requires either `make_session` or `database_url` as argument"
            )
        sync_engine = create_engine(
            database_url,
            json_serializer=json_serializer,
            json_deserializer=json_deserializer,
        )
        make_session = sessionmaker(bind=sync_engine, expire_on_commit=False)

    fa_globals.database_url = database_url
    fa_globals.make_session = make_session
    return make_session


def activate_savepoint_only_mode(
    make_session: async_sessionmaker | sessionmaker,
) -> None:
    """
    When `savepoint_only_mode` is enabled, any changes to the database will not be
    committed. This is done with "create_savepoint" mode and a wrapper on
    engine.connect() that begins the transaction before the Session can use it.
    https://docs.sqlalchemy.org/en/20/orm/session_transaction.html#session-external-transaction
    """
    engine = make_session.kw["bind"]
    original_connect = engine.connect
    engine._original_connect = original_connect

    def _begin_on_connect():
        connection = original_connect()
        connection.begin()
        return connection

    engine.connect = _begin_on_connect
    make_session.configure(join_transaction_mode="create_savepoint")


def deactivate_savepoint_only_mode(
    make_session: async_sessionmaker | sessionmaker,
) -> None:
    """
    Reverts the effect of `activate_savepoint_only_mode`.
    Restores the original engine.connect and disables savepoint-only mode.
    """
    engine = make_session.kw["bind"]
    if hasattr(engine, "_original_connect"):
        engine.connect = engine._original_connect
        del engine._original_connect

    make_session.configure(join_transaction_mode=None)


async def async_generate_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency for async database session."""
    # FastAPI does not support contextmanagers as dependency directly,
    # but it does support generators.
    async with fa_globals.async_make_session.begin() as session:
        yield session


AsyncDBDependency = Annotated[AsyncSession, Depends(async_generate_session)]


def generate_session() -> Iterator[Session]:
    """FastAPI dependency for sync database session."""
    with fa_globals.make_session.begin() as session:
        yield session


DBDependency = Annotated[Session, Depends(generate_session)]
