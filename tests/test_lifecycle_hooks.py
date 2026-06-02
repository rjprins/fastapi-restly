"""Tests for the handle-design transaction bracket.

Covers ``snapshot`` -> ``before_commit`` -> commit -> ``after_commit``: that
``old`` is the pre-mutation snapshot, that ``after_commit`` runs after the write
is durable, and that a ``before_commit`` failure aborts the write (it runs
*inside* the transaction).
"""

from sqlalchemy.orm import Mapped

import fastapi_restly as fr

from .conftest import create_tables


def test_snapshot_is_pre_mutation_old_in_after_commit(client):
    """``after_commit`` receives ``old`` = the pre-update snapshot while the
    object itself already reflects the new state."""

    class Doc(fr.IDBase):
        title: Mapped[str]

    class DocSchema(fr.IDSchema):
        title: str

    captured: dict = {}

    @fr.include_view(client.app)
    class DocView(fr.AsyncRestView):
        prefix = "/docs"
        model = Doc
        schema = DocSchema

        async def after_commit(self, action, new, old=None):
            captured["action"] = action
            captured["old"] = old
            captured["new_title"] = new.title if new is not None else None

    create_tables()

    doc = client.post("/docs/", json={"title": "v1"}).json()
    client.patch(f"/docs/{doc['id']}", json={"title": "v2"})

    assert captured["action"] == "update"
    assert captured["old"]["title"] == "v1"  # snapshot taken before the write
    assert captured["new_title"] == "v2"


def test_before_commit_failure_aborts_the_write(client):
    """``before_commit`` runs inside the transaction: raising there discards the
    in-flight write (nothing is committed)."""

    class Doc(fr.IDBase):
        title: Mapped[str]

    class DocSchema(fr.IDSchema):
        title: str

    @fr.include_view(client.app)
    class DocView(fr.AsyncRestView):
        prefix = "/docs"
        model = Doc
        schema = DocSchema

        async def before_commit(self, action, new, old=None):
            # An in-transaction guard (e.g. a uniqueness/outbox check) that
            # rejects the write. Runs before the commit, so the write aborts.
            raise fr.exc.Conflict("duplicate outbox entry")

    create_tables()

    client.post("/docs/", json={"title": "v1"}, assert_status_code=409)

    # The failed before_commit means the create was never committed.
    assert client.get("/docs/").json() == []
