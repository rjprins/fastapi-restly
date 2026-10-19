"""Tests for the ``write_action`` context manager (custom write actions).

``async with self.write_action(action, *, obj, data)`` runs the full request
bracket -- authorize -> snapshot -> (your inline body) -> save ->
before_action_commit -> commit -> after_action_commit. ``obj`` names the row
that the action is about: a row from the database, a new object that the
action creates, or None.
"""

import pytest
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr

from ._models import SharedBase
from .conftest import create_tables


def _publish_view(client, **attrs):
    class Doc(fr.IDMixin, SharedBase):
        title: Mapped[str]
        published: Mapped[bool] = mapped_column(default=False)

    class DocSchema(fr.IDSchema):
        title: str
        published: bool

    async def publish_endpoint(self, id: int):
        doc = await self.handle_get_one(id)  # scope + 404 + read-auth
        async with self.write_action("publish", obj=doc):
            doc.published = True
        return self.to_response(doc)

    async def boom_endpoint(self, id: int):
        doc = await self.handle_get_one(id)
        async with self.write_action("publish", obj=doc):
            doc.published = True
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

    async def after_action_commit(self, action, new, old=None):
        events["action"] = action
        events["old"] = old["published"] if old else None
        events["new"] = new.published if new is not None else None

    _publish_view(client, after_action_commit=after_action_commit)

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
    after_action_commits: list[str] = []

    async def after_action_commit(self, action, new, old=None):
        after_action_commits.append(action)

    _publish_view(client, after_action_commit=after_action_commit)

    doc = client.post("/docs/", json={"title": "t", "published": False}).json()
    after_action_commits.clear()  # ignore the create's after_action_commit
    client.post(f"/docs/{doc['id']}/boom", assert_status_code=409)

    assert client.get(f"/docs/{doc['id']}").json()["published"] is False  # rolled back
    assert after_action_commits == []  # commit + after_action_commit never ran


def test_a_new_object_as_obj_is_created(client):
    seen: dict = {}

    class Item(fr.IDMixin, SharedBase):
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
            copy = Item(name=original.name + " (copy)")
            async with self.write_action("create", obj=copy):
                pass  # Restly adds the new object, and saves it
            return self.to_response(copy)

        async def authorize(self, action, obj=None, data=None):
            seen.setdefault(action, []).append(obj)

        async def after_action_commit(self, action, new, old=None):
            seen[f"{action} hook"] = (new.name, old)

    create_tables()

    a = client.post("/items/", json={"name": "x"}).json()
    seen.clear()
    cloned = client.post(f"/items/clone/{a['id']}").json()
    assert cloned["name"] == "x (copy)"
    assert cloned["id"] != a["id"]
    assert len(client.get("/items/").json()["data"]) == 2
    # authorize gets no row for a new object, as on POST /; there is no old
    assert seen["create"] == [None]
    assert seen["create hook"] == ("x (copy)", None)


def test_write_action_requires_obj(client):
    """``obj`` has no default: a block names its row, its new object, or None.
    Leaving it out fails before ``authorize``, and nothing is written."""

    class Thing(fr.IDMixin, SharedBase):
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
            async with self.write_action("create"):  # type: ignore[call-arg]
                self.session.add(Thing(name="ghost"))
                await self.session.flush()
            return {"never": "reached"}

    create_tables()

    with pytest.raises(TypeError, match="obj"):
        client.post("/things/sneaky")
    assert client.get("/things/").json()["data"] == []


def test_write_action_refuses_a_value_that_is_not_a_row(client):
    class Box(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class BoxSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class BoxView(fr.AsyncRestView):
        prefix = "/boxes"
        model = Box
        schema = BoxSchema

        @fr.post("/several")
        async def several(self):
            boxes = [Box(name="a"), Box(name="b")]
            async with self.write_action("pack", obj=boxes):
                pass
            return {}

    create_tables()
    with pytest.raises(TypeError, match="obj=None"):
        client.post("/boxes/several")


def test_explicit_no_object_write_is_allowed(client):
    """Passing ``obj=None`` explicitly is a no-object write: no guard fires and
    the commit hooks see ``new=None``."""
    seen: dict = {}

    class Widget(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class WidgetSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class WidgetView(fr.AsyncRestView):
        prefix = "/widgets"
        model = Widget
        schema = WidgetSchema

        async def after_action_commit(self, action, new, old=None):
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
