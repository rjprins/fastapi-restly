"""Tests for the reified write lifecycle (`handle_write` / `run_write_action`).

`handle_write(action, *, obj, data, mutate)` runs the full request bracket --
authorize -> snapshot -> mutate -> before_commit -> commit -> after_commit -- and
the CRUD handlers delegate to it. These tests pin: a custom action gets the whole
bracket (and persists), authorize rejects *before* the write, and overriding
`handle_write` propagates to the built-in create/update/delete.
"""

from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr

from .conftest import create_tables


def _publish_view(app, **attrs):
    class Doc(fr.IDBase):
        title: Mapped[str]
        published: Mapped[bool] = mapped_column(default=False)

    class DocSchema(fr.IDSchema):
        title: str
        published: bool

    namespace = {
        "prefix": "/docs",
        "model": Doc,
        "schema": DocSchema,
        **attrs,
    }

    async def publish_endpoint(self, id: int):
        doc = await self.handle_get_one(id)  # scope + 404 + read-auth

        async def _publish():
            doc.published = True
            return await self.save_object(doc)

        result = await self.handle_write("publish", obj=doc, mutate=_publish)
        return self.to_response(result, "update")

    namespace["publish_endpoint"] = fr.post("/{id}/publish")(publish_endpoint)
    view_cls = type("DocView", (fr.AsyncRestView,), namespace)
    fr.include_view(app)(view_cls)
    create_tables()
    return view_cls


def test_custom_action_via_handle_write_persists_and_brackets(client):
    events: dict = {}

    async def after_commit(self, action, new, old=None):
        events["action"] = action
        events["old_published"] = old["published"] if old else None
        events["new_published"] = new.published if new is not None else None

    _publish_view(client.app, after_commit=after_commit)

    doc = client.post("/docs/", json={"title": "t", "published": False}).json()
    client.post(f"/docs/{doc['id']}/publish")

    # The mutation was committed.
    assert client.get(f"/docs/{doc['id']}").json()["published"] is True
    # The bracket fired with the custom action name and a pre-mutation snapshot.
    assert events["action"] == "publish"
    assert events["old_published"] is False
    assert events["new_published"] is True


def test_handle_write_authorize_rejects_before_the_write(client):
    async def authorize(self, action, obj=None, data=None):
        if action == "publish":
            raise fr.Forbidden()

    _publish_view(client.app, authorize=authorize)

    doc = client.post("/docs/", json={"title": "t", "published": False}).json()
    client.post(f"/docs/{doc['id']}/publish", assert_status_code=403)

    # authorize ran before mutate + commit, so nothing changed.
    assert client.get(f"/docs/{doc['id']}").json()["published"] is False


def test_overriding_handle_write_wraps_every_builtin_write(client):
    """The CRUD handlers delegate to ``handle_write``, so an override sees every
    create/update/delete and the writes still persist."""
    seen: list[str] = []

    class Thing(fr.IDBase):
        name: Mapped[str]

    class ThingSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class ThingView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing
        schema = ThingSchema

        async def handle_write(self, action, **kwargs):
            seen.append(action)
            return await super().handle_write(action, **kwargs)

    create_tables()

    created = client.post("/things/", json={"name": "a"}).json()
    client.patch(f"/things/{created['id']}", json={"name": "b"})
    client.delete(f"/things/{created['id']}")

    # Every built-in write routed through the override...
    assert seen == ["create", "update", "delete"]
    # ...and still took effect (delete persisted).
    assert client.get("/things/").json() == []
