"""A replaced endpoint method keeps the annotations its author wrote.

Registration fills the view's types (``id_type``, ``schema_create``,
``schema_update``, the response model) only into parameters and returns left
unannotated or annotated ``Any``. ``list_params`` is the exception: it always
takes the view's list params, because the unknown-key guard checks against
them.
"""

from types import SimpleNamespace
from typing import Annotated, Any
from uuid import UUID, uuid4

import fastapi
import pytest
from fastapi import FastAPI
from pydantic import Field
from sqlalchemy import Uuid
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly.testing._client import RestlyTestClient

from .conftest import create_tables

PositiveId = Annotated[int, fastapi.Path(ge=1)]
UnhashableId = Annotated[int, fastapi.Path(ge=1), {"note": "unhashable"}]


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

    class ProductRead(fr.IDSchema):
        name: str
        price: float

    return SimpleNamespace(
        client=client,
        asynchronous=asynchronous,
        base=fr.AsyncRestView if asynchronous else fr.RestView,
        react_admin_base=(
            fr.AsyncReactAdminView if asynchronous else fr.ReactAdminView
        ),
        Product=Product,
        ProductRead=ProductRead,
        make_tables=make_tables,
    )


def _register(env, view_cls):
    fr.include_view(env.client.app, view_cls)
    env.make_tables()
    return env.client.app.openapi()


