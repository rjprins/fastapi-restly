"""A route that catches the error of a write action puts each write action in
``session.begin_nested()``, as docs/lifecycle.md shows.

The engines are plain, as in production: SQLite without the SAVEPOINT fix
that the test fixtures install.
"""

from contextlib import asynccontextmanager
from inspect import isawaitable
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, Session, mapped_column

import fastapi_restly as fr

from ._models import SharedBase

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _call(value):
    return await value if isawaitable(value) else value


@asynccontextmanager
async def _enter(manager):
    if hasattr(manager, "__aenter__"):
        async with manager as value:
            yield value
    else:
        with manager as value:
            yield value


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
    view_type = type(
        "OrderView",
        (fr.AsyncRestView if asynchronous else fr.RestView,),
        {
            "model": Order,
            "schema": OrderSchema,
            "prefix": "/orders",
            "before_action_commit": async_before if asynchronous else before,
        },
    )
    fr.include_view(FastAPI())(view_type)

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

        async with _enter(make_session()) as session:
            yield SimpleNamespace(
                view=view_type(request=None, session=session),
                session=session,
                make_session=make_session,
                model=Order,
                schema=view_type.schema_create,
            )
    finally:
        await _call(engine.dispose())


async def _bulk_create(orders, names):
    """The loop from the docs: a savepoint around each handler call."""
    saved, errors = [], []
    for name in names:
        try:
            async with _enter(orders.session.begin_nested()):
                order = await _call(orders.view.handle_create(orders.schema(name=name)))
                saved.append(order)
        except HTTPException as exc:
            errors.append(type(exc))
    return saved, errors


async def _names(orders):
    async with _enter(orders.make_session()) as session:
        stmt = select(orders.model.name).order_by(orders.model.id)
        return list(await _call(session.scalars(stmt)))


async def test_a_savepoint_undoes_a_refused_write(orders):
    saved, errors = await _bulk_create(orders, ["rejected", "ok", "rejected"])

    assert errors == [HTTPException, HTTPException]
    assert [order.name for order in saved] == ["ok"]
    assert await _names(orders) == ["ok"]


async def test_a_savepoint_keeps_the_session_usable_after_a_conflict(orders):
    await _call(orders.view.handle_create(orders.schema(name="dup")))

    saved, errors = await _bulk_create(orders, ["first", "dup", "next"])

    assert errors == [fr.exc.Conflict]
    # The rows saved before the error are still loaded, so async needs no I/O.
    assert [order.name for order in saved] == ["first", "next"]
    assert await _names(orders) == ["dup", "first", "next"]
