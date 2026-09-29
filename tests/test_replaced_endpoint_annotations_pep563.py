"""Under PEP 563 an endpoint annotation is a string, and a string is explicit.

Registration keeps it instead of filling in the view's type; FastAPI resolves
it against the module the method was written in. Kept in its own
``from __future__ import annotations`` module, with the schemas at module
level so the string annotations resolve.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly.testing._client import RestlyTestClient

from .conftest import create_tables


class ProductRead(fr.IDSchema):
    name: str
    price: float


class ProductReplace(fr.BaseSchema):
    name: str
    price: float


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_string_annotation_on_replaced_endpoint_is_kept(request, asynchronous):
    if asynchronous:
        client = request.getfixturevalue("client")
        make_tables = create_tables
    else:
        engine, _ = request.getfixturevalue("sync_db")
        client = RestlyTestClient(FastAPI())

        def make_tables():
            fr.DataclassBase.metadata.create_all(engine)

    class Product(fr.IDBase):
        name: Mapped[str]
        price: Mapped[float]

    base: Any = fr.AsyncRestView if asynchronous else fr.RestView

    class ProductView(base):
        prefix = "/products"
        model = Product
        schema = ProductRead

        if asynchronous:

            @fr.patch("/{id}")
            async def update_endpoint(self, id: int, schema_obj: ProductReplace):
                return self.to_response(await self.handle_update(id, schema_obj))

        else:

            @fr.patch("/{id}")
            def update_endpoint(self, id: int, schema_obj: ProductReplace):
                return self.to_response(self.handle_update(id, schema_obj))

    assert ProductView.update_endpoint.__annotations__["schema_obj"] == "ProductReplace"
    fr.include_view(client.app, ProductView)
    make_tables()

    created = client.post("/products/", json={"name": "Lamp", "price": 1.0}).json()
    client.patch(
        f"/products/{created['id']}", json={"name": "Desk"}, assert_status_code=422
    )
    response = client.patch(
        f"/products/{created['id']}", json={"name": "Desk", "price": 2.0}
    )
    assert response.json() == {"id": created["id"], "name": "Desk", "price": 2.0}
