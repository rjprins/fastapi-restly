"""Wrapped routes resolve postponed annotations in their author's module."""

from __future__ import annotations

from enum import Enum
from types import SimpleNamespace
from typing import Annotated

import pytest
from fastapi import FastAPI, Path
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly.testing._client import RestlyTestClient

from .conftest import create_tables

PositiveId = Annotated[int, Path(ge=1)]


class ProductReplace(fr.BaseSchema):
    name: str
    price: float


class ProductRead(fr.IDSchema):
    name: str
    price: float


class Mode(str, Enum):
    fast = "fast"
    slow = "slow"


class SearchResult(fr.BaseSchema):
    mode: Mode
    names: list[str]


@pytest.fixture(params=["sync", "async"])
def env(request):
    asynchronous = request.param == "async"
    if asynchronous:
        client = request.getfixturevalue("client")

        def make_tables():
            create_tables()

    else:
        engine, _ = request.getfixturevalue("sync_db")
        client = RestlyTestClient(FastAPI())

        def make_tables():
            fr.DataclassBase.metadata.create_all(engine)

    class Product(fr.IDBase):
        name: Mapped[str]
        price: Mapped[float]

    return SimpleNamespace(
        client=client,
        asynchronous=asynchronous,
        base=fr.AsyncRestView if asynchronous else fr.RestView,
        Product=Product,
        make_tables=make_tables,
    )


def test_inherited_replaced_endpoint_resolves_postponed_annotations(env):
    class ReplaceUpdateMixin:
        if env.asynchronous:

            @fr.patch("/{id}")
            async def update_endpoint(
                self, id: PositiveId, schema_obj: ProductReplace
            ) -> ProductRead:
                return self.to_response(await self.handle_update(id, schema_obj))

        else:

            @fr.patch("/{id}")
            def update_endpoint(
                self, id: PositiveId, schema_obj: ProductReplace
            ) -> ProductRead:
                return self.to_response(self.handle_update(id, schema_obj))

    class ProductView(ReplaceUpdateMixin, env.base):
        prefix = "/products"
        model = env.Product
        schema = ProductRead

    fr.include_view(env.client.app, ProductView)
    env.make_tables()
    client = env.client
    created = client.post("/products/", json={"name": "Lamp", "price": 1.0}).json()
    path = f"/products/{created['id']}"

    client.patch(path, json={"name": "Desk"}, assert_status_code=422)
    client.patch(
        "/products/0", json={"name": "Desk", "price": 2.0}, assert_status_code=422
    )
    assert client.patch(path, json={"name": "Desk", "price": 2.0}).json() == {
        "id": created["id"],
        "name": "Desk",
        "price": 2.0,
    }

    operation = client.app.openapi()["paths"]["/products/{id}"]["patch"]
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ProductReplace"
    }
    assert operation["parameters"][0]["schema"]["minimum"] == 1
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ProductRead"
    }


def test_inherited_custom_route_resolves_postponed_annotations(env):
    class ImportMixin:
        if env.asynchronous:

            @fr.post("/import", status_code=201)
            async def import_product(self, payload: ProductReplace) -> ProductRead:
                """Import one product."""
                return self.to_response(await self.handle_create(payload))

        else:

            @fr.post("/import", status_code=201)
            def import_product(self, payload: ProductReplace) -> ProductRead:
                """Import one product."""
                return self.to_response(self.handle_create(payload))

    class ProductView(ImportMixin, env.base):
        prefix = "/products"
        model = env.Product
        schema = ProductRead

    fr.include_view(env.client.app, ProductView)
    env.make_tables()
    client = env.client
    client.post("/products/import", json={"name": "Lamp"}, assert_status_code=422)
    created = client.post(
        "/products/import", json={"name": "Lamp", "price": 1.0}
    ).json()
    assert client.get(f"/products/{created['id']}").json() == created

    operation = client.app.openapi()["paths"]["/products/import"]["post"]
    assert operation["description"] == "Import one product."
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ProductReplace"
    }
    assert operation["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ProductRead"
    }


@pytest.mark.parametrize("inherited", [False, True], ids=["direct", "inherited"])
def test_guarded_listing_resolves_postponed_annotations(env, inherited):
    class SearchView(env.base):
        prefix = "/products"
        model = env.Product
        schema = ProductRead

        if env.asynchronous:

            @fr.get("/search/{mode}")
            async def search(self, query_params, mode: Mode) -> SearchResult:
                result = await self.handle_get_many(query_params)
                return SearchResult(
                    mode=mode, names=[obj.name for obj in result.objects]
                )

        else:

            @fr.get("/search/{mode}")
            def search(self, query_params, mode: Mode) -> SearchResult:
                result = self.handle_get_many(query_params)
                return SearchResult(
                    mode=mode, names=[obj.name for obj in result.objects]
                )

    fr.include_view(env.client.app, SearchView)
    # A registered parent's guarded route is copied onto its child. Registering
    # the child twice also exercises the prepared-class path on a second app.
    if inherited:

        class ChildView(SearchView):
            prefix = "/child-products"

        fr.include_view(env.client.app, ChildView)
        second_app = FastAPI()
        fr.include_view(second_app, ChildView)
        prefix = "/products/child-products"
        assert f"{prefix}/search/{{mode}}" in second_app.openapi()["paths"]
    else:
        prefix = SearchView.prefix

    env.make_tables()
    client = env.client
    client.post(f"{prefix}/", json={"name": "Lamp", "price": 1.0})
    client.post(f"{prefix}/", json={"name": "Desk", "price": 2.0})

    assert client.get(f"{prefix}/search/fast", params={"name": "Desk"}).json() == {
        "mode": "fast",
        "names": ["Desk"],
    }
    client.get(f"{prefix}/search/invalid", assert_status_code=422)
    response = client.get(
        f"{prefix}/search/fast", params={"unknown": "x"}, assert_status_code=422
    )
    assert response.json()["detail"][0]["loc"] == ["query", "unknown"]

    operation = client.app.openapi()["paths"][f"{prefix}/search/{{mode}}"]["get"]
    parameters = {param["name"]: param for param in operation["parameters"]}
    assert parameters["mode"]["schema"]["$ref"] == "#/components/schemas/Mode"
    assert "name" in parameters
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SearchResult"
    }
