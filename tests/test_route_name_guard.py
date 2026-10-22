"""The bare verbs (get_many/get_one/create/update/delete) are reserved names
on a CRUD view. A plain ``View`` has no such methods, so it may use them.

A ``@route``-decorated method named like a bare write verb shadows the verb
(which the ``handle_<verb>`` handlers call) and collides with the
``<verb>_endpoint`` route shell at the same path -- the duplicate-route +
recursion footgun. Such a definition is rejected at registration. Overriding a
write verb *without* a decorator (the intended domain-logic path) is
unaffected. ``get_one`` and ``get_many`` are final, so a class that defines
one fails at class definition.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

from ._models import SharedBase


def test_route_named_like_write_verb_is_rejected():
    class Item(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ItemSchema(fr.IDSchema):
        name: str

    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.post("/")
        async def create(self, schema_obj):  # shadows the bare verb
            return schema_obj

    app = FastAPI()
    with pytest.raises(TypeError, match="business method 'create'"):
        fr.include_view(app, ItemView)


def test_route_named_like_read_verb_is_rejected():
    """``get_many`` is final, so the class definition already fails."""

    class Item(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ItemSchema(fr.IDSchema):
        name: str

    with pytest.raises(
        fr.exc.RestlyConfigurationError, match="get_many, which is final"
    ):

        class ItemView(fr.AsyncRestView):
            prefix = "/items"
            model = Item
            schema = ItemSchema

            @fr.get("/")
            async def get_many(self):  # shadows the bare read verb
                return []


def test_bare_verb_override_without_route_is_allowed(sync_db):
    """The intended override path: redefine the bare verb (no decorator) for
    domain logic. This must still register and work."""
    engine, _ = sync_db

    class Item(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ItemSchema(fr.IDSchema):
        name: str

    class ItemView(fr.RestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        def create(self, schema_obj):  # no @route -> domain override, allowed
            obj = super().create(schema_obj)
            obj.name = obj.name.upper()
            return obj

    app = FastAPI()
    fr.include_view(app, ItemView)  # no raise

    SharedBase.metadata.create_all(engine)
    client = TestClient(app)
    created = client.post("/items/", json={"name": "abc"})
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "ABC"  # the override ran


def test_a_plain_view_may_name_its_routes_like_the_verbs():
    class SettingView(fr.View):
        prefix = "/settings"

        @fr.post("/")
        def create(self) -> dict:
            return {"made": True}

        @fr.get("/{key}")
        def get_one(self, key: str) -> dict:
            return {"key": key}

        @fr.put("/{key}")
        def update(self, key: str) -> dict:
            return {"updated": key}

        @fr.delete("/{key}")
        def delete(self, key: str) -> None:
            return None

    app = FastAPI()
    fr.include_view(app, SettingView)

    client = TestClient(app)
    assert client.post("/settings/").json() == {"made": True}
    assert client.get("/settings/a").json() == {"key": "a"}
    assert client.put("/settings/a").json() == {"updated": "a"}
    assert client.delete("/settings/a").status_code == 204


def test_a_route_named_like_a_view_setting_is_refused():
    """A route method is a class attribute, so it would replace the setting."""
    with pytest.raises(
        fr.exc.RestlyConfigurationError,
        match=r"TagView\.tags\(\) is a route method with the name of the view "
        r"setting View\.tags",
    ):

        class TagView(fr.View):
            prefix = "/tags"

            @fr.get("")
            def tags(self) -> list[str]:
                return []


def test_a_route_named_like_a_crud_setting_is_refused_first():
    class Item(fr.IDMixin, SharedBase):
        name: Mapped[str]

    with pytest.raises(
        fr.exc.RestlyConfigurationError,
        match=r"setting BaseRestView\.pagination, so it replaces the setting",
    ):

        class ItemView(fr.AsyncRestView):
            prefix = "/items"
            model = Item

            @fr.get("/pagination")
            async def pagination(self) -> dict:
                return {}


def test_a_plain_view_may_name_a_route_like_a_crud_setting():
    class ModelView(fr.View):
        prefix = "/models"

        @fr.get("")
        def model(self) -> dict:
            return {"model": "x"}

    app = FastAPI()
    fr.include_view(app, ModelView)
    assert TestClient(app).get("/models").json() == {"model": "x"}
