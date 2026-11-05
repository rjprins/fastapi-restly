"""A write action on plain engines, as in production: SQLite with the settings
of its driver, without the ``BEGIN`` recipe of tests/_write_env.py. Restly
leaves the transaction to the driver, as SQLAlchemy does."""

import sqlite3
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session

import fastapi_restly as fr

from ._write_env import call, enter, registered

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _drive(coroutine):
    """Run a step from a sync view. It does not suspend there, because all that
    it awaits is the plain value of a sync session."""
    try:
        coroutine.send(None)
    except StopIteration as stop:
        return stop.value
    raise RuntimeError("the step of a sync view suspended")


@asynccontextmanager
async def _tags(asynchronous, tmp_path, **connect_args):
    """A model and a view, on a new SQLite file. A test sets ``env.step``: the
    code of the action, an ``async def`` that gets the session."""

    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class Tag(fr.IDMixin, Base):
        name: Mapped[str]

    class TagSchema(fr.IDSchema):
        name: str

    env = SimpleNamespace(step=None, authorize=None, before_calls=[], model=Tag)

    if asynchronous:

        class TagView(fr.AsyncRestView):
            model = Tag
            schema = TagSchema
            prefix = "/tags"

            async def authorize(self, action, obj=None, data=None):
                if env.authorize:
                    await env.authorize(self.session)

            async def create(self, data):
                tag = await super().create(data)
                if env.step:
                    await env.step(self.session)
                return tag

            async def before_action_commit(self, action, new, old=None):
                env.before_calls.append(action)

    else:

        class TagView(fr.RestView):
            model = Tag
            schema = TagSchema
            prefix = "/tags"

            def authorize(self, action, obj=None, data=None):
                if env.authorize:
                    _drive(env.authorize(self.session))

            def create(self, data):
                tag = super().create(data)
                if env.step:
                    _drive(env.step(self.session))
                return tag

            def before_action_commit(self, action, new, old=None):
                env.before_calls.append(action)

    registered(TagView)
    env.schema = TagView.create_schema

    database = env.database = tmp_path / "tags.db"
    if asynchronous:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{database}", connect_args=connect_args
        )
        env.make_session = lambda: AsyncSession(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    else:
        engine = create_engine(f"sqlite:///{database}", connect_args=connect_args)
        env.make_session = lambda: Session(engine, expire_on_commit=False)
        Base.metadata.create_all(engine)

    env.statements = []

    @event.listens_for(getattr(engine, "sync_engine", engine), "before_cursor_execute")
    def record(conn, cursor, statement, params, context, executemany):
        env.statements.append(statement.split()[0])

    try:
        async with enter(env.make_session()) as session:
            env.session = session
            env.view = TagView(request=None, session=session)
            yield env
    finally:
        await call(engine.dispose())


@pytest.fixture(params=["sync", "async"])
def asynchronous(request):
    return request.param == "async"


def _names(env):
    """What another connection sees: only what is committed. A connection of
    its own, because the pool may give a session the connection of the view."""
    connection = sqlite3.connect(env.database)
    try:
        table = env.model.__tablename__
        rows = connection.execute(f"SELECT name FROM {table} ORDER BY id").fetchall()
        return [name for (name,) in rows]
    finally:
        connection.close()


async def test_a_read_before_the_change_stays_outside_the_transaction(
    asynchronous, tmp_path
):
    """The driver begins no transaction for a read, and the savepoint begins it
    where the change begins. So the read in ``authorize`` holds no lock."""

    async def read(session):
        await call(session.execute(text("SELECT 1")))

    async with _tags(asynchronous, tmp_path) as env:
        env.authorize = read
        env.statements.clear()
        await call(env.view.handle_create(env.schema(name="a")))
        assert "BEGIN" not in env.statements
        assert env.statements.index("SELECT") < env.statements.index("SAVEPOINT")
        assert _names(env) == ["a"]


async def test_a_rollback_in_the_action_does_not_run_the_before_hook(
    asynchronous, tmp_path
):
    """The action is over at the rollback. The hook may have an outside effect,
    so it must not run for a write that is not saved."""

    async def rollback(session):
        await call(session.rollback())

    async with _tags(asynchronous, tmp_path) as env:
        env.step = rollback
        with pytest.raises(RuntimeError, match="commits nothing"):
            await call(env.view.handle_create(env.schema(name="a")))
        assert env.before_calls == []
        assert _names(env) == []


async def test_a_close_in_the_action_does_not_run_the_before_hook(
    asynchronous, tmp_path
):
    async def close(session):
        await call(session.close())

    async with _tags(asynchronous, tmp_path) as env:
        env.step = close
        with pytest.raises(RuntimeError, match="commits nothing"):
            await call(env.view.handle_create(env.schema(name="a")))
        assert env.before_calls == []
        assert _names(env) == []


@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="sqlite3 has autocommit from Python 3.12"
)
async def test_a_write_is_saved_when_the_driver_is_in_autocommit_mode(
    asynchronous, tmp_path
):
    """In this mode, the ``commit()`` of the driver does nothing. A ``BEGIN``
    from Restly would never end, and the write would be lost without an error."""
    async with _tags(asynchronous, tmp_path, autocommit=True) as env:
        await call(env.view.handle_create(env.schema(name="a")))
        assert _names(env) == ["a"]


@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="sqlite3 has autocommit from Python 3.12"
)
async def test_a_write_is_saved_when_the_driver_has_autocommit_off(
    asynchronous, tmp_path
):
    async with _tags(asynchronous, tmp_path, autocommit=False) as env:
        await call(env.view.handle_create(env.schema(name="a")))
        assert _names(env) == ["a"]


async def test_a_second_write_on_the_same_connection_is_saved(asynchronous, tmp_path):
    async with _tags(asynchronous, tmp_path) as env:
        await call(env.view.handle_create(env.schema(name="a")))
        await call(env.view.handle_create(env.schema(name="b")))
        assert _names(env) == ["a", "b"]


async def test_a_write_after_a_failed_one_is_saved(asynchronous, tmp_path):
    """The rollback to the savepoint keeps the transaction of the driver open.
    The next write runs in it, and the commit of the driver ends it."""

    async def fail(session):
        await call(session.flush())
        raise HTTPException(409)

    async with _tags(asynchronous, tmp_path) as env:
        env.step = fail
        with pytest.raises(HTTPException):
            await call(env.view.handle_create(env.schema(name="a")))
        env.step = None
        await call(env.view.handle_create(env.schema(name="b")))
        assert _names(env) == ["b"]
