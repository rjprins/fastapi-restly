"""An IntegrityError becomes a 409 at the outermost write block.

A ``write_action`` outside any ``shared_write_action_commit()`` block, the CRUD
handlers included, turns an ``IntegrityError`` into ``fr.exc.Conflict``. So
does the outermost shared block, for what escapes it and for its own commit.
A block inside a shared block lets the raw error through, so a bulk route can
catch a failing row. Code outside a write block gets the raw error. The
end-to-end CRUD cases are in ``tests/test_errors_integrity.py``; the
PostgreSQL detail texts are in ``tests/postgres/test_dialect.py``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import ForeignKey
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly._integrity import _build_integrity_detail
from fastapi_restly.testing import RestlyTestClient

from ._models import SharedBase
from .conftest import create_tables


def _make_integrity_error(orig: Any) -> IntegrityError:
    """Construct a synthetic IntegrityError wrapping ``orig`` (no real DB)."""
    return IntegrityError("INSERT INTO foo VALUES (1)", {}, orig)


def _tag_models():
    class Tag(fr.IDMixin, SharedBase):
        name: Mapped[str] = mapped_column(unique=True)

    class TagSchema(fr.IDSchema):
        name: str

    return Tag, TagSchema


# ---------------------------------------------------------------------------
# Where the translation happens
# ---------------------------------------------------------------------------


def test_custom_route_write_action_conflict_is_409(client):
    Tag, TagSchema = _tag_models()

    @fr.include_view(client.app)
    class TagView(fr.AsyncRestView):
        prefix = "/tags"
        model = Tag
        schema = TagSchema

        class Action(fr.Action):
            RENAME = "rename"

        @fr.post("/{id}/rename", status_code=200)
        async def rename(self, id: int, name: str):
            tag = await self.get_one(id)
            async with self.write_action("rename", obj=tag):
                tag.name = name
            return self.to_response(tag)

    create_tables()
    client.post("/tags", json={"name": "a"})
    second = client.post("/tags", json={"name": "b"}).json()

    response = client.post(
        f"/tags/{second['id']}/rename?name=a", assert_status_code=409
    )
    assert "unique" in response.json()["detail"].lower()


def test_conflict_reaches_the_apps_restly_error_handler():
    """The 409 is a RestlyHTTPError, so the app's handler for those shapes it,
    and the IntegrityError stays reachable as its cause."""
    Tag, TagSchema = _tag_models()
    causes = []
    app = FastAPI()

    @app.exception_handler(fr.exc.RestlyHTTPError)
    async def problem(request: Request, exc: fr.exc.RestlyHTTPError):
        causes.append(type(exc.__cause__))
        return JSONResponse(
            status_code=exc.status_code,
            content={"title": exc.detail, "status": exc.status_code},
            media_type="application/problem+json",
        )

    @fr.include_view(app)
    class TagView(fr.AsyncRestView):
        prefix = "/tags"
        model = Tag
        schema = TagSchema

    create_tables()
    client = RestlyTestClient(app)
    client.post("/tags", json={"name": "a"})
    response = client.post("/tags", json={"name": "a"}, assert_status_code=409)

    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["status"] == 409
    assert causes == [IntegrityError]


def test_a_row_inside_a_shared_block_raises_the_raw_error(client):
    """The partial-success recipe: a savepoint per row, and the route catches
    the row's IntegrityError. The block inside the shared block must not turn
    it into a Conflict first."""
    Tag, TagSchema = _tag_models()

    @fr.include_view(client.app)
    class TagView(fr.AsyncRestView):
        prefix = "/tags"
        model = Tag
        schema = TagSchema

        @fr.post("/bulk", status_code=200)
        async def bulk(self, names: list[str]):
            created, skipped = 0, 0
            async with self.shared_write_action_commit():
                for name in names:
                    try:
                        async with self.session.begin_nested():
                            await self.handle_create(self.schema_create(name=name))
                        created += 1
                    except IntegrityError:
                        skipped += 1
            return {"created": created, "skipped": skipped}

    create_tables()
    client.post("/tags", json={"name": "a"})
    response = client.post("/tags/bulk", json=["a", "b", "c"])

    assert response.json() == {"created": 2, "skipped": 1}
    names = sorted(t["name"] for t in client.get("/tags").json()["data"])
    assert names == ["a", "b", "c"]


def test_an_error_that_escapes_the_shared_block_is_409(client):
    Tag, TagSchema = _tag_models()

    @fr.include_view(client.app)
    class TagView(fr.AsyncRestView):
        prefix = "/tags"
        model = Tag
        schema = TagSchema

        @fr.post("/bulk", status_code=200)
        async def bulk(self, names: list[str]):
            async with self.shared_write_action_commit():
                for name in names:
                    await self.handle_create(self.schema_create(name=name))
            return {"created": len(names)}

    create_tables()
    response = client.post("/tags/bulk", json=["a", "a"], assert_status_code=409)
    assert "unique" in response.json()["detail"].lower()
    assert client.get("/tags").json()["data"] == []


def test_after_action_commit_errors_stay_raw(client):
    """After the commit the write is saved, so a 409 would be wrong."""
    Tag, TagSchema = _tag_models()

    @fr.include_view(client.app)
    class TagView(fr.AsyncRestView):
        prefix = "/tags"
        model = Tag
        schema = TagSchema

        async def after_action_commit(self, action, new, old=None):
            raise _make_integrity_error(Exception("from the hook"))

    create_tables()
    with pytest.raises(IntegrityError):
        client.post("/tags", json={"name": "a"})


def test_a_bare_view_that_commits_itself_gets_the_raw_error(client):
    """Outside a write block, the route owns its commit and its errors."""
    Tag, _ = _tag_models()

    @fr.include_view(client.app)
    class ImportView(fr.View):
        prefix = "/import"
        session: fr.AsyncSessionDep

        @fr.post("/", status_code=204)
        async def receive(self, name: str):
            self.session.add(Tag(name=name))
            await self.session.commit()

    create_tables()
    client.post("/import/?name=a", assert_status_code=204)
    with pytest.raises(IntegrityError):
        client.post("/import/?name=a")


def _parent_child_models():
    class Parent(fr.IDMixin, SharedBase):
        name: Mapped[str] = mapped_column(unique=True)

    class Child(fr.IDMixin, SharedBase):
        parent_id: Mapped[int] = mapped_column(ForeignKey("parent.id"))

    class ParentSchema(fr.IDSchema):
        name: str

    return Parent, Child, ParentSchema


def test_async_update_and_delete_conflicts_are_409(client):
    Parent, Child, ParentSchema = _parent_child_models()

    @fr.include_view(client.app)
    class ParentView(fr.AsyncRestView):
        prefix = "/parents"
        model = Parent
        schema = ParentSchema

    create_tables()
    first = client.post("/parents", json={"name": "a"}).json()
    second = client.post("/parents", json={"name": "b"}).json()

    async def add_child():
        async with fr.db.open_async_session() as session:
            session.add(Child(parent_id=first["id"]))
            await session.commit()

    asyncio.run(add_child())

    client.patch(f"/parents/{second['id']}", json={"name": "a"}, assert_status_code=409)
    response = client.delete(f"/parents/{first['id']}", assert_status_code=409)
    assert "foreign key" in response.json()["detail"].lower()


def test_sync_update_and_delete_conflicts_are_409(sync_db):
    engine, make_session = sync_db
    Parent, Child, ParentSchema = _parent_child_models()
    app = FastAPI()

    @fr.include_view(app)
    class ParentView(fr.RestView):
        prefix = "/parents"
        model = Parent
        schema = ParentSchema

    SharedBase.metadata.create_all(engine)
    with engine.connect() as connection:  # the one StaticPool connection
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    client = RestlyTestClient(app)
    first = client.post("/parents", json={"name": "a"}).json()
    second = client.post("/parents", json={"name": "b"}).json()
    with make_session() as session:
        session.add(Child(parent_id=first["id"]))
        session.commit()

    client.patch(f"/parents/{second['id']}", json={"name": "a"}, assert_status_code=409)
    response = client.delete(f"/parents/{first['id']}", assert_status_code=409)
    assert "foreign key" in response.json()["detail"].lower()


@pytest.mark.parametrize("hook", ["authorize", "before_action_commit"])
def test_a_conflict_in_a_hook_before_the_commit_is_409(client, hook):
    Tag, TagSchema = _tag_models()

    class Hooks:
        async def authorize(self, action, obj=None, data=None):
            if hook == "authorize" and action == "rename":
                self.session.add(Tag(name="taken"))
                await self.session.flush()

        async def before_action_commit(self, action, new, old=None):
            if hook == "before_action_commit" and action == "rename":
                self.session.add(Tag(name="taken"))
                await self.session.flush()

    @fr.include_view(client.app)
    class TagView(Hooks, fr.AsyncRestView):
        prefix = "/tags"
        model = Tag
        schema = TagSchema

        class Action(fr.Action):
            RENAME = "rename"

        @fr.post("/{id}/rename", status_code=200)
        async def rename(self, id: int, name: str):
            tag = await self.get_one(id)
            async with self.write_action("rename", obj=tag):
                tag.name = name
            return self.to_response(tag)

    create_tables()
    client.post("/tags", json={"name": "taken"})
    tag = client.post("/tags", json={"name": "b"}).json()
    client.post(f"/tags/{tag['id']}/rename?name=c", assert_status_code=409)


def test_sync_after_action_commit_errors_stay_raw(sync_db):
    engine, _ = sync_db
    Tag, TagSchema = _tag_models()
    app = FastAPI()

    @fr.include_view(app)
    class TagView(fr.RestView):
        prefix = "/tags"
        model = Tag
        schema = TagSchema

        def after_action_commit(self, action, new, old=None):
            raise _make_integrity_error(Exception("from the hook"))

    SharedBase.metadata.create_all(engine)
    with pytest.raises(IntegrityError):
        RestlyTestClient(app).post("/tags", json={"name": "a"})


@pytest.mark.parametrize("outer", ["write_action", "shared_write_action_commit"])
def test_an_after_hook_inside_another_block_stays_raw(client, outer):
    """An after-hook runs after a commit, also when its block sits inside
    another block. The enclosing block must not turn its error into a 409."""
    Tag, TagSchema = _tag_models()

    @fr.include_view(client.app)
    class TagView(fr.AsyncRestView):
        prefix = "/tags"
        model = Tag
        schema = TagSchema

        class Action(fr.Action):
            OUTER = "outer"

        async def after_action_commit(self, action, new, old=None):
            if action == "create":
                raise _make_integrity_error(Exception("from the hook"))

        @fr.post("/nested", status_code=200)
        async def nested(self):
            if outer == "write_action":
                async with self.write_action("outer", obj=None):
                    await self.handle_create(self.schema_create(name="a"))
            else:
                async with self.shared_write_action_commit():
                    await self.handle_create(self.schema_create(name="a"))
            return {}

    create_tables()
    with pytest.raises(IntegrityError):
        client.post("/tags/nested")


def test_a_nested_shared_block_lets_the_raw_error_through(client):
    Tag, TagSchema = _tag_models()
    seen = []

    @fr.include_view(client.app)
    class TagView(fr.AsyncRestView):
        prefix = "/tags"
        model = Tag
        schema = TagSchema

        @fr.post("/nested", status_code=200)
        async def nested(self):
            async with self.shared_write_action_commit():
                try:
                    async with self.shared_write_action_commit():
                        await self.handle_create(self.schema_create(name="a"))
                        await self.handle_create(self.schema_create(name="a"))
                except IntegrityError:
                    seen.append("raw")
                    raise
            return {}

    create_tables()
    client.post("/tags/nested", assert_status_code=409)
    assert seen == ["raw"]


def test_sync_crud_and_write_action_conflicts_are_409(sync_db):
    engine, _ = sync_db
    Tag, TagSchema = _tag_models()
    app = FastAPI()

    @fr.include_view(app)
    class TagView(fr.RestView):
        prefix = "/tags"
        model = Tag
        schema = TagSchema

        class Action(fr.Action):
            RENAME = "rename"

        @fr.post("/{id}/rename", status_code=200)
        def rename(self, id: int, name: str):
            tag = self.get_one(id)
            with self.write_action("rename", obj=tag):
                tag.name = name
            return self.to_response(tag)

        @fr.post("/bulk", status_code=200)
        def bulk(self, names: list[str]):
            with self.shared_write_action_commit():
                for name in names:
                    self.handle_create(self.schema_create(name=name))
            return {"created": len(names)}

    SharedBase.metadata.create_all(engine)
    client = RestlyTestClient(app)
    client.post("/tags", json={"name": "a"})
    second = client.post("/tags", json={"name": "b"}).json()

    client.post("/tags", json={"name": "a"}, assert_status_code=409)
    client.post(f"/tags/{second['id']}/rename?name=a", assert_status_code=409)
    client.post("/tags/bulk", json=["c", "c"], assert_status_code=409)


# ---------------------------------------------------------------------------
# Detail-string classification
# ---------------------------------------------------------------------------


# The SQLSTATE code lives under a different attribute per driver: psycopg 3
# (Restly's PostgreSQL stack) and asyncpg expose ``sqlstate``, psycopg 2 exposes
# ``pgcode``. Cover both so the classifier can't silently regress on either --
# reading only ``pgcode`` degraded every psycopg 3 error to the generic fallback.
@pytest.mark.parametrize("code_attr", ["sqlstate", "pgcode"])
@pytest.mark.parametrize(
    "code, expected_substring",
    [
        ("23505", "unique"),
        ("23503", "foreign key"),
        ("23502", "not-null"),
        ("23514", "check"),
    ],
)
def test_postgres_sqlstate_classification(code_attr, code, expected_substring):
    """Postgres SQLSTATE codes drive the human-readable detail."""
    orig = SimpleNamespace(
        **{code_attr: code},
        diag=SimpleNamespace(constraint_name=None, column_name=None),
    )
    exc = _make_integrity_error(orig)
    detail = _build_integrity_detail(exc)
    assert expected_substring in detail.lower()


def test_postgres_sqlstate_includes_constraint_name():
    """When psycopg's ``orig.diag`` carries a constraint name, we surface it."""
    orig = SimpleNamespace(
        sqlstate="23505",
        diag=SimpleNamespace(constraint_name="uq_user_email", column_name=None),
    )
    exc = _make_integrity_error(orig)
    detail = _build_integrity_detail(exc)
    assert "uq_user_email" in detail
    assert "unique" in detail.lower()


