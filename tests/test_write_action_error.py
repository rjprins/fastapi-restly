"""A route that catches the error of a write action and goes on, outside a
shared commit.

With ``session.begin_nested()`` around each call, as docs/lifecycle.md shows,
the error undoes only the change of that call. Without it, the change stays
in the session: the known issue in the changelog. The ``plain`` legs expect
the fixed behaviour, so they fail strictly until the issue is fixed.

The engines are plain, as in production: SQLite without the SAVEPOINT fix
that the test fixtures install.
"""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, Session, mapped_column

import fastapi_restly as fr

from ._models import SharedBase
from ._write_env import call, enter, registered

pytestmark = pytest.mark.anyio

KNOWN_ISSUE = "Known issue: without a savepoint, the change stays in the session"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(params=["sync", "async"])
async def orders(request, tmp_path):
    class Order(fr.IDMixin, SharedBase):
        name: Mapped[str] = mapped_column(unique=True)

    class OrderSchema(fr.IDSchema):
        name: str

    def before(self, action, new, old=None):
        if new is not None and new.name == "rejected":
            raise HTTPException(400, "rejected")

    async def async_before(self, action, new, old=None):
        before(self, action, new, old)

    asynchronous = request.param == "async"
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

    database = tmp_path / "orders.db"
    if asynchronous:
        engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
        make_session = lambda: AsyncSession(engine, expire_on_commit=False)
    else:
        engine = create_engine(f"sqlite:///{database}")
        make_session = lambda: Session(engine, expire_on_commit=False)

    try:
        if asynchronous:
            async with engine.begin() as connection:
                await connection.run_sync(SharedBase.metadata.create_all)
        else:
            SharedBase.metadata.create_all(engine)

        async with enter(make_session()) as session:
            yield SimpleNamespace(
                view=view_type(request=None, session=session),
                session=session,
                make_session=make_session,
                model=Order,
                schema=view_type.schema_create,
            )
    finally:
        await call(engine.dispose())


async def _bulk_create(orders, names, *, savepoint):
    """The loop from the docs, with or without a savepoint around each call."""
    saved, errors = [], []
    for name in names:
        block = orders.session.begin_nested() if savepoint else nullcontext()
        try:
            async with enter(block):
                order = await call(orders.view.handle_create(orders.schema(name=name)))
                saved.append(order)
        except HTTPException as exc:
            errors.append(type(exc))
    return saved, errors


async def _names(orders):
    async with enter(orders.make_session()) as session:
        stmt = select(orders.model.name).order_by(orders.model.id)
        return list(await call(session.scalars(stmt)))


@pytest.mark.parametrize(
    "savepoint",
    [
        pytest.param(
            False,
            id="plain",
            marks=pytest.mark.xfail(reason=KNOWN_ISSUE, raises=AssertionError),
        ),
        pytest.param(True, id="savepoint"),
    ],
)
async def test_a_refused_write_is_not_saved(orders, savepoint):
    saved, errors = await _bulk_create(
        orders, ["rejected", "ok", "rejected"], savepoint=savepoint
    )

    assert errors == [HTTPException, HTTPException]
    # Nothing expired the row saved before the error.
    assert [inspect(order).unloaded for order in saved] == [set()]
    assert [order.name for order in saved] == ["ok"]
    assert await _names(orders) == ["ok"]


@pytest.mark.parametrize(
    "savepoint",
    [
        pytest.param(
            False,
            id="plain",
            marks=pytest.mark.xfail(reason=KNOWN_ISSUE, raises=PendingRollbackError),
        ),
        pytest.param(True, id="savepoint"),
    ],
)
async def test_the_session_stays_usable_after_a_caught_conflict(orders, savepoint):
    await call(orders.view.handle_create(orders.schema(name="dup")))

    saved, errors = await _bulk_create(
        orders, ["first", "dup", "next"], savepoint=savepoint
    )

    assert errors == [fr.exc.Conflict]
    # Nothing expired the rows saved before the error, so async can read them.
    assert [inspect(order).unloaded for order in saved] == [set(), set()]
    assert [order.name for order in saved] == ["first", "next"]
    assert await _names(orders) == ["dup", "first", "next"]
