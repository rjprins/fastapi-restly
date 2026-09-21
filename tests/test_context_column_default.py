"""A ContextNamespace slot bound per request reaches a column default.

This is the write half of the model layer: a server-stamped field is
``insert_default=Current.user_id`` on the column, and the bind
made by ``Current.depends(...)`` in the request task must be visible where
the default runs. On the async path that is inside SQLAlchemy's flush
greenlet, not the endpoint coroutine, so it is pinned here for both views.
Both the direct callable and a lambda wrapper are covered.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly.db._globals import _fr_globals
from fastapi_restly.testing._client import RestlyTestClient


@pytest.fixture
def sync_client(sync_db) -> Iterator[RestlyTestClient]:
    app = FastAPI()
    yield RestlyTestClient(app)


def _create_sync_tables():
    fr.DataclassBase.metadata.create_all(_fr_globals.make_session.kw["bind"])


def _define(prefix: str, *, wrap_default: bool):
    class Ctx(fr.ContextNamespace):
        user_id: fr.ContextParam[int | None]

    read_user_id = (lambda: Ctx.user_id()) if wrap_default else Ctx.user_id

    class Note(fr.IDBase):
        title: Mapped[str]
        # default=None is the constructor default on a dataclass base; the
        # insert-time callable goes in insert_default
        created_by_id: Mapped[int | None] = mapped_column(
            default=None, insert_default=read_user_id
        )
        updated_by_id: Mapped[int | None] = mapped_column(
            default=None, insert_default=read_user_id, onupdate=read_user_id
        )

    class NoteSchema(fr.IDSchema):
        title: str
        created_by_id: fr.ReadOnly[int | None] = None
        updated_by_id: fr.ReadOnly[int | None] = None

    return Ctx, Note, NoteSchema


def _exercise(client: Any, get_user_id: Any) -> None:
    note = client.post("/notes/", json={"title": "mine"}).json()
    assert (note["created_by_id"], note["updated_by_id"]) == (7, 7)

    # a payload value never lands: the fields are read-only on the schema
    client.app.dependency_overrides[get_user_id] = lambda: 9
    try:
        updated = client.patch(
            f"/notes/{note['id']}", json={"title": "renamed", "created_by_id": 1}
        ).json()
        another = client.post("/notes/", json={"title": "another"}).json()
    finally:
        client.app.dependency_overrides.pop(get_user_id)
    assert (updated["created_by_id"], updated["updated_by_id"]) == (7, 9)
    assert (another["created_by_id"], another["updated_by_id"]) == (9, 9)


@pytest.mark.parametrize("wrap_default", [False, True], ids=["direct", "lambda"])
def test_async_flush_sees_the_request_bind(client, wrap_default):
    Ctx, Note, NoteSchema = _define("/notes", wrap_default=wrap_default)

    def get_user_id() -> int:
        return 7

    @fr.include_view(client.app)
    class NoteView(fr.AsyncRestView):
        prefix = "/notes"
        model = Note
        schema = NoteSchema
        dependencies = [Ctx.depends(user_id=get_user_id)]

    from .conftest import create_tables

    create_tables()
    _exercise(client, get_user_id)


@pytest.mark.parametrize("wrap_default", [False, True], ids=["direct", "lambda"])
def test_sync_flush_sees_the_request_bind(sync_client, wrap_default):
    Ctx, Note, NoteSchema = _define("/notes", wrap_default=wrap_default)

    def get_user_id() -> int:
        return 7

    @fr.include_view(sync_client.app)
    class NoteView(fr.RestView):
        prefix = "/notes"
        model = Note
        schema = NoteSchema
        dependencies = [Ctx.depends(user_id=get_user_id)]

    _create_sync_tables()
    _exercise(sync_client, get_user_id)
