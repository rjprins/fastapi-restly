"""A SQLModel table class does not validate, so it cannot be a schema of a view.

A view without a schema, or with a class without ``table=True``, validates the
request body, and OpenAPI shows the required fields.
"""

import asyncio
from typing import Any

import fastapi
import pydantic
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError
from fastapi_restly.views import SchemaType

from ._openapi_spec import spec

sqlmodel = pytest.importorskip("sqlmodel")


class HeroBase(sqlmodel.SQLModel):
    name: str = sqlmodel.Field(max_length=5)
    age: int = sqlmodel.Field(default=3, ge=0)

    @pydantic.field_validator("name")
    @classmethod
    def _not_x(cls, value: str) -> str:
        if value == "x":
            raise ValueError("x is not a name")
        return value


class SchemaHero(HeroBase, table=True):
    id: int | None = sqlmodel.Field(default=None, primary_key=True)


class HeroSchema(HeroBase):
    id: fr.ReadOnly[int]


class GeneratedHero(sqlmodel.SQLModel, table=True):
    id: int | None = sqlmodel.Field(default=None, primary_key=True)
    name: str


class ChildOfTable(GeneratedHero):
    pass


def _sync_app() -> fastapi.FastAPI:
    engine = sqlmodel.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    sqlmodel.SQLModel.metadata.create_all(engine)

    def get_session():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    fr.configure(session_generator=get_session)
    return fastapi.FastAPI()


def _async_app() -> fastapi.FastAPI:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)

    async def create_tables():
        async with engine.begin() as connection:
            await connection.run_sync(sqlmodel.SQLModel.metadata.create_all)

    asyncio.run(create_tables())
    make_session = async_sessionmaker(engine, expire_on_commit=False)

    async def get_session():
        async with make_session() as session:
            yield session

    fr.configure(async_session_generator=get_session)
    return fastapi.FastAPI()


BOTH_KINDS = pytest.mark.parametrize(
    "view_base, make_app",
    [(fr.RestView, _sync_app), (fr.AsyncRestView, _async_app)],
    ids=["sync", "async"],
)


@pytest.mark.parametrize("view_base", [fr.RestView, fr.AsyncRestView])
@pytest.mark.parametrize("setting", ["schema", "create_schema", "update_schema"])
def test_a_table_class_as_a_schema_fails_when_the_view_registers(view_base, setting):
    view = type(
        "TableView",
        (view_base,),
        {"prefix": "/heroes", "model": GeneratedHero, setting: GeneratedHero},
    )

    with pytest.raises(RestlyConfigurationError) as info:
        fr.include_view(fastapi.FastAPI(), view)

    message = str(info.value)
    assert f"TableView.{setting} is GeneratedHero" in message
    assert "SQLModel table class" in message
    assert "without `table=True`" in message
    assert "fr.schemas.derive_schema(GeneratedHero)" in message


def test_a_subclass_of_a_table_class_fails_too():
    class ChildView(fr.RestView):
        prefix = "/heroes"
        model = GeneratedHero
        schema = ChildOfTable

    with pytest.raises(RestlyConfigurationError) as info:
        fr.include_view(fastapi.FastAPI(), ChildView)

    message = str(info.value)
    assert "ChildView.schema is ChildOfTable" in message
    assert "subclass of a SQLModel table class" in message
    # a subclass has no mapper, so derive_schema cannot take it
    assert "derive_schema" not in message


def test_a_subclass_with_table_false_fails_too():
    class NoTableChild(GeneratedHero, table=False):
        pass

    class NoTableView(fr.RestView):
        prefix = "/heroes"
        model = GeneratedHero
        create_schema = NoTableChild

    with pytest.raises(RestlyConfigurationError, match="subclass of a SQLModel"):
        fr.include_view(fastapi.FastAPI(), NoTableView)


@pytest.mark.parametrize("view_base", [fr.RestView, fr.AsyncRestView])
@pytest.mark.parametrize(
    "setting, value",
    [
        ("response_schema", ChildOfTable),
        ("list_response_schema", list[ChildOfTable]),
        ("list_response_schema", fr.views.NumberedEnvelope[ChildOfTable]),
    ],
    ids=["response_schema", "list", "envelope"],
)
def test_a_subclass_of_a_table_class_cannot_go_out(view_base, setting, value):
    view = type(
        "ChildOutView",
        (view_base,),
        {"prefix": "/heroes", "model": GeneratedHero, setting: value},
    )

    with pytest.raises(RestlyConfigurationError, match="ChildOfTable, a subclass"):
        fr.include_view(fastapi.FastAPI(), view)


