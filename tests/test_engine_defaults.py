"""Tests for the engine defaults Restly applies to engines it builds itself.

``configure()`` builds an engine only from a URL. These defaults ride on that
one rung: an engine, sessionmaker or generator handed in by the caller is left
exactly as it arrived, which is what makes dropping down the ladder the way to
decline them.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool, StaticPool

import fastapi_restly as fr
from fastapi_restly.db._engine_defaults import engine_options, is_memory_sqlite
from fastapi_restly.db._globals import RestlyContext
from fastapi_restly.db._session import get_async_engine, get_engine


@contextmanager
def configured(url):
    """Configure a throwaway sync engine, then dispose it.

    Disposal is not incidental tidiness: a leaked SQLite connection surfaces as
    a ResourceWarning inside whichever later test happens to trigger the GC.
    """
    with RestlyContext():
        fr.configure(database_url=url)
        engine = get_engine()
        try:
            yield engine
        finally:
            engine.dispose()


@asynccontextmanager
async def configured_async(url):
    with RestlyContext():
        fr.configure(async_database_url=url)
        engine = get_async_engine()
        try:
            yield engine
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# Backend option selection (pure, no engine or server needed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@h/db",
        "postgresql+asyncpg://u:p@h/db",
        "postgresql+psycopg://u:p@h/db",
        "postgresql+psycopg2://u:p@h/db",
    ],
)
def test_postgres_gets_pre_ping_and_recycle_on_every_driver(url):
    """The options follow the backend, not the driver spelling."""
    assert engine_options(url) == {"pool_pre_ping": True, "pool_recycle": 1800}


@pytest.mark.parametrize(
    "url",
    [
        "sqlite://",
        "sqlite:///:memory:",
        "sqlite:///file::memory:",
        "sqlite:///foo.db?mode=memory",
        "sqlite+aiosqlite://",
    ],
)
def test_memory_sqlite_spellings_all_get_static_pool(url):
    assert engine_options(url) == {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }


@pytest.mark.parametrize(
    "url", ["sqlite:///app.db", "sqlite+aiosqlite:///app.db", "sqlite:////tmp/app.db"]
)
def test_file_sqlite_keeps_sqlalchemy_pooling(url):
    """Only the in-memory case needs a pool override; SQLAlchemy already sets
    ``check_same_thread=False`` for file-backed SQLite."""
    assert engine_options(url) == {}


@pytest.mark.parametrize(
    "url", ["mysql+pymysql://u:p@h/db", "oracle://u:p@h/db", "mssql+pyodbc://u:p@h/db"]
)
def test_unhandled_backends_get_no_options(url):
    """Backends Restly has no opinion on are built exactly as SQLAlchemy would."""
    assert engine_options(url) == {}


def test_is_memory_sqlite_ignores_non_sqlite_backends():
    from sqlalchemy.engine import make_url

    assert not is_memory_sqlite(make_url("postgresql://u:p@h/memory"))


# ---------------------------------------------------------------------------
# SQLite, sync
# ---------------------------------------------------------------------------


def test_memory_sqlite_is_one_database_across_threads():
    """The bug this default exists for: SQLAlchemy's SingletonThreadPool opens
    one connection per thread, and an in-memory database lives inside its
    connection, so a worker thread used to get a second, empty database. FastAPI
    runs ``def`` endpoints on a thread pool, so this is the common path.
    """
    with configured("sqlite://") as engine:
        assert isinstance(engine.pool, StaticPool)

        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
            conn.execute(text("INSERT INTO t VALUES (1)"))

        def read_from_worker():
            with engine.connect() as conn:
                return conn.execute(text("SELECT count(*) FROM t")).scalar()

        with ThreadPoolExecutor(1) as pool:
            assert pool.submit(read_from_worker).result() == 1


def test_memory_sqlite_enables_foreign_keys():
    with configured("sqlite://") as engine, engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_file_sqlite_enables_foreign_keys(tmp_path):
    with configured(f"sqlite:///{tmp_path / 'app.db'}") as engine:
        assert isinstance(engine.pool, QueuePool)
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_restly_leaves_no_permanent_mark_on_the_database_file(tmp_path):
    """The line the defaults stop at: Restly configures the connections it
    opens, and does not change the database they reach.

    journal_mode is the case that forced the rule. WAL would help a file-backed
    SQLite database serve concurrent requests, but it is written into the file
    and outlives the process, so every later reader gets it too. Restly declines
    to make that choice on the caller's data; ``foreign_keys`` is safe to set
    precisely because it evaporates with the connection.
    """
    db = tmp_path / "app.db"
    with configured(f"sqlite:///{db}") as engine:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))

    # Reopen with a plain engine Restly never saw: the file must look untouched.
    plain = create_engine(f"sqlite:///{db}")
    try:
        with plain.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "delete"
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 0
    finally:
        plain.dispose()


def test_foreign_keys_is_set_on_every_pooled_connection(tmp_path):
    """``foreign_keys`` is connection state, not database state: each new pooled
    connection starts with it off again. This is why the hook runs per connect
    rather than once per engine.
    """
    with configured(f"sqlite:///{tmp_path / 'app.db'}") as engine:
        # Hold the first connection open so the second is genuinely a new one.
        with engine.connect() as first, engine.connect() as second:
            assert first.execute(text("PRAGMA foreign_keys")).scalar() == 1
            assert second.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_foreign_keys_are_actually_enforced(tmp_path):
    """The pragma is only worth setting if the constraint bites."""
    from sqlalchemy.exc import IntegrityError

    with configured(f"sqlite:///{tmp_path / 'app.db'}") as engine:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
            conn.execute(
                text(
                    "CREATE TABLE child (id INTEGER PRIMARY KEY, "
                    "parent_id INTEGER REFERENCES parent(id))"
                )
            )
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text("INSERT INTO child VALUES (1, 999)"))


# ---------------------------------------------------------------------------
# SQLite, async
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_memory_sqlite_enables_foreign_keys():
    """aiosqlite drives the DBAPI on its own thread, so the pragma hook has to
    survive the greenlet hop that SQLAlchemy's connect event runs under."""
    async with configured_async("sqlite+aiosqlite://") as engine:
        async with engine.connect() as conn:
            assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar() == 1


@pytest.mark.asyncio
async def test_async_file_sqlite_enables_foreign_keys(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    async with configured_async(url) as engine:
        async with engine.connect() as conn:
            assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar() == 1
            assert (
                await conn.execute(text("PRAGMA journal_mode"))
            ).scalar() == "delete"


# ---------------------------------------------------------------------------
# The invariant: only engines Restly builds are touched
# ---------------------------------------------------------------------------


def test_caller_supplied_engine_is_left_alone():
    """Passing ``engine=`` is how you decline every default here, which is why
    no opt-out flag exists."""
    own = create_engine("sqlite://")
    try:
        with RestlyContext():
            fr.configure(engine=own)
            assert get_engine() is own
            with own.connect() as conn:
                assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 0
            assert not isinstance(own.pool, StaticPool)
    finally:
        own.dispose()


@pytest.mark.asyncio
async def test_caller_supplied_async_engine_is_left_alone(tmp_path):
    own = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'own.db'}")
    with RestlyContext():
        fr.configure(async_engine=own)
        assert get_async_engine() is own
        async with own.connect() as conn:
            assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar() == 0
        await own.dispose()


def test_caller_supplied_sessionmaker_is_left_alone():
    own = create_engine("sqlite://")
    try:
        with RestlyContext():
            fr.configure(make_session=sessionmaker(bind=own, expire_on_commit=False))
            with own.connect() as conn:
                assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 0
    finally:
        own.dispose()
