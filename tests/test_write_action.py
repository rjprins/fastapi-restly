"""Tests for the ``write_action`` context manager (custom write actions).

``async with self.write_action(action, *, obj, data)`` runs the full request
bracket -- authorize -> snapshot -> (your inline body) -> before_commit -> commit
-> after_commit. For an in-place action you ignore the yielded handle; for a
create-shaped action you deposit the new object on ``w.obj`` and read it back.
"""

import pytest
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr

from .conftest import create_tables


def _publish_view(client, **attrs):
    class Doc(fr.IDBase):
        title: Mapped[str]
        published: Mapped[bool] = mapped_column(default=False)

    class DocSchema(fr.IDSchema):
        title: str
        published: bool

    async def publish_endpoint(self, id: int):
        doc = await self.handle_get_one(id)  # scope + 404 + read-auth
        async with self.write_action("publish", obj=doc):
            doc.published = True
            await self.save_object(doc)
        return self.to_response(doc)

    async def boom_endpoint(self, id: int):
        doc = await self.handle_get_one(id)
        async with self.write_action("publish", obj=doc):
            doc.published = True
            await self.save_object(doc)
            raise fr.exc.Conflict("nope")  # fails inside the block
        return self.to_response(doc)

    namespace = {
        "prefix": "/docs",
        "model": Doc,
        "schema": DocSchema,
        "publish_endpoint": fr.post("/{id}/publish")(publish_endpoint),
        "boom_endpoint": fr.post("/{id}/boom")(boom_endpoint),
        **attrs,
    }
    view_cls = type("DocView", (fr.AsyncRestView,), namespace)
    fr.include_view(client.app)(view_cls)
    create_tables()
    return view_cls


def test_in_place_action_persists_and_brackets(client):
    events: dict = {}

    async def after_commit(self, action, new, old=None):
        events["action"] = action
        events["old"] = old["published"] if old else None
        events["new"] = new.published if new is not None else None

    _publish_view(client, after_commit=after_commit)

    doc = client.post("/docs/", json={"title": "t", "published": False}).json()
    client.post(f"/docs/{doc['id']}/publish")

    assert client.get(f"/docs/{doc['id']}").json()["published"] is True
    # snapshot captured the pre-mutation value; new is the mutated object.
    assert events == {"action": "publish", "old": False, "new": True}


def test_authorize_rejects_before_the_write(client):
    async def authorize(self, action, obj=None, data=None):
        if action == "publish":
            raise fr.exc.Forbidden()

    _publish_view(client, authorize=authorize)

    doc = client.post("/docs/", json={"title": "t", "published": False}).json()
    client.post(f"/docs/{doc['id']}/publish", assert_status_code=403)
    # authorize ran on entry, before the mutation and commit.
    assert client.get(f"/docs/{doc['id']}").json()["published"] is False


def test_failure_in_block_rolls_back_and_skips_hooks(client):
    after_commits: list[str] = []

    async def after_commit(self, action, new, old=None):
        after_commits.append(action)

    _publish_view(client, after_commit=after_commit)

    doc = client.post("/docs/", json={"title": "t", "published": False}).json()
    after_commits.clear()  # ignore the create's after_commit
    client.post(f"/docs/{doc['id']}/boom", assert_status_code=409)

    assert client.get(f"/docs/{doc['id']}").json()["published"] is False  # rolled back
    assert after_commits == []  # commit + after_commit never ran


def test_create_shaped_action_returns_via_handle(client):
    class Item(fr.IDBase):
        name: Mapped[str]

    class ItemSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.post("/clone/{id}")
        async def clone(self, id: int):
            original = await self.handle_get_one(id)
            async with self.write_action("create", data=None) as w:
                w.obj = await self.make_new_object(
                    ItemSchema.model_construct(name=original.name + " (copy)")
                )
            return self.to_response(w.obj)

    create_tables()

    a = client.post("/items/", json={"name": "x"}).json()
    cloned = client.post(f"/items/clone/{a['id']}").json()
    assert cloned["name"] == "x (copy)"
    assert len(client.get("/items/").json()) == 2


def test_create_shaped_action_without_deposit_raises_and_rolls_back(client):
    """The create-shaped footgun: a block with no ``obj=`` that builds a row but
    forgets to set ``w.obj`` raises ``RuntimeError`` on exit (instead of silently
    committing the row with the hooks blind to it), and the write rolls back."""

    class Thing(fr.IDBase):
        name: Mapped[str]

    class ThingSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class ThingView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing
        schema = ThingSchema

        @fr.post("/sneaky")
        async def sneaky(self):
            # create-shaped (no obj=): build a row but FORGET to deposit w.obj.
            async with self.write_action("create"):
                self.session.add(Thing(name="ghost"))
                await self.session.flush()
            return {"never": "reached"}

    create_tables()

    with pytest.raises(RuntimeError, match="create-shaped"):
        client.post("/things/sneaky")

    # The guard fired before commit, so the flushed row never persisted.
    assert client.get("/things/").json() == []


def test_explicit_no_object_write_is_allowed(client):
    """Passing ``obj=None`` explicitly is a no-object write: no guard fires and
    the commit hooks see ``new=None``."""
    seen: dict = {}

    class Widget(fr.IDBase):
        name: Mapped[str]

    class WidgetSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class WidgetView(fr.AsyncRestView):
        prefix = "/widgets"
        model = Widget
        schema = WidgetSchema

        async def after_commit(self, action, new, old=None):
            seen["action"] = action
            seen["new"] = new

        @fr.post("/recompute")
        async def recompute(self):
            # No single object -> pass obj=None explicitly; the guard stays quiet.
            async with self.write_action("recompute", obj=None):
                pass
            return {"ok": True}

    create_tables()

    assert client.post("/widgets/recompute").json() == {"ok": True}
    assert seen == {"action": "recompute", "new": None}
