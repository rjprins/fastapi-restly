"""A view warns once, at its first write, when its session expires on commit:
``snapshot(new)`` in ``after_action_commit`` is then empty."""

import warnings
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, Session, sessionmaker

import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient
from fastapi_restly.views._lifecycle import _warn_if_session_expires

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

    with pytest.warns(UserWarning, match="GearView writes through a session with"):
        client.post("/gears", json={"name": "a"})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        client.post("/gears", json={"name": "b"})

    assert snapshots == [{}, {}]


def test_an_async_session_that_expires_warns_too():
    class CogView:
        session = AsyncSession(expire_on_commit=True)

    with pytest.warns(UserWarning, match="CogView writes through a session with"):
        _warn_if_session_expires(CogView())


def test_a_session_that_does_not_expire_does_not_warn():
    class WheelView:
        session = AsyncSession(expire_on_commit=False)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _warn_if_session_expires(WheelView())