def test_postgres_sqlstate_includes_constraint_name_from_asyncpg():
    """asyncpg has no ``diag``. SQLAlchemy raises its wrapper from asyncpg's own
    exception, which carries the names."""

    class UniqueViolationError(Exception):
        constraint_name = "uq_user_username"
        column_name = None

    class AsyncAdaptIntegrityError(Exception):
        sqlstate = "23505"

    orig = AsyncAdaptIntegrityError("duplicate key value")
    orig.__cause__ = UniqueViolationError("duplicate key value")
    detail = _build_integrity_detail(_make_integrity_error(orig))
    assert detail == "Unique constraint violated: uq_user_username"


def test_postgres_sqlstate_not_null_includes_column():
    orig = SimpleNamespace(
        sqlstate="23502",
        diag=SimpleNamespace(constraint_name=None, column_name="email"),
    )
    detail = _build_integrity_detail(_make_integrity_error(orig))
    assert "not-null" in detail.lower()
    assert "email" in detail


def test_sqlite_unique_message_classification():
    """A real SQLite-style error message is parsed and surfaces the column."""
    orig = Exception("UNIQUE constraint failed: user.username")
    detail = _build_integrity_detail(_make_integrity_error(orig))
    assert "unique" in detail.lower()
    assert "user.username" in detail