def test_a_table_class_as_the_response_schema_still_works():
    app = _sync_app()

    @fr.include_view(app)
    class HeroOutView(fr.RestView):
        prefix = "/out-heroes"
        model = GeneratedHero
        response_schema = GeneratedHero

    client = TestClient(app)
    assert client.post("/out-heroes", json={"name": "Ann"}).status_code == 201
    assert any(row["name"] == "Ann" for row in client.get("/out-heroes").json()["data"])
    assert spec(app)["components"]["schemas"]["GeneratedHero"]["required"] == ["name"]


def test_a_schema_type_that_gives_a_table_class_fails_too():
    class FunctionView(fr.RestView):
        prefix = "/heroes"
        model = GeneratedHero
        create_schema = SchemaType(lambda view: GeneratedHero)

    with pytest.raises(RestlyConfigurationError, match="FunctionView.create_schema"):
        fr.include_view(fastapi.FastAPI(), FunctionView)


@pytest.mark.parametrize(
    "derive, args",
    [
        (fr.schemas.derive_response_schema, (GeneratedHero,)),
        (fr.schemas.derive_create_schema, (GeneratedHero, GeneratedHero)),
        (fr.schemas.derive_update_schema, (GeneratedHero, GeneratedHero)),
    ],
)
def test_the_derive_functions_reject_a_table_class(derive, args):
    with pytest.raises(RestlyConfigurationError, match="SQLModel table class"):
        derive(*args)


@BOTH_KINDS
def test_a_view_without_a_schema_validates_and_shows_required_fields(
    view_base, make_app
):
    app = make_app()

    @fr.include_view(app)
    class GeneratedHeroView(view_base):
        prefix = "/heroes"
        model = GeneratedHero

    client = TestClient(app)
    assert client.post("/heroes", json={"name": 5}).status_code == 422
    assert client.post("/heroes", json={}).status_code == 422
    created = client.post("/heroes", json={"name": "Ann"})
    assert created.status_code == 201
    assert created.json()["name"] == "Ann"

    schemas = spec(app)["components"]["schemas"]
    assert schemas["GeneratedHeroCreate"]["required"] == ["name"]
    assert "required" not in schemas["GeneratedHeroUpdate"]
    assert schemas["GeneratedHeroResponse"]["required"] == ["id", "name"]


@BOTH_KINDS
def test_a_data_model_as_the_schema_validates_and_shows_required_fields(
    view_base, make_app
):
    """The way that the error message and the docs name: limits and validators
    on a data model that the table and the schema both inherit from."""
    app = make_app()

    @fr.include_view(app)
    class HeroView(view_base):
        prefix = "/data-heroes"
        model = SchemaHero
        schema = HeroSchema

    client = TestClient(app)
    bad_bodies: list[dict[str, Any]] = [
        {"name": 5},
        {},
        {"name": "longer than five"},
        {"name": "x"},
        {"name": "Ann", "age": -1},
    ]
    for body in bad_bodies:
        assert client.post("/data-heroes", json=body).status_code == 422, body
    created = client.post("/data-heroes", json={"name": "Ann", "id": 99})
    assert created.status_code == 201
    assert created.json()["name"] == "Ann"
    assert created.json()["age"] == 3
    assert created.json()["id"] != 99
    hero_id = created.json()["id"]
    patched = client.patch(f"/data-heroes/{hero_id}", json={"age": 4})
    assert patched.status_code == 200
    assert patched.json() == {"name": "Ann", "age": 4, "id": hero_id}
    assert client.patch(f"/data-heroes/{hero_id}", json={"age": -1}).status_code == 422

    schemas = spec(app)["components"]["schemas"]
    assert schemas["HeroCreate"]["required"] == ["name"]
    assert schemas["HeroCreate"]["properties"]["name"]["maxLength"] == 5
    assert "id" not in schemas["HeroCreate"]["properties"]
    assert "required" not in schemas["HeroUpdate"]
    assert set(schemas["HeroResponse"]["required"]) == {"id", "name"}


def test_a_table_class_in_a_list_response_still_works():
    """A table class only goes out there, and it is not a base of any class."""
    app = _sync_app()

    @fr.include_view(app)
    class HeroListView(fr.RestView):
        prefix = "/list-heroes"
        model = GeneratedHero
        list_response_schema = list[GeneratedHero]

    client = TestClient(app)
    assert client.post("/list-heroes", json={"name": "Ann"}).status_code == 201
    assert any(row["name"] == "Ann" for row in client.get("/list-heroes").json())
    assert spec(app)["components"]["schemas"]["GeneratedHero"]["required"] == ["name"]
