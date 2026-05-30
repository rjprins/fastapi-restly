"""Tests for the handle-design lifecycle seams.

Covers the cooperative stamping seams (``prepare_create`` / ``prepare_update``)
and the transaction bracket (``snapshot`` -> ``before_commit`` -> commit ->
``after_commit``): that stamped fields land on the row, that ``old`` is the
pre-mutation snapshot, and that a ``before_commit`` failure aborts the write
(it runs *inside* the transaction).
"""

from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr

from .conftest import create_tables


def _slug(title: str) -> str:
    return title.lower().replace(" ", "-")


def test_prepare_create_stamps_extra_field(client):
    """``prepare_create`` extras are applied after construction and win over
    whatever the client sent for that field."""

    class Doc(fr.IDBase):
        title: Mapped[str]
        slug: Mapped[str] = mapped_column(default="")

    class DocSchema(fr.IDSchema):
        title: str
        slug: str

    @fr.include_view(client.app)
    class DocView(fr.AsyncRestView):
        prefix = "/docs"
        model = Doc
        schema = DocSchema

        async def prepare_create(self, schema_obj):
            fields = await super().prepare_create(schema_obj)
            fields["slug"] = _slug(schema_obj.title)
            return fields

    create_tables()

    created = client.post(
        "/docs/", json={"title": "Hello World", "slug": "client-sent"}
    ).json()
    assert created["slug"] == "hello-world"  # stamped, not the client value


def test_prepare_update_stamps_extra_field(client):
    """``prepare_update`` can derive a server-controlled field from the loaded
    object on every update."""

    class Doc(fr.IDBase):
        title: Mapped[str]
        revision: Mapped[int] = mapped_column(default=0)

    class DocSchema(fr.IDSchema):
        title: str
        revision: int

    @fr.include_view(client.app)
    class DocView(fr.AsyncRestView):
        prefix = "/docs"
        model = Doc
        schema = DocSchema

        async def prepare_update(self, obj, schema_obj):
            fields = await super().prepare_update(obj, schema_obj)
            fields["revision"] = obj.revision + 1
            return fields

    create_tables()

    doc = client.post("/docs/", json={"title": "v1", "revision": 0}).json()
    assert doc["revision"] == 0

    updated = client.patch(f"/docs/{doc['id']}", json={"title": "v2"}).json()
    assert updated["revision"] == 1  # bumped by prepare_update


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
            raise fr.Conflict("duplicate outbox entry")

    create_tables()

    client.post("/docs/", json={"title": "v1"}, assert_status_code=409)

    # The failed before_commit means the create was never committed.
    assert client.get("/docs/").json() == []
