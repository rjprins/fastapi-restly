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

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
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


def test_sync_crud_and_write_action_conflicts_are_409(sync_db):
    engine, _ = sync_db
    Tag, TagSchema = _tag_models()
    app = FastAPI()

    @fr.include_view(app)
    class TagView(fr.RestView):
        prefix = "/tags"
        model = Tag
        schema = TagSchema

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


def test_configure_with_only_an_app_is_still_a_setup_call():
    """It attaches the OpenAPI check for two classes with the same name."""
    fr.configure(FastAPI())


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
