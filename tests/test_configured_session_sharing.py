"""A configured generator participates in FastAPI's dependency cache."""

import asyncio
import warnings
from types import SimpleNamespace
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import ForeignKey, create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr
from fastapi_restly.db._globals import RestlyContext


class Base(DeclarativeBase):
    pass


class Owner(Base):
    __tablename__ = "sharing_owner"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


class Item(Base):
    __tablename__ = "sharing_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    owner_id: Mapped[int] = mapped_column(ForeignKey(Owner.id))
    owner: Mapped[Owner] = relationship()


class ItemSchema(fr.IDSchema):
    name: str
    owner_id: fr.ReadOnly[int]


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def sessions(request):
    asynchronous = request.param
    calls = []
    closed = []
    with RestlyContext():
        if asynchronous:
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            factory = async_sessionmaker(engine, expire_on_commit=False)

            async def prepare():
                async with engine.begin() as connection:
                    await connection.run_sync(Base.metadata.create_all)
                    await connection.execute(
                        Owner.__table__.insert(), {"id": 1, "name": "Ada"}
                    )

            asyncio.run(prepare())

            async def get_db():
                async with factory() as session:
                    calls.append(session)
                    try:
                        yield session
                    finally:
                        closed.append(session)

            configure = lambda: fr.configure(session_generator=get_db)
            session_type = AsyncSession
            restly_dep = fr.AsyncSessionDep
            view_base = fr.AsyncRestView
        else:
            engine = create_engine(
                "sqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
            factory = sessionmaker(engine, expire_on_commit=False)
            Base.metadata.create_all(engine)
            with engine.begin() as connection:
                connection.execute(Owner.__table__.insert(), {"id": 1, "name": "Ada"})

            def get_db():
                with factory() as session:
                    calls.append(session)
                    try:
                        yield session
                    finally:
                        closed.append(session)

            configure = lambda: fr.configure(sync_session_generator=get_db)
            session_type = Session
            restly_dep = fr.SessionDep
            view_base = fr.RestView

        try:
            yield SimpleNamespace(
                asynchronous=asynchronous,
                factory=factory,
                get_db=get_db,
                configure=configure,
                calls=calls,
                closed=closed,
                session_type=session_type,
                restly_dep=restly_dep,
                view_base=view_base,
            )
        finally:
            if asynchronous:
                asyncio.run(engine.dispose())
            else:
                engine.dispose()


@pytest.mark.parametrize("owner_dependency", ["application", "restly"])
def test_view_route_and_owner_dependency_share_one_session(sessions, owner_dependency):
    sessions.configure()
    app_dep = Annotated[sessions.session_type, Depends(sessions.get_db)]
    owner_dep = app_dep if owner_dependency == "application" else sessions.restly_dep
    if sessions.asynchronous:

        async def get_owner(session: owner_dep):
            return session, await session.get(Owner, 1)

        class ItemView(sessions.view_base):
            async def create(self, data):
                obj = await self.make_new_object(data)
                obj.owner = self.owner_state[1]
                return await self.save_object(obj)
    else:

        def get_owner(session: owner_dep):
            return session, session.get(Owner, 1)

        class ItemView(sessions.view_base):
            def create(self, data):
                obj = self.make_new_object(data)
                obj.owner = self.owner_state[1]
                return self.save_object(obj)

    class BoundView(ItemView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        owner_state: Annotated[tuple, Depends(get_owner)]

        @fr.get("/shared")
        def shared(self, session: app_dep):
            return self.session is session is self.owner_state[0]

    app = FastAPI()
    fr.include_view(app, BoundView)
    with TestClient(app) as client:
        response = client.get("/items/shared")
        assert response.status_code == 200
        assert response.json() is True
        assert len(sessions.calls) == len(sessions.closed) == 1

        response = client.post("/items", json={"name": "Book"})
        assert response.status_code == 201, response.text
        assert response.json()["owner_id"] == 1
        assert len(sessions.calls) == len(sessions.closed) == 2


def test_explicit_view_dependency_wins_over_configured_default(sessions):
    sessions.configure()
    sentinel = object()

    def reporting_session():
        return sentinel

    class ReportingView(sessions.view_base):
        prefix = "/reports"
        model = Item
        schema = ItemSchema
        session: Annotated[object, Depends(reporting_session)]

        @fr.get("/source")
        def source(self):
            return self.session is sentinel

    app = FastAPI()
    fr.include_view(app, ReportingView)
    with TestClient(app) as client:
        assert client.get("/reports/source").json() is True
    assert sessions.calls == []


def test_configuring_generator_after_view_registration_raises(sessions):
    class ItemView(sessions.view_base):
        prefix = "/items"
        model = Item
        schema = ItemSchema

    fr.include_view(FastAPI(), ItemView)
    with pytest.raises(fr.exc.RestlyConfigurationError, match="before.*register"):
        sessions.configure()


def test_configuring_generator_after_plain_route_registration_raises(sessions):
    app = FastAPI()

    @app.get("/session")
    def session_endpoint(session: sessions.restly_dep):
        return True

    with pytest.raises(fr.exc.RestlyConfigurationError, match="before.*register"):
        sessions.configure()


def test_configured_generator_is_cleaned_up_when_a_route_raises(sessions):
    sessions.configure()
    app = FastAPI()

    @app.get("/fails")
    def fails(session: sessions.restly_dep):
        raise RuntimeError("route failed")

    with TestClient(app) as client, pytest.raises(RuntimeError, match="route failed"):
        client.get("/fails")
    assert len(sessions.calls) == len(sessions.closed) == 1


def test_application_override_is_shared_by_native_and_restly_dependencies(sessions):
    sessions.configure()
    sentinel = object()
    app = FastAPI()

    @app.get("/shared")
    def shared(
        session: sessions.restly_dep,
        native: Annotated[sessions.session_type, Depends(sessions.get_db)],
    ):
        return session is native is sentinel

    app.dependency_overrides[sessions.get_db] = lambda: sentinel
    with TestClient(app) as client:
        assert client.get("/shared").json() is True
    assert sessions.calls == []


def test_plain_route_and_nested_dependencies_share_configured_session(sessions):
    sessions.configure()
    app_dep = Annotated[sessions.session_type, Depends(sessions.get_db)]

    def nested(session: sessions.restly_dep):
        return session

    app = FastAPI()

    @app.get("/shared")
    def shared(
        native: app_dep,
        restly: sessions.restly_dep,
        indirect: Annotated[object, Depends(nested)],
    ):
        return native is restly is indirect

    with TestClient(app) as client:
        assert client.get("/shared").json() is True
    assert len(sessions.calls) == len(sessions.closed) == 1


@pytest.mark.parametrize("on_view", [False, True], ids=["plain-route", "view"])
def test_configured_session_warns_for_an_uncommitted_flush(sessions, on_view):
    sessions.configure()
    app = FastAPI()
    if sessions.asynchronous:

        async def forget(session: sessions.restly_dep):
            session.add(Item(name="uncommitted", owner_id=1))
            await session.flush()
            return {"ok": True}

        class WriteView(sessions.view_base):
            @fr.post("/forgot")
            async def forgot(self):
                return await forget(self.session)
    else:

        def forget(session: sessions.restly_dep):
            session.add(Item(name="uncommitted", owner_id=1))
            session.flush()
            return {"ok": True}

        class WriteView(sessions.view_base):
            @fr.post("/forgot")
            def forgot(self):
                return forget(self.session)

    if on_view:

        class BoundView(WriteView):
            prefix = "/items"
            model = Item
            schema = ItemSchema

        fr.include_view(app, BoundView)
        path = "/items/forgot"
    else:
        app.post("/forgot")(forget)
        path = "/forgot"

    with TestClient(app) as client, warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert client.post(path).status_code in (200, 201)
    assert (
        sum(
            issubclass(w.category, fr.exc.RestlyUncommittedChangesWarning)
            for w in caught
        )
        == 1
    )
    assert len(sessions.calls) == len(sessions.closed) == 1


def test_restly_owned_session_is_shared_with_application_dependencies(sessions):
    if sessions.asynchronous:
        fr.configure(async_make_session=sessions.factory)
    else:
        fr.configure(make_session=sessions.factory)

    def get_session(session: sessions.restly_dep):
        return session

    class SharedView(sessions.view_base):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        app_session: Annotated[object, Depends(get_session)]

        @fr.get("/shared")
        def shared(self, session: sessions.restly_dep):
            return self.session is self.app_session is session

    app = FastAPI()
    fr.include_view(app, SharedView)
    with TestClient(app) as client:
        assert client.get("/items/shared").json() is True


def test_configured_generator_can_have_fastapi_dependencies(sessions):
    def get_marker():
        return "resolved"

    if sessions.asynchronous:

        async def get_db(marker: Annotated[str, Depends(get_marker)]):
            assert marker == "resolved"
            async for session in sessions.get_db():
                yield session

        fr.configure(session_generator=get_db)
    else:

        def get_db(marker: Annotated[str, Depends(get_marker)]):
            assert marker == "resolved"
            yield from sessions.get_db()

        fr.configure(sync_session_generator=get_db)

    app = FastAPI()

    @app.get("/shared")
    def shared(
        session: sessions.restly_dep,
        native: Annotated[sessions.session_type, Depends(get_db)],
    ):
        return session is native

    with TestClient(app) as client:
        assert client.get("/shared").json() is True
    assert len(sessions.calls) == len(sessions.closed) == 1