def test_sqlite_foreign_key_message_classification():
    orig = Exception("FOREIGN KEY constraint failed")
    detail = _build_integrity_detail(_make_integrity_error(orig))
    assert "foreign key" in detail.lower()


def test_sqlite_not_null_message_classification():
    orig = Exception("NOT NULL constraint failed: user.email")
    detail = _build_integrity_detail(_make_integrity_error(orig))
    assert "not-null" in detail.lower()
    assert "user.email" in detail


def test_sqlite_check_message_classification():
    orig = Exception("CHECK constraint failed: positive_quantity")
    detail = _build_integrity_detail(_make_integrity_error(orig))
    assert "check" in detail.lower()


def test_fallback_for_unknown_dialect():
    """Unrecognised ``orig`` (no pgcode, unknown message) falls back to
    the generic conflict message and includes the truncated original."""
    orig = Exception("some weird custom database error nobody has seen before")
    detail = _build_integrity_detail(_make_integrity_error(orig))
    assert "conflict" in detail.lower()
    assert "weird custom database error" in detail


def test_fallback_truncates_long_orig_text():
    long_text = "x" * 2000
    orig = Exception(long_text)
    detail = _build_integrity_detail(_make_integrity_error(orig))
    assert "truncated" in detail
    # We cap at 500 chars + the "...(truncated)" suffix; the resulting
    # detail must therefore be much shorter than the raw 2000 chars.
    assert len(detail) < 700
