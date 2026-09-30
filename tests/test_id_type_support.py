from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Response
from pydantic import BaseModel
from sqlalchemy import ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.testing._client import RestlyTestClient

from .conftest import create_tables


class UUIDModel(fr.DataclassBase):
    __tablename__ = "uuid_model"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid4)
    name: Mapped[str]


class UUIDSchema(BaseModel):
    id: UUID
    name: str


class UUIDView(fr.AsyncRestView):
    prefix = "/uuid-models"
    model = UUIDModel
    schema = UUIDSchema
    id_type = UUID

    async def perform_get(self, id: UUID):
        return SimpleNamespace(id=id, name="demo")

    async def perform_update(self, id: UUID, schema_obj: BaseModel):
        return SimpleNamespace(id=id, name=getattr(schema_obj, "name", "demo"))

    async def perform_delete(self, id: UUID):
        return Response(status_code=204)


def test_view_id_type_controls_openapi_path_parameter():
    app = FastAPI()
    fr.include_view(app, UUIDView)

    parameter_schema = app.openapi()["paths"]["/uuid-models/{id}"]["get"]["parameters"][
        0
    ]["schema"]
    assert parameter_schema["type"] == "string"
    assert parameter_schema["format"] == "uuid"


def test_idschema_accepts_uuid_relation_ids(client):
    class Author(fr.DataclassBase):
        __tablename__ = "uuid_author"

        id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid4)
        name: Mapped[str]

    class Article(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("uuid_author.id"))
        author: Mapped[Author] = relationship()

    class AuthorRead(fr.BaseSchema):
        id: fr.ReadOnly[UUID]
        name: str

    class ArticleRead(fr.IDSchema):
        title: str
        author_id: fr.IDSchema[Author]

    @fr.include_view(client.app)
    class AuthorView(fr.AsyncRestView):
        prefix = "/uuid-authors"
        model = Author
        schema = AuthorRead
        id_type = UUID

    @fr.include_view(client.app)
    class ArticleView(fr.AsyncRestView):
        prefix = "/uuid-articles"
        model = Article
        schema = ArticleRead

    create_tables()

    author_response = client.post("/uuid-authors/", json={"name": "Alice"})
    author_id = author_response.json()["id"]

    article_response = client.post(
        "/uuid-articles/", json={"title": "Hello", "author_id": {"id": author_id}}
    )

    assert article_response.status_code == 201
    payload = article_response.json()
    assert payload["title"] == "Hello"
    assert payload["author_id"]["id"] == author_id


# ---------------------------------------------------------------------------
# Without id_type, the {id} parameter takes the primary key's type
# ---------------------------------------------------------------------------


@pytest.fixture(params=["sync", "async"])
def flavor(request):
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

    return SimpleNamespace(
        client=client,
        asynchronous=asynchronous,
        base=fr.AsyncRestView if asynchronous else fr.RestView,
        react_admin_base=(
            fr.AsyncReactAdminView if asynchronous else fr.ReactAdminView
        ),
        make_tables=make_tables,
    )


def _uuid_thing():
    class UuidThing(fr.DataclassBase):
        id: Mapped[UUID] = mapped_column(
            Uuid, primary_key=True, default_factory=uuid4, init=False
        )
        name: Mapped[str]

    class UuidThingRead(fr.BaseSchema):
        id: fr.ReadOnly[UUID]
        name: str

    return UuidThing, UuidThingRead


def _pk_case(kind):
    """(model, schema, create body, an id that fails validation, id schema)."""
    if kind == "int":

        class IntThing(fr.IDBase):
            name: Mapped[str]

        class IntThingRead(fr.IDSchema):
            name: str

        return IntThing, IntThingRead, {"name": "a"}, "abc", {"type": "integer"}
    if kind == "uuid":
        uuid_model, uuid_schema = _uuid_thing()
        return (
            uuid_model,
            uuid_schema,
            {"name": "a"},
            "not-a-uuid",
            {"type": "string", "format": "uuid"},
        )

    class StrThing(fr.DataclassBase):
        id: Mapped[str] = mapped_column(primary_key=True)
        name: Mapped[str]

    class StrThingRead(fr.BaseSchema):
        id: str
        name: str

    return StrThing, StrThingRead, {"id": "lamp", "name": "a"}, None, {"type": "string"}


def _id_schema(openapi, path, method):
    (param,) = [
        p for p in openapi["paths"][path][method]["parameters"] if p["name"] == "id"
    ]
    return {
        key: param["schema"][key]
        for key in ("type", "format")
        if key in param["schema"]
    }


@pytest.mark.parametrize("kind", ["int", "uuid", "str"])
def test_id_parameter_takes_the_primary_key_type(flavor, kind):
    thing_model, thing_schema, body, bad_id, expected = _pk_case(kind)

    class ThingView(flavor.base):
        prefix = "/things"
        model = thing_model
        schema = thing_schema

    fr.include_view(flavor.client.app, ThingView)
    flavor.make_tables()
    client = flavor.client

    created = client.post("/things/", json=body).json()
    thing_id = created["id"]
    assert client.get(f"/things/{thing_id}").json() == created
    assert client.patch(f"/things/{thing_id}", json={"name": "b"}).json()["name"] == "b"
    client.delete(f"/things/{thing_id}")
    client.get(f"/things/{thing_id}", assert_status_code=404)
    if bad_id is not None:
        client.get(f"/things/{bad_id}", assert_status_code=404)

    openapi = client.app.openapi()
    for method in ("get", "patch", "delete"):
        assert _id_schema(openapi, "/things/{id}", method) == expected


