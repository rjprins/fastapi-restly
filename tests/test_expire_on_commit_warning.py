"""A view warns once, at a write through a session that expires on commit:
``snapshot(new)`` in ``after_action_commit`` is then empty."""

import warnings
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, Session, sessionmaker

import fastapi_restly as fr
from fastapi_restly.exc import RestlyExpireOnCommitWarning
from fastapi_restly.testing import RestlyTestClient

from ._models import SharedBase


def test_a_view_on_an_expiring_session_warns_once(sync_db):
    engine, _ = sync_db
    make_session = sessionmaker(bind=engine)  # SQLAlchemy's default: expires

    def get_session():
        with make_session() as session:
            yield session

    class Gear(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class GearView(fr.RestView):
        prefix = "/gears"
        model = Gear
        session: Annotated[Session, Depends(get_session)]

    snapshots = []

    def after_action_commit(self, action, new, old=None):
        snapshots.append(self.snapshot(new))

    GearView.after_action_commit = after_action_commit
    app = FastAPI()
    fr.include_view(app, GearView)
    SharedBase.metadata.create_all(engine)
    client = RestlyTestClient(app)

    with pytest.warns(RestlyExpireOnCommitWarning, match="GearView writes through"):
        client.post("/gears", json={"name": "a"})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        client.post("/gears", json={"name": "b"})

    assert snapshots == [{}, {}]


# The views below write through write_action(obj=None) with no change, so the
# sessions need no database: the bracket runs, flushes nothing and commits
# nothing.


def _touch_view(view_base, get_session, session_type):
    class Cog(fr.IDMixin, SharedBase):
        name: Mapped[str]

    if view_base is fr.AsyncRestView:

        class AsyncCogView(view_base):
            prefix = "/cogs"
            model = Cog
            session: Annotated[session_type, Depends(get_session)]

            class Action(fr.Action):
                TOUCH = "touch"

            @fr.post("/touch")
            async def touch(self) -> dict:
                async with self.write_action(self.Action.TOUCH, obj=None):
                    pass
                return {}

        return AsyncCogView

    class CogView(view_base):
        prefix = "/cogs"
        model = Cog
        session: Annotated[session_type, Depends(get_session)]

        class Action(fr.Action):
            TOUCH = "touch"

        @fr.post("/touch")
        def touch(self) -> dict:
            with self.write_action(self.Action.TOUCH, obj=None):
                pass
            return {}

    return CogView


def test_an_async_view_on_an_expiring_session_warns():
    async def get_session():
        yield AsyncSession(expire_on_commit=True)

    app = FastAPI()
    fr.include_view(app, _touch_view(fr.AsyncRestView, get_session, AsyncSession))

    with pytest.warns(RestlyExpireOnCommitWarning, match="AsyncCogView writes"):
        RestlyTestClient(app).post("/cogs/touch")


def test_a_view_on_a_session_that_does_not_expire_does_not_warn():
    def get_session():
        yield Session(expire_on_commit=False)

    app = FastAPI()
    fr.include_view(app, _touch_view(fr.RestView, get_session, Session))

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        RestlyTestClient(app).post("/cogs/touch")


def test_a_session_that_does_not_expire_does_not_hide_one_that_does():
    """The same view on two apps, and only the second app's session expires."""

    def get_session():
        yield Session(expire_on_commit=False)

    def get_expiring_session():
        yield Session(expire_on_commit=True)

    view = _touch_view(fr.RestView, get_session, Session)
    first, second = FastAPI(), FastAPI()
    fr.include_view(first, view)
    fr.include_view(second, view)
    second.dependency_overrides[get_session] = get_expiring_session

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        RestlyTestClient(first).post("/cogs/touch")
    with pytest.warns(RestlyExpireOnCommitWarning, match="CogView writes"):
        RestlyTestClient(second).post("/cogs/touch")
