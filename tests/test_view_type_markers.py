"""The markers of ``fastapi_restly.views`` stand for the view's own types.

A route on a mixin names them, and each view that uses the mixin gets its own
classes when it registers.
"""

from types import SimpleNamespace
from typing import Annotated, Generic, TypeVar
from uuid import UUID, uuid4

import pydantic
import pytest
from fastapi import FastAPI, Path
from fastapi.exceptions import ResponseValidationError
from fastapi.routing import APIRoute
from sqlalchemy import Uuid
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly._routes import iter_routes, original_route
from fastapi_restly.exc import RestlyConfigurationError
from fastapi_restly.testing._client import RestlyTestClient
from fastapi_restly.views import (
    ViewCreate,
    ViewID,
    ViewListResponse,
    ViewResponse,
    ViewUpdate,
)

from ._models import SharedBase
from .conftest import create_tables

T = TypeVar("T")


class Data(pydantic.BaseModel, Generic[T]):
    """A wrapper around one object, for the tests of a marker in a generic class."""

    data: T


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
            SharedBase.metadata.create_all(engine)

    class Product(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Gadget(SharedBase):
        id: Mapped[UUID] = mapped_column(
            Uuid, primary_key=True, default_factory=uuid4, init=False
        )
        name: Mapped[str]

    return SimpleNamespace(
        client=client,
        asynchronous=asynchronous,
        base=fr.AsyncRestView if asynchronous else fr.RestView,
        Product=Product,
        Gadget=Gadget,
        make_tables=make_tables,
    )


def _route_paths(app):
    return {
        route.path
        for route in iter_routes(app.routes)
        if isinstance(original_route(route), APIRoute)
    }


def _ref(schema):
    return schema["$ref"].rsplit("/", 1)[-1]


def _rename_mixin(env):
    class RenameMixin:
        if env.asynchronous:

            @fr.post("/{id}/rename", status_code=200)
            async def rename(self, id: ViewID, schema_obj: ViewUpdate) -> ViewResponse:
                return await self.handle_update(id, schema_obj)

        else:

            @fr.post("/{id}/rename", status_code=200)
            def rename(self, id: ViewID, schema_obj: ViewUpdate) -> ViewResponse:
                return self.handle_update(id, schema_obj)

    return RenameMixin


def test_a_mixin_route_gets_the_types_of_each_view(env):
    RenameMixin = _rename_mixin(env)

    class ProductView(RenameMixin, env.base):
        prefix = "/products"
        model = env.Product

    class GadgetView(RenameMixin, env.base):
        prefix = "/gadgets"
        model = env.Gadget

    fr.include_view(env.client.app, ProductView)
    fr.include_view(env.client.app, GadgetView)
    env.make_tables()
    client = env.client

    for prefix in ("/products", "/gadgets"):
        created = client.post(f"{prefix}/", json={"name": "Lamp"}).json()
        response = client.post(
            f"{prefix}/{created['id']}/rename", json={"name": "Desk"}
        )
        assert response.json() == {"id": created["id"], "name": "Desk"}

    assert {"/products/{id:int}/rename", "/gadgets/{id:uuid}/rename"} <= (
        _route_paths(client.app)
    )
    paths = client.app.openapi()["paths"]
    for prefix, resource, id_schema in [
        ("/products", "Product", {"type": "integer"}),
        ("/gadgets", "Gadget", {"type": "string", "format": "uuid"}),
    ]:
        operation = paths[f"{prefix}/{{id}}/rename"]["post"]
        body = operation["requestBody"]["content"]["application/json"]["schema"]
        assert _ref(body) == f"{resource}Update"
        response = operation["responses"]["200"]["content"]["application/json"]
        assert _ref(response["schema"]) == f"{resource}Response"
        (id_param,) = operation["parameters"]
        assert id_schema.items() <= id_param["schema"].items()


def test_a_subclass_of_a_registered_view_gets_its_own_types(env):
    RenameMixin = _rename_mixin(env)

    class ProductView(RenameMixin, env.base):
        prefix = "/products"
        model = env.Product

    fr.include_view(env.client.app, ProductView)

    class GadgetView(ProductView):
        prefix = "/gadgets"
        model = env.Gadget

    fr.include_view(env.client.app, GadgetView)
    paths = _route_paths(env.client.app)
    assert {"/products/{id:int}/rename", "/products/gadgets/{id:uuid}/rename"} <= paths
    operation = env.client.app.openapi()["paths"]["/products/gadgets/{id}/rename"]
    body = operation["post"]["requestBody"]["content"]["application/json"]
    assert _ref(body["schema"]) == "GadgetUpdate"


def test_list_markers_follow_the_pagination_of_the_view(env):
    class NamedMixin:
        if env.asynchronous:

            @fr.get("/named/{name}")
            async def named(self, name: str, list_params) -> ViewListResponse:
                result = await self.handle_get_many(
                    list_params, where=self.model.name == name
                )
                return result

            @fr.get("/plain", response_model=list[ViewResponse])
            async def plain(self, list_params):
                result = await self.handle_get_many(list_params)
                return [self.to_single_response(obj) for obj in result.objects]

        else:

            @fr.get("/named/{name}")
            def named(self, name: str, list_params) -> ViewListResponse:
                result = self.handle_get_many(
                    list_params, where=self.model.name == name
                )
                return result

            @fr.get("/plain", response_model=list[ViewResponse])
            def plain(self, list_params):
                result = self.handle_get_many(list_params)
                return [self.to_single_response(obj) for obj in result.objects]

    class ProductView(NamedMixin, env.base):
        prefix = "/products"
        model = env.Product

    class GadgetView(NamedMixin, env.base):
        prefix = "/gadgets"
        model = env.Gadget
        pagination = None

    fr.include_view(env.client.app, ProductView)
    fr.include_view(env.client.app, GadgetView)
    env.make_tables()
    client = env.client
    for prefix in ("/products", "/gadgets"):
        client.post(f"{prefix}/", json={"name": "Lamp"})
        client.post(f"{prefix}/", json={"name": "Desk"})

    page = client.get("/products/named/Desk").json()
    assert [row["name"] for row in page["data"]] == ["Desk"]
    assert page["total_count"] == 1
    assert client.get("/gadgets/named/Desk").json().keys() == {"data"}
    assert [row["name"] for row in client.get("/products/plain").json()] == [
        "Lamp",
        "Desk",
    ]

    openapi = client.app.openapi()
    schemas = openapi["components"]["schemas"]
    for prefix, resource in [("/products", "Product"), ("/gadgets", "Gadget")]:
        named = openapi["paths"][f"{prefix}/named/{{name}}"]["get"]
        content = named["responses"]["200"]["content"]["application/json"]
        assert _ref(content["schema"]) == f"{resource}ListResponse"
        plain = openapi["paths"][f"{prefix}/plain"]["get"]
        content = plain["responses"]["200"]["content"]["application/json"]
        assert content["schema"]["type"] == "array"
        assert _ref(content["schema"]["items"]) == f"{resource}Response"
    assert "total_count" in schemas["ProductListResponse"]["properties"]
    assert schemas["GadgetListResponse"]["properties"].keys() == {"data"}


def test_markers_work_inside_other_types(env):
    class ProductView(env.base):
        prefix = "/products"
        model = env.Product

        if env.asynchronous:

            @fr.get("/maybe/{id}")
            async def maybe(
                self, id: Annotated[ViewID, Path(ge=1)]
            ) -> ViewResponse | None:
                return self.to_single_response(await self.handle_get_one(id))

        else:

            @fr.get("/maybe/{id}")
            def maybe(self, id: Annotated[ViewID, Path(ge=1)]) -> ViewResponse | None:
                return self.to_single_response(self.handle_get_one(id))

    fr.include_view(env.client.app, ProductView)
    env.make_tables()
    client = env.client
    created = client.post("/products/", json={"name": "Lamp"}).json()
    assert client.get(f"/products/maybe/{created['id']}").json() == created
    client.get("/products/maybe/0", assert_status_code=422)

    assert "/products/maybe/{id:int}" in _route_paths(client.app)
    operation = client.app.openapi()["paths"]["/products/maybe/{id}"]["get"]
    (id_param,) = operation["parameters"]
    assert id_param["schema"]["type"] == "integer"
    assert id_param["schema"]["minimum"] == 1
    content = operation["responses"]["200"]["content"]["application/json"]
    refs = [_ref(option) for option in content["schema"]["anyOf"] if "$ref" in option]
    assert refs == ["ProductResponse"]
    assert {"type": "null"} in content["schema"]["anyOf"]


def test_view_id_converter_with_another_name_or_a_given_converter(env):
    class ProductView(env.base):
        prefix = "/products"
        model = env.Product

        @fr.get("/by-item/{item_id}")
        def by_item(self, item_id: ViewID) -> dict[str, int]:
            return {"item_id": item_id}

        @fr.get("/by-text/{id:str}")
        def by_text(self, id: ViewID) -> dict[str, int]:
            return {"id": id}

    fr.include_view(env.client.app, ProductView)
    client = env.client
    paths = _route_paths(client.app)
    assert {"/products/by-item/{item_id:int}", "/products/by-text/{id:str}"} <= paths
    assert client.get("/products/by-item/5").json() == {"item_id": 5}
    client.get("/products/by-item/abc", assert_status_code=404)
    # the path's own converter stays, and the parameter still takes the id type
    client.get("/products/by-text/abc", assert_status_code=422)


def test_a_marker_on_a_plain_view_raises():
    class PingView(fr.View):
        prefix = "/ping"

        @fr.get("/")
        def ping(self) -> ViewResponse:
            return {}

    with pytest.raises(
        RestlyConfigurationError,
        match=r"PingView\.ping uses fastapi_restly\.views\.ViewResponse",
    ):
        fr.include_view(FastAPI(), PingView)


def _json_schema(operation, status="200"):
    return operation["responses"][status]["content"]["application/json"]["schema"]


def _generic_view(env):
    """A view with one route for each way to name a marker in a generic class."""

    def lamp(id):
        row = env.Product(name="Lamp")
        row.id = id
        return row

    class ProductView(env.base):
        prefix = "/products"
        model = env.Product

        @fr.get("/annotated/{id}")
        def annotated(self, id: ViewID) -> Data[ViewResponse]:
            return {"data": lamp(id)}

        @fr.get("/decorator/{id}", response_model=Data[ViewResponse])
        def decorator(self, id: ViewID):
            return {"data": lamp(id)}

        @fr.get("/string/{id}")
        def string(self, id: ViewID) -> "Data[ViewResponse]":
            return {"data": lamp(id)}

        @fr.get("/many")
        def many(self) -> Data[list[ViewResponse]]:
            return {"data": [lamp(1)]}

        @fr.get("/optional/{id}")
        def optional(self, id: ViewID) -> Data[ViewResponse] | None:
            return None if id == 0 else {"data": lamp(id)}

        @fr.get("/raw/{id}")
        def raw(self, id: ViewID) -> Data[ViewResponse]:
            return lamp(id)

        @fr.post("/echo", status_code=200)
        def echo(self, body: Data[ViewCreate]) -> Data[ViewCreate]:
            return body

    return ProductView


@pytest.mark.parametrize("route", ["annotated", "decorator", "string"])
def test_a_marker_in_a_generic_class_is_filled(env, route):
    fr.include_view(env.client.app, _generic_view(env))
    client = env.client

    assert client.get(f"/products/{route}/7").json() == {
        "data": {"id": 7, "name": "Lamp"}
    }
    spec = client.app.openapi()
    operation = spec["paths"][f"/products/{route}/{{id}}"]["get"]
    name = _ref(_json_schema(operation))
    assert name == "Data_ProductResponse_"
    assert _ref(spec["components"]["schemas"][name]["properties"]["data"]) == (
        "ProductResponse"
    )
    # the marker gets the view's converter also here, from the route's parameter
    assert f"/products/{route}/{{id:int}}" in _route_paths(client.app)


def test_a_marker_inside_a_type_inside_a_generic_class_is_filled(env):
    fr.include_view(env.client.app, _generic_view(env))
    client = env.client

    assert client.get("/products/many").json() == {"data": [{"id": 1, "name": "Lamp"}]}
    spec = client.app.openapi()
    name = _ref(_json_schema(spec["paths"]["/products/many"]["get"]))
    data = spec["components"]["schemas"][name]["properties"]["data"]
    assert data["type"] == "array"
    assert _ref(data["items"]) == "ProductResponse"


def test_a_generic_class_with_a_marker_can_be_optional(env):
    fr.include_view(env.client.app, _generic_view(env))
    client = env.client

    assert client.get("/products/optional/0").json() is None
    assert client.get("/products/optional/7").json() == {
        "data": {"id": 7, "name": "Lamp"}
    }
    operation = client.app.openapi()["paths"]["/products/optional/{id}"]["get"]
    options = _json_schema(operation)["anyOf"]
    assert {"type": "null"} in options
    assert [_ref(o) for o in options if "$ref" in o] == ["Data_ProductResponse_"]


def test_a_generic_class_with_a_marker_as_the_body(env):
    fr.include_view(env.client.app, _generic_view(env))
    client = env.client

    body = {"data": {"name": "Lamp"}}
    assert client.post("/products/echo", json=body).json() == body
    client.post("/products/echo", json={"data": {}}, assert_status_code=422)
    operation = client.app.openapi()["paths"]["/products/echo"]["post"]
    request_body = operation["requestBody"]["content"]["application/json"]["schema"]
    assert _ref(request_body) == "Data_ProductCreate_"


def test_a_route_that_returns_the_row_for_a_generic_class_fails_as_in_fastapi(env):
    # Restly does not wrap the row: only the route knows where it goes. FastAPI's
    # check of the response fails, so the row does not go out.
    fr.include_view(env.client.app, _generic_view(env))
    with pytest.raises(ResponseValidationError):
        env.client.get("/products/raw/7")


def test_each_view_gets_its_own_class_in_a_generic_class(env):
    class Base(env.base):
        @fr.get("/one/{id}")
        def one(self, id: ViewID) -> Data[ViewResponse]:
            return {"data": {"id": id, "name": "Lamp"}}

    class ProductView(Base):
        prefix = "/products"
        model = env.Product

    class GadgetView(Base):
        prefix = "/gadgets"
        model = env.Gadget

    fr.include_view(env.client.app, ProductView)
    fr.include_view(env.client.app, GadgetView)
    paths = env.client.app.openapi()["paths"]
    products = paths["/products/one/{id}"]["get"]
    gadgets = paths["/gadgets/one/{id}"]["get"]
    assert _ref(_json_schema(products)) == "Data_ProductResponse_"
    assert _ref(_json_schema(gadgets)) == "Data_GadgetResponse_"


def test_a_generic_class_without_a_marker_is_left_alone():
    class PingView(fr.View):
        prefix = "/ping"

        @fr.get("/")
        def ping(self) -> Data[int]:
            return {"data": 1}

    app = FastAPI()
    fr.include_view(app, PingView)
    assert RestlyTestClient(app).get("/ping/").json() == {"data": 1}


def test_a_marker_in_a_generic_class_on_a_plain_view_raises():
    class PingView(fr.View):
        prefix = "/ping"

        @fr.get("/")
        def ping(self) -> Data[ViewResponse]:
            return {}

    with pytest.raises(
        RestlyConfigurationError,
        match=r"PingView\.ping uses fastapi_restly\.views\.ViewResponse",
    ):
        fr.include_view(FastAPI(), PingView)
