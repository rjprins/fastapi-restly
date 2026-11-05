"""``atomic_write_actions = False`` in the shapes that bring their own setup:
real requests, the lint, a sync view in an async session, and the fixtures of the
tests. tests/test_atomic_write_actions.py has the rule itself.
"""

import warnings
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr
import fastapi_restly._pytest_fixtures as _fixtures
from fastapi_restly.db._config import RestlyContext, _fr_config
from fastapi_restly.exc import RestlyMisuseWarning

from ._models import SharedBase
from ._write_env import registered


def _lint_warnings(atomic):
    """The commit lint for a business method and a route that commit."""
    fr.configure(warn_on_misuse=True)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            class Base(fr.ModelDefaults, DeclarativeBase):
                pass

            class Tag(fr.IDMixin, Base):
                name: Mapped[str]

            class TagView(fr.RestView):
                model = Tag
                prefix = "/tags"
                atomic_write_actions = atomic

                def create(self, data):
                    tag = super().create(data)
                    self.session.commit()
                    return tag

                @fr.post("/quick")
                def quick(self):
                    self.session.add(Tag(name="quick"))
                    self.session.commit()

            registered(TagView)
    finally:
        fr.configure(warn_on_misuse=False)
    return {
        attr: str(w.message)
        for w in caught
        if issubclass(w.category, RestlyMisuseWarning)
        for attr in ("create", "quick")
        if f"TagView.{attr} calls session.commit()" in str(w.message)
    }


def test_the_lint_names_the_setting_for_a_business_method_of_a_strict_view():
    """The setting helps only code that runs in a write action."""
    found = _lint_warnings(True)
    assert "atomic_write_actions = False" in found["create"]
    assert "atomic_write_actions" not in found["quick"]


def test_the_lint_warns_only_about_a_route_on_a_non_atomic_view():
    """A route that commits skips the steps, also on this view."""
    assert set(_lint_warnings(False)) == {"quick"}


def test_a_commit_in_a_business_method_of_a_sync_view_over_http(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'http.db'}", connect_args={"check_same_thread": False}
    )

    class Tag(fr.IDMixin, SharedBase):
        name: Mapped[str] = mapped_column(unique=True)

    class TagSchema(fr.IDSchema):
        name: str

    class TagView(fr.RestView):
        model = Tag
        schema = TagSchema
        prefix = "/tags"
        atomic_write_actions = False

        def create(self, data):
            tag = super().create(data)
            self.session.add(tag)
            self.session.commit()
            return tag

        def update(self, obj, data):
            tag = super().update(obj, data)
            self.session.commit()
            return tag

        def delete(self, obj):
            super().delete(obj)
            self.session.commit()

    SharedBase.metadata.create_all(engine)
    fr.configure(make_session=sessionmaker(bind=engine, expire_on_commit=False))
    app = FastAPI()
    fr.include_view(app)(TagView)

    try:
        with TestClient(app) as client:
            created = client.post("/tags/", json={"name": "new"})
            assert created.status_code == 201
            tag_id = created.json()["id"]
            changed = client.patch(f"/tags/{tag_id}", json={"name": "changed"})
            assert changed.status_code == 200
            assert changed.json()["name"] == "changed"
            assert client.post("/tags/", json={"name": "changed"}).status_code == 409
            assert client.delete(f"/tags/{tag_id}").status_code == 204
            assert client.get(f"/tags/{tag_id}").status_code == 404
    finally:
        engine.dispose()