def test_id_parameter_follows_a_primary_key_with_another_name(flavor):
    class Sku(fr.DataclassBase):
        code: Mapped[str] = mapped_column(primary_key=True)
        name: Mapped[str]

    class SkuRead(fr.BaseSchema):
        code: str
        name: str

    class SkuView(flavor.base):
        prefix = "/skus"
        model = Sku
        schema = SkuRead

    fr.include_view(flavor.client.app, SkuView)
    flavor.make_tables()
    client = flavor.client

    client.post("/skus/", json={"code": "lamp-1", "name": "Lamp"})
    assert client.get("/skus/lamp-1").json() == {"code": "lamp-1", "name": "Lamp"}
    assert _id_schema(client.app.openapi(), "/skus/{id}", "get") == {"type": "string"}


def test_react_admin_put_takes_the_primary_key_type(flavor):
    Thing, ThingRead = _uuid_thing()

    class ThingView(flavor.react_admin_base):
        prefix = "/things"
        model = Thing
        schema = ThingRead

    fr.include_view(flavor.client.app, ThingView)
    flavor.make_tables()
    client = flavor.client

    created = client.post("/things/", json={"name": "a"}).json()
    response = client.put(f"/things/{created['id']}", json={"name": "b"})
    assert response.json() == {"id": created["id"], "name": "b"}
    client.put("/things/not-a-uuid", json={"name": "b"}, assert_status_code=404)
    assert _id_schema(client.app.openapi(), "/things/{id}", "put")["format"] == "uuid"


def test_replaced_endpoint_on_a_uuid_model_accepts_a_uuid(flavor):
    """The reported case: a replaced endpoint annotated ``id: UUID``."""
    Thing, ThingRead = _uuid_thing()

    class ThingView(flavor.base):
        prefix = "/things"
        model = Thing
        schema = ThingRead

        if flavor.asynchronous:

            @fr.get("/{id}")
            async def get_one_endpoint(self, id: UUID):
                return self.to_response(await self.handle_get_one(id))

        else:

            @fr.get("/{id}")
            def get_one_endpoint(self, id: UUID):
                return self.to_response(self.handle_get_one(id))

    fr.include_view(flavor.client.app, ThingView)
    flavor.make_tables()
    client = flavor.client

    created = client.post("/things/", json={"name": "a"}).json()
    assert client.get(f"/things/{created['id']}").json() == created


def test_explicit_id_type_overrides_the_primary_key_type(flavor):
    thing_model, thing_schema, *_ = _pk_case("str")

    class ThingView(flavor.base):
        prefix = "/things"
        model = thing_model
        schema = thing_schema
        id_type = int

    fr.include_view(flavor.client.app, ThingView)
    flavor.make_tables()
    client = flavor.client

    client.get("/things/abc", assert_status_code=404)
    assert _id_schema(client.app.openapi(), "/things/{id}", "get") == {
        "type": "integer"
    }


def test_subclass_of_registered_view_derives_its_own_id_type(flavor):
    """The derived type is not stored on the class, so a subclass with a
    UUID model does not inherit its registered parent's ``int``."""
    thing_model, thing_schema, *_ = _pk_case("int")

    class ThingView(flavor.base):
        prefix = "/things"
        model = thing_model
        schema = thing_schema

    fr.include_view(FastAPI(), ThingView)

    Gadget, GadgetRead = _uuid_thing()

    class GadgetView(ThingView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetRead

    fr.include_view(flavor.client.app, GadgetView)
    flavor.make_tables()
    client = flavor.client

    created = client.post("/things/gadgets/", json={"name": "a"}).json()
    assert client.get(f"/things/gadgets/{created['id']}").json() == created
    openapi = client.app.openapi()
    assert _id_schema(openapi, "/things/gadgets/{id}", "get")["format"] == "uuid"


@pytest.mark.parametrize("base", [fr.RestView, fr.AsyncRestView])
def test_composite_primary_key_keeps_the_int_id(base):
    class Pair(fr.DataclassBase):
        code: Mapped[str] = mapped_column(primary_key=True)
        n: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str]

    class PairRead(fr.BaseSchema):
        code: str
        n: int
        name: str

    class PairView(base):
        prefix = "/pairs"
        model = Pair
        schema = PairRead

    app = FastAPI()
    fr.include_view(app, PairView)

    assert _id_schema(app.openapi(), "/pairs/{id}", "get") == {"type": "integer"}


@pytest.mark.parametrize("kind", ["int", "uuid"])
@pytest.mark.parametrize("react_admin", [False, True], ids=["rest", "react-admin"])
def test_typed_item_routes_allow_later_static_routes(flavor, kind, react_admin):
    thing_model, thing_schema, _, bad_id, expected = _pk_case(kind)
    base = flavor.react_admin_base if react_admin else flavor.base

    class ThingView(base):
        prefix = "/things"
        model = thing_model
        schema = thing_schema

    app = flavor.client.app
    fr.include_view(app, ThingView)
    methods = ["GET", "PATCH", "DELETE"] + (["PUT"] if react_admin else [])

    @app.api_route("/things/me", methods=methods, include_in_schema=False)
    def me():
        return {"name": "current user"}

    for method in methods:
        response = flavor.client.request(method, "/things/me")
        assert response.status_code == 200
        assert response.json() == {"name": "current user"}
        assert flavor.client.request(method, f"/things/{bad_id}").status_code == 404
        assert _id_schema(app.openapi(), "/things/{id}", method.lower()) == expected
