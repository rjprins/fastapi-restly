"""A route that catches the error of a write action and goes on.

Each outermost write action, and each outermost
``shared_write_action_commit()`` block, runs in a savepoint. An error rolls
back to it: this undoes only the changes of the action or block, and the
session stays usable. The ``plain`` legs show this. The ``savepoint`` legs
put the route's own ``session.begin_nested()`` around each call, as the docs
of 0.12 showed, which still works.

The engines are plain, as in production: SQLite without the SAVEPOINT fix
that the test fixtures install. With the default settings of the driver, the
release of a savepoint that began the transaction commits. So on SQLite, the
``BEGIN`` that Restly sends before its savepoint is under test too.
tests/postgres/test_write_action_error.py runs the same tests on PostgreSQL.
"""

import warnings
from contextlib import asynccontextmanager, nullcontext, suppress
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

import fastapi_restly as fr
from fastapi_restly.db._session import _arm_uncommitted_warning, _warn_if_uncommitted
from fastapi_restly.exc import RestlyUncommittedChangesWarning

from ._models import SharedBase
from ._write_env import call, enter, registered

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@asynccontextmanager
async def orders_env(engine, base, *, asynchronous):
    """Declare the model and the view on ``base``, create the table on
    ``engine``, and yield what the tests use, with an open session."""

    class Order(fr.IDMixin, base):
        name: Mapped[str] = mapped_column(unique=True)

    class OrderSchema(fr.IDSchema):
        name: str

    def before(self, action, new, old=None):
        if new is not None and new.name == "rejected":
            raise HTTPException(400, "rejected")

    async def async_before(self, action, new, old=None):
        before(self, action, new, old)

    view_type = registered(
        type(
            "OrderView",
            (fr.AsyncRestView if asynchronous else fr.RestView,),
            {
                "model": Order,
                "schema": OrderSchema,
                "prefix": "/orders",
                "before_action_commit": async_before if asynchronous else before,
            },
        )
    )

    if asynchronous:
        make_session = lambda: AsyncSession(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(base.metadata.create_all)
    else:
        make_session = lambda: Session(engine, expire_on_commit=False)
        base.metadata.create_all(engine)

    async with enter(make_session()) as session:
        yield SimpleNamespace(
            view=view_type(request=None, session=session),
            session=session,
            make_session=make_session,
            model=Order,
            schema=view_type.schema_create,
            dialect=engine.dialect.name,
        )


@asynccontextmanager
async def orders_on_new_tables(url, *, asynchronous):
    """``orders_env`` on a new engine for ``url`` and a base of its own, for
    a database that outlives the test: the table is dropped at the end."""

    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    engine = create_async_engine(url) if asynchronous else create_engine(url)

    async def drop_all():
        if asynchronous:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.drop_all)
        else:
            Base.metadata.drop_all(engine)

    try:
        async with orders_env(engine, Base, asynchronous=asynchronous) as env:
            yield env
    finally:
        await drop_all()
        await call(engine.dispose())


@pytest.fixture(params=["sync", "async"])
async def orders(request, tmp_path):
    asynchronous = request.param == "async"
    database = tmp_path / "orders.db"
    if asynchronous:
        engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    else:
        engine = create_engine(f"sqlite:///{database}")
    try:
        async with orders_env(engine, SharedBase, asynchronous=asynchronous) as env:
            yield env
    finally:
        await call(engine.dispose())


async def _create(orders, name):
    return await call(orders.view.handle_create(orders.schema(name=name)))


async def _bulk_create(orders, names, *, savepoint):
    """The loop from the docs, with or without a savepoint around each call."""
    saved, errors = [], []
    for name in names:
        block = orders.session.begin_nested() if savepoint else nullcontext()
        try:
            async with enter(block):
                saved.append(await _create(orders, name))
        except HTTPException as exc:
            errors.append(type(exc))
    return saved, errors


async def _names(orders):
    async with enter(orders.make_session()) as session:
        stmt = select(orders.model.name).order_by(orders.model.id)
        return list(await call(session.scalars(stmt)))


@pytest.mark.parametrize("savepoint", [False, True], ids=["plain", "savepoint"])
async def test_a_refused_write_is_not_saved(orders, savepoint):
    saved, errors = await _bulk_create(
        orders, ["rejected", "ok", "rejected"], savepoint=savepoint
    )

    assert errors == [HTTPException, HTTPException]
    # Nothing expired the row saved before the error.
    assert [inspect(order).unloaded for order in saved] == [set()]
    assert [order.name for order in saved] == ["ok"]
    assert await _names(orders) == ["ok"]


@pytest.mark.parametrize("savepoint", [False, True], ids=["plain", "savepoint"])
async def test_the_session_stays_usable_after_a_caught_conflict(orders, savepoint):
    await _create(orders, "dup")

    saved, errors = await _bulk_create(
        orders, ["first", "dup", "next"], savepoint=savepoint
    )

    assert errors == [fr.exc.Conflict]
    # Nothing expired the rows saved before the error, so async can read them.
    assert [inspect(order).unloaded for order in saved] == [set(), set()]
    assert [order.name for order in saved] == ["first", "next"]
    assert await _names(orders) == ["dup", "first", "next"]