def _body_ref(openapi, path, method):
    content = openapi["paths"][path][method]["requestBody"]["content"]
    return content["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]


def _response_ref(openapi, path, method, status="200"):
    content = openapi["paths"][path][method]["responses"][status]["content"]
    return content["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]


def _id_param(openapi, path, method):
    (param,) = [
        p for p in openapi["paths"][path][method]["parameters"] if p["name"] == "id"
    ]
    return param["schema"]


def test_replaced_update_endpoint_keeps_explicit_id_and_schema_obj(env):
    class ProductReplace(fr.BaseSchema):
        name: str
        price: float

    class ProductView(env.base):
        prefix = "/products"
        model = env.Product
        schema = env.ProductRead

        if env.asynchronous:

            @fr.patch("/{id}")
            async def update_endpoint(self, id: PositiveId, schema_obj: ProductReplace):
                return self.to_response(await self.handle_update(id, schema_obj))

        else:

            @fr.patch("/{id}")
            def update_endpoint(self, id: PositiveId, schema_obj: ProductReplace):
                return self.to_response(self.handle_update(id, schema_obj))

    openapi = _register(env, ProductView)
    client = env.client
    created = client.post("/products/", json={"name": "Lamp", "price": 1.0}).json()

    # schema_update would accept the partial body; ProductReplace does not
    client.patch(
        f"/products/{created['id']}", json={"name": "Desk"}, assert_status_code=422
    )
    client.patch(
        "/products/0", json={"name": "Desk", "price": 2.0}, assert_status_code=422
    )
    response = client.patch(
        f"/products/{created['id']}", json={"name": "Desk", "price": 2.0}
    )
    assert response.json() == {"id": created["id"], "name": "Desk", "price": 2.0}

    assert _body_ref(openapi, "/products/{id}", "patch") == "ProductReplace"
    assert _id_param(openapi, "/products/{id}", "patch")["minimum"] == 1


def test_replaced_create_endpoint_keeps_explicit_schema_obj(env):
    class ProductCreateStrict(fr.BaseSchema):
        name: str = Field(min_length=3)
        price: float

    class ProductView(env.base):
        prefix = "/products"
        model = env.Product
        schema = env.ProductRead

        if env.asynchronous:

            @fr.post("/", status_code=201)
            async def create_endpoint(self, schema_obj: ProductCreateStrict):
                return self.to_response(await self.handle_create(schema_obj))

        else:

            @fr.post("/", status_code=201)
            def create_endpoint(self, schema_obj: ProductCreateStrict):
                return self.to_response(self.handle_create(schema_obj))

    openapi = _register(env, ProductView)
    client = env.client

    client.post("/products/", json={"name": "ab", "price": 1.0}, assert_status_code=422)
    response = client.post("/products/", json={"name": "abc", "price": 1.0})
    assert response.json()["name"] == "abc"

    assert _body_ref(openapi, "/products", "post") == "ProductCreateStrict"


def test_replaced_get_one_endpoint_keeps_explicit_id(env):
    class ProductView(env.base):
        prefix = "/products"
        model = env.Product
        schema = env.ProductRead

        if env.asynchronous:

            @fr.get("/{id}")
            async def get_one_endpoint(self, id: PositiveId):
                return self.to_response(await self.handle_get_one(id))

        else:

            @fr.get("/{id}")
            def get_one_endpoint(self, id: PositiveId):
                return self.to_response(self.handle_get_one(id))

    openapi = _register(env, ProductView)
    client = env.client
    created = client.post("/products/", json={"name": "Lamp", "price": 1.0}).json()

    client.get("/products/0", assert_status_code=422)
    client.get("/products/999", assert_status_code=404)
    assert client.get(f"/products/{created['id']}").json()["name"] == "Lamp"

    assert _id_param(openapi, "/products/{id}", "get")["minimum"] == 1


def test_explicit_annotation_with_unhashable_metadata_is_kept(env):
    """``Annotated`` metadata may be unhashable, so judging the annotation
    must not hash it."""

    class ProductView(env.base):
        prefix = "/products"
        model = env.Product
        schema = env.ProductRead

        if env.asynchronous:

            @fr.get("/{id}")
            async def get_one_endpoint(self, id: UnhashableId):
                return self.to_response(await self.handle_get_one(id))

        else:

            @fr.get("/{id}")
            def get_one_endpoint(self, id: UnhashableId):
                return self.to_response(self.handle_get_one(id))

    openapi = _register(env, ProductView)

    env.client.get("/products/0", assert_status_code=422)
    assert _id_param(openapi, "/products/{id}", "get")["minimum"] == 1


def test_replaced_delete_endpoint_keeps_explicit_id(env):
    class ProductView(env.base):
        prefix = "/products"
        model = env.Product
        schema = env.ProductRead

        if env.asynchronous:

            @fr.delete("/{id}", status_code=204)
            async def delete_endpoint(self, id: PositiveId):
                await self.handle_delete(id)
                return fastapi.Response(status_code=204)

        else:

            @fr.delete("/{id}", status_code=204)
            def delete_endpoint(self, id: PositiveId):
                self.handle_delete(id)
                return fastapi.Response(status_code=204)

    openapi = _register(env, ProductView)
    client = env.client
    created = client.post("/products/", json={"name": "Lamp", "price": 1.0}).json()

    client.delete("/products/0", assert_status_code=422)
    client.delete(f"/products/{created['id']}")
    client.get(f"/products/{created['id']}", assert_status_code=404)

    assert _id_param(openapi, "/products/{id}", "delete")["minimum"] == 1


def test_replaced_endpoint_without_annotations_gets_the_view_types(env):
    """Unannotated and ``Any`` parameters and returns take the view's types."""

    class ProductView(env.base):
        prefix = "/products"
        model = env.Product
        schema = env.ProductRead

        if env.asynchronous:

            @fr.get("/{id}")
            async def get_one_endpoint(self, id: Any):
                return self.to_response(await self.handle_get_one(id))

            @fr.post("/", status_code=201)
            async def create_endpoint(self, schema_obj):
                return self.to_response(await self.handle_create(schema_obj))

            @fr.patch("/{id}")
            async def update_endpoint(self, id, schema_obj: Any) -> Any:
                return self.to_response(await self.handle_update(id, schema_obj))

        else:

            @fr.get("/{id}")
            def get_one_endpoint(self, id: Any):
                return self.to_response(self.handle_get_one(id))

            @fr.post("/", status_code=201)
            def create_endpoint(self, schema_obj):
                return self.to_response(self.handle_create(schema_obj))

            @fr.patch("/{id}")
            def update_endpoint(self, id, schema_obj: Any) -> Any:
                return self.to_response(self.handle_update(id, schema_obj))

    openapi = _register(env, ProductView)
    client = env.client

    # schema_create requires price; schema_update accepts a partial body
    client.post("/products/", json={"name": "Lamp"}, assert_status_code=422)
    created = client.post("/products/", json={"name": "Lamp", "price": 1.0}).json()
    response = client.patch(f"/products/{created['id']}", json={"name": "Desk"})
    assert response.json() == {"id": created["id"], "name": "Desk", "price": 1.0}
    client.get("/products/abc", assert_status_code=422)
    client.patch("/products/abc", json={"name": "Desk"}, assert_status_code=422)

    create_ref = _body_ref(openapi, "/products", "post")
    update_ref = _body_ref(openapi, "/products/{id}", "patch")
    schemas = openapi["components"]["schemas"]
    assert schemas[create_ref]["required"] == ["name", "price"]
    assert "required" not in schemas[update_ref]
    assert _id_param(openapi, "/products/{id}", "get")["type"] == "integer"
    assert _id_param(openapi, "/products/{id}", "patch")["type"] == "integer"
    for path, method, status in [
        ("/products/{id}", "get", "200"),
        ("/products", "post", "201"),
        ("/products/{id}", "patch", "200"),
    ]:
        assert _response_ref(openapi, path, method, status) == "ProductRead"


def test_replaced_endpoint_keeps_explicit_return_annotation(env):
    class ProductName(fr.BaseSchema):
        name: str

    class ProductView(env.base):
        prefix = "/products"
        model = env.Product
        schema = env.ProductRead

        if env.asynchronous:

            @fr.get("/{id}")
            async def get_one_endpoint(self, id: Any) -> ProductName:
                return self.to_response(await self.handle_get_one(id))

        else:

            @fr.get("/{id}")
            def get_one_endpoint(self, id: Any) -> ProductName:
                return self.to_response(self.handle_get_one(id))

    openapi = _register(env, ProductView)
    client = env.client
    created = client.post("/products/", json={"name": "Lamp", "price": 1.0}).json()

    assert client.get(f"/products/{created['id']}").json() == {"name": "Lamp"}
    assert _response_ref(openapi, "/products/{id}", "get") == "ProductName"


def test_subclass_of_registered_view_fills_its_own_types(env):
    """A subclass's copies of the parent's endpoints take the subclass's types.

    Registering the parent fills its endpoint signatures, and the copies made
    for the subclass carry those signatures along. The subclass's own schemas
    and ``id_type`` must still win.
    """

    class ProductView(env.base):
        prefix = "/products"
        model = env.Product
        schema = env.ProductRead

    fr.include_view(FastAPI(), ProductView)

    class Gadget(fr.DataclassBase):
        id: Mapped[UUID] = mapped_column(
            Uuid, primary_key=True, default_factory=uuid4, init=False
        )
        name: Mapped[str]
        color: Mapped[str]

    class GadgetRead(fr.BaseSchema):
        id: fr.ReadOnly[UUID]
        name: str
        color: str

    class GadgetView(ProductView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetRead
        id_type = UUID

    openapi = _register(env, GadgetView)
    client = env.client

    # ProductRead's create schema would accept this body
    client.post(
        "/products/gadgets/",
        json={"name": "Lamp", "price": 1.0},
        assert_status_code=422,
    )
    created = client.post(
        "/products/gadgets/", json={"name": "Lamp", "color": "red"}
    ).json()
    assert created["color"] == "red"
    assert client.get(f"/products/gadgets/{created['id']}").json() == created
    response = client.patch(
        f"/products/gadgets/{created['id']}", json={"color": "blue"}
    )
    assert response.json()["color"] == "blue"

    assert _id_param(openapi, "/products/gadgets/{id}", "get")["format"] == "uuid"
    assert _response_ref(openapi, "/products/gadgets/{id}", "get") == "GadgetRead"


def test_replaced_react_admin_put_keeps_explicit_schema_obj(env):
    class ProductReplace(fr.BaseSchema):
        name: str
        price: float

    class ProductView(env.react_admin_base):
        prefix = "/products"
        model = env.Product
        schema = env.ProductRead

        if env.asynchronous:

            @fr.put("/{id}")
            async def put(self, id: int, schema_obj: ProductReplace):
                return self.to_response(await self.handle_update(id, schema_obj))

        else:

            @fr.put("/{id}")
            def put(self, id: int, schema_obj: ProductReplace):
                return self.to_response(self.handle_update(id, schema_obj))

    openapi = _register(env, ProductView)
    client = env.client
    created = client.post("/products/", json={"name": "Lamp", "price": 1.0}).json()

    client.put(
        f"/products/{created['id']}", json={"name": "Desk"}, assert_status_code=422
    )
    response = client.put(
        f"/products/{created['id']}", json={"name": "Desk", "price": 2.0}
    )
    assert response.json()["price"] == 2.0

    assert _body_ref(openapi, "/products/{id}", "put") == "ProductReplace"


def test_default_react_admin_put_takes_schema_update(env):
    class ProductView(env.react_admin_base):
        prefix = "/products"
        model = env.Product
        schema = env.ProductRead

    openapi = _register(env, ProductView)
    client = env.client
    created = client.post("/products/", json={"name": "Lamp", "price": 1.0}).json()

    response = client.put(f"/products/{created['id']}", json={"name": "Desk"})
    assert response.json() == {"id": created["id"], "name": "Desk", "price": 1.0}

    assert _body_ref(openapi, "/products/{id}", "put") == _body_ref(
        openapi, "/products/{id}", "patch"
    )


def test_explicit_list_params_annotation_takes_the_view_list_params(env):
    """The unknown-key guard checks against ``schema_list_params``, so the
    parameter takes them whatever its author annotated."""

    class ProductView(env.base):
        prefix = "/products"
        model = env.Product
        schema = env.ProductRead

        if env.asynchronous:

            @fr.get("/search")
            async def search(self, list_params: dict[str, str]):
                result = await self.handle_get_many(list_params)
                return self.to_response(result, fr.ResponseShape.LIST)

        else:

            @fr.get("/search")
            def search(self, list_params: dict[str, str]):
                result = self.handle_get_many(list_params)
                return self.to_response(result, fr.ResponseShape.LIST)

    openapi = _register(env, ProductView)
    client = env.client
    client.post("/products/", json={"name": "Lamp", "price": 1.0})
    client.post("/products/", json={"name": "Desk", "price": 2.0})

    response = client.get("/products/search", params={"name": "Desk"})
    assert [row["name"] for row in response.json()["data"]] == ["Desk"]
    client.get("/products/search", params={"colour": "red"}, assert_status_code=422)

    operation = openapi["paths"]["/products/search"]["get"]
    assert "requestBody" not in operation
    assert "name" in {param["name"] for param in operation["parameters"]}