def test_a_commit_in_a_business_method_of_an_async_view_over_http(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'http.db'}")

    class Tag(fr.IDMixin, SharedBase):
        name: Mapped[str] = mapped_column(unique=True)

    class TagSchema(fr.IDSchema):
        name: str

    class TagView(fr.AsyncRestView):
        model = Tag
        schema = TagSchema
        prefix = "/tags"
        atomic_write_actions = False

        async def create(self, data):
            tag = await super().create(data)
            self.session.add(tag)
            await self.session.commit()
            return tag

        async def update(self, obj, data):
            tag = await super().update(obj, data)
            await self.session.commit()
            return tag

        async def delete(self, obj):
            await super().delete(obj)
            await self.session.commit()

    fr.configure(
        async_make_session=async_sessionmaker(bind=engine, expire_on_commit=False)
    )

    @asynccontextmanager
    async def lifespan(_app):
        async with engine.begin() as connection:
            await connection.run_sync(SharedBase.metadata.create_all)
        yield
        await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    fr.include_view(app)(TagView)

    with TestClient(app) as client:
        created = client.post("/tags/", json={"name": "new"})
        assert created.status_code == 201
        tag_id = created.json()["id"]
        changed = client.patch(f"/tags/{tag_id}", json={"name": "changed"})
        assert changed.status_code == 200
        assert changed.json()["name"] == "changed"
        assert client.post("/tags/", json={"name": "changed"}).status_code == 409
        assert client.delete(f"/tags/{tag_id}").status_code == 204
        assert client.get(f"/tags/{tag_id}").status_code == 404


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_a_commit_in_a_sync_action_through_run_sync(tmp_path, anyio_backend):
    """On its own the sync action commits. In the block of a strict async view
    it joins that block, so its commit is refused and nothing is saved."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'run_sync.db'}")

    class Tag(fr.IDMixin, SharedBase):
        name: Mapped[str] = mapped_column(unique=True)

    class TagSchema(fr.IDSchema):
        name: str

    class SyncView(fr.RestView):
        model = Tag
        schema = TagSchema
        prefix = "/sync"
        atomic_write_actions = False

        def create(self, data):
            tag = super().create(data)
            self.session.add(tag)
            self.session.commit()
            return tag

    class AsyncView(fr.AsyncRestView):
        model = Tag
        schema = TagSchema
        prefix = "/async"

    registered(SyncView, AsyncView)

    def create_in_sync_view(session, name):
        view = SyncView(request=None, session=session)
        return view.handle_create(SyncView.create_schema(name=name))

    async with engine.begin() as connection:
        await connection.run_sync(SharedBase.metadata.create_all)

    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await session.run_sync(create_in_sync_view, "alone")

            outer = AsyncView(request=None, session=session)
            with pytest.raises(RuntimeError, match="cannot be called inside"):
                async with outer.write_action("create", obj=Tag(name="outer")):
                    await session.run_sync(create_in_sync_view, "joined")

        async with AsyncSession(engine, expire_on_commit=False) as session:
            names = await session.scalars(select(Tag.name).order_by(Tag.id))
            assert list(names) == ["alone"]
    finally:
        await engine.dispose()


def test_a_commit_in_an_action_under_the_savepoint_fixtures():
    """The session of the fixtures runs in a test transaction that never
    commits. Its commit acts as a commit for the code of the action. The error
    after it keeps the commit, as in production."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class Tag(fr.IDMixin, Base):
        name: Mapped[str] = mapped_column(unique=True)

    class TagSchema(fr.IDSchema):
        name: str

    class TagView(fr.RestView):
        model = Tag
        schema = TagSchema
        prefix = "/tags"
        atomic_write_actions = False

        def create(self, data):
            tag = super().create(data)
            self.session.add(tag)
            self.session.commit()
            if tag.name == "refused":
                raise HTTPException(400, "no")
            return tag

    registered(TagView)
    make_session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    try:
        with RestlyContext():
            _fr_config.make_session = make_session
            source = _fixtures._source_factories()[0]
            connections = _fixtures._shared_connection.__wrapped__(
                SimpleNamespace(fixturenames=["restly_session"]), source
            )
            scope = _fixtures._restly_sync_scope.__wrapped__(next(connections), source)
            sessions = _fixtures.restly_session.__wrapped__(next(scope))
            session = next(sessions)
            try:
                view = TagView(request=None, session=session)
                view.handle_create(TagView.create_schema(name="first"))
                with pytest.raises(HTTPException):
                    view.handle_create(TagView.create_schema(name="refused"))
                stmt = select(Tag.name).order_by(Tag.id)
                assert list(session.scalars(stmt)) == ["first", "refused"]
            finally:
                sessions.close()
                scope.close()
                connections.close()
        with Session(engine) as session:
            assert list(session.scalars(select(Tag.name))) == []
    finally:
        engine.dispose()