async def test_a_loop_that_ends_with_an_error_does_not_warn(orders):
    """The rollback of the savepoint leaves nothing uncommitted."""
    _arm_uncommitted_warning(orders.session)

    await _bulk_create(orders, ["ok", "rejected"], savepoint=False)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RestlyUncommittedChangesWarning)
        _warn_if_uncommitted(orders.session)


async def test_a_caught_error_keeps_the_warning_for_a_write_before_it(orders):
    """The route's own object is flushed when the savepoint begins, outside
    it. The rollback of the savepoint does not undo it, so it is still
    uncommitted, and the request warns."""
    _arm_uncommitted_warning(orders.session)
    orders.session.add(orders.model(name="added before"))

    with pytest.raises(HTTPException):
        await _create(orders, "rejected")

    with pytest.warns(RestlyUncommittedChangesWarning):
        _warn_if_uncommitted(orders.session)
    await call(orders.session.rollback())


async def test_an_object_added_before_the_action_is_written_at_its_start(orders):
    """The savepoint begins after the flush of what is pending, so it holds
    only the change of the action."""
    orders.session.add(orders.model(name="added before"))

    with pytest.raises(HTTPException):
        await _create(orders, "rejected")
    await _create(orders, "ok")

    assert await _names(orders) == ["added before", "ok"]


async def test_an_error_that_leaves_a_shared_block_undoes_the_block(orders):
    before = await _create(orders, "before")

    with pytest.raises(ValueError, match="stop"):
        async with enter(orders.view.shared_write_action_commit()):
            await _create(orders, "inside")
            raise ValueError("stop")

    assert inspect(before).unloaded == set()
    await _create(orders, "after")
    assert await _names(orders) == ["before", "after"]


async def test_a_savepoint_inside_a_shared_block_does_not_commit_early(orders):
    """The route's savepoint inside the block does not begin the
    transaction, so on SQLite its release commits nothing."""
    with pytest.raises(ValueError, match="stop"):
        async with enter(orders.view.shared_write_action_commit()):
            async with enter(orders.session.begin_nested()):
                await _create(orders, "inside")
            raise ValueError("stop")

    assert await _names(orders) == []


async def test_a_caught_conflict_inside_a_shared_block_stops_its_commit(orders):
    """Inside the block, an action has no savepoint of its own. A caught
    error of a failed flush aborts the block, and its rollback leaves the
    session usable."""
    await _create(orders, "dup")

    with pytest.raises(RuntimeError, match="The write was aborted"):
        async with enter(orders.view.shared_write_action_commit()):
            await _create(orders, "first")
            with suppress(IntegrityError):
                await _create(orders, "dup")
            await _create(orders, "next")

    await _create(orders, "after")
    assert await _names(orders) == ["dup", "after"]


async def test_a_direct_commit_inside_a_write_action_saves_nothing(orders):
    """The outermost block owns the commit, so a direct commit is refused
    before it flushes, and the block saves nothing."""
    view = orders.view
    order = orders.model(name="direct")

    with pytest.raises(RuntimeError, match="cannot be called inside"):
        async with enter(view.write_action(view.Action.CREATE, obj=order)):
            await call(orders.session.commit())

    await _create(orders, "after")
    assert await _names(orders) == ["after"]


async def test_a_release_that_fails_undoes_everything(orders):
    """A failed release leaves the savepoint unusable: SQLAlchemy does not
    roll back to it any more. So the whole transaction rolls back, and the
    next write does not commit the row of the failed one."""
    bind = orders.session.bind
    engine = getattr(bind, "sync_engine", bind)
    failures = ["the release failed"]

    def fail_once(connection, name, context):
        if failures:
            raise RuntimeError(failures.pop())

    event.listen(engine, "release_savepoint", fail_once)
    try:
        with pytest.raises(RuntimeError, match="the release failed"):
            await _create(orders, "lost")
        await _create(orders, "after")
    finally:
        event.remove(engine, "release_savepoint", fail_once)

    assert await _names(orders) == ["after"]


async def test_a_commit_that_fails_after_the_release_undoes_everything(orders):
    """The savepoint is gone, so the whole transaction rolls back. On SQLite,
    the ``BEGIN`` before the savepoint keeps its release from committing."""
    session = getattr(orders.session, "sync_session", orders.session)

    def fail_the_commit(session):
        if not session.in_nested_transaction():
            raise RuntimeError("the commit failed")

    event.listen(session, "before_commit", fail_the_commit)
    try:
        with pytest.raises(RuntimeError, match="the commit failed"):
            await _create(orders, "lost")
    finally:
        event.remove(session, "before_commit", fail_the_commit)

    await _create(orders, "after")
    assert await _names(orders) == ["after"]
