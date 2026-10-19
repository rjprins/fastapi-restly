"""The bare verbs (get_many/get_one/create/update/delete) are reserved names.

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
