"""A SQLModel table class does not validate, so it cannot be a schema of a view.

A view without a schema, or with a class without ``table=True``, validates the
request body, and OpenAPI shows the required fields.
"""

import asyncio
import sys
from typing import Annotated, Any, Literal, Optional

import fastapi
import pydantic
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from typing_extensions import TypeAliasType

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


class TeamRow(sqlmodel.SQLModel, table=True):
    id: int | None = sqlmodel.Field(default=None, primary_key=True)
    name: str = sqlmodel.Field(max_length=5)
    is_admin_team: bool = False


class ChildOfTeamRow(TeamRow):
    pass


class PlayerRow(sqlmodel.SQLModel, table=True):
    id: int | None = sqlmodel.Field(default=None, primary_key=True)
    name: str
    team_id: int | None = sqlmodel.Field(default=None, foreign_key="teamrow.id")
    team: Optional[TeamRow] = sqlmodel.Relationship()


class TeamHolder(pydantic.BaseModel):
    team: TeamRow | None = None


class ReadOnlyTeamHolder(pydantic.BaseModel):
    team: fr.ReadOnly[TeamRow | None] = None


class ReadOnlyChildHolder(pydantic.BaseModel):
    team: fr.ReadOnly[ChildOfTeamRow | None] = None


class RowsOfChild(pydantic.BaseModel):
    rows: list[ChildOfTeamRow]


class TreeNode(pydantic.BaseModel):
    children: list["TreeNode"] = []


TeamAlias = TypeAliasType("TeamAlias", TeamRow)


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
@pytest.mark.parametrize(
    "setting, advice",
    [
        ("schema", "Leave `schema` out"),
        ("create_schema", "Remove create_schema"),
        ("update_schema", "Remove update_schema"),
    ],
)
def test_a_table_class_as_a_schema_fails_when_the_view_registers(
    view_base, setting, advice
):
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
    assert "does not validate its input" in message
    assert advice in message
    assert "without `table=True`" in message
    # The class from derive_schema requires the id, so it is no write schema.
    assert "derive_schema" not in message


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


@pytest.mark.parametrize("view_base", [fr.RestView, fr.AsyncRestView])
@pytest.mark.parametrize("setting", ["schema", "create_schema", "update_schema"])
@pytest.mark.parametrize(
    "field_type",
    [
        TeamRow | None,
        Optional[TeamRow],
        ChildOfTeamRow,
        list[TeamRow],
        dict[str, TeamRow],
        Annotated[TeamRow, pydantic.Field(description="The team")],
        list[Annotated[TeamRow, pydantic.Field(description="A team")] | None],
        TeamHolder,
        list[TeamHolder],
        int | TeamRow,
        TeamAlias,
        list[TeamAlias],
    ],
    ids=lambda field_type: str(field_type)[:40],
)
def test_a_table_class_in_a_writable_field_fails(view_base, setting, field_type):
    schema = pydantic.create_model("PlayerInput", team=(field_type, ...))
    view = type(
        "PlayerView",
        (view_base,),
        {"prefix": "/players", "model": PlayerRow, setting: schema},
    )

    with pytest.raises(RestlyConfigurationError) as info:
        fr.include_view(fastapi.FastAPI(), view)

    message = str(info.value)
    assert "has the field team" in message
    assert "a SQLModel table class" in message
    assert "fr.MustExist" in message
    assert "fr.ReadOnly" in message


@pytest.mark.parametrize("view_base", [fr.RestView, fr.AsyncRestView])
@pytest.mark.parametrize(
    "setting, fields_or_type",
    [
        ("schema", {"team": (fr.ReadOnly[ChildOfTeamRow | None], None)}),
        ("create_schema", {"team": (fr.ReadOnly[ChildOfTeamRow | None], None)}),
        ("update_schema", {"team": (fr.ReadOnly[ChildOfTeamRow | None], None)}),
        ("response_schema", {"team": (ChildOfTeamRow | None, None)}),
        ("response_schema", {"holder": (ReadOnlyChildHolder | None, None)}),
        ("list_response_schema", RowsOfChild),
        ("list_response_schema", list[ReadOnlyChildHolder]),
    ],
)
def test_a_subclass_of_a_table_class_inside_a_schema_fails(
    view_base, setting, fields_or_type
):
    """OpenAPI would show the subclass with no required fields."""
    value = fields_or_type
    if isinstance(fields_or_type, dict):
        value = pydantic.create_model("Shown", **fields_or_type)
    view = type(
        "PlayerView",
        (view_base,),
        {"prefix": "/players", "model": PlayerRow, setting: value},
    )

    with pytest.raises(RestlyConfigurationError) as info:
        fr.include_view(fastapi.FastAPI(), view)

    message = str(info.value)
    assert f"PlayerView.{setting} holds ChildOfTeamRow, a subclass" in message
    assert "no required fields" in message


def test_a_table_class_in_a_field_of_a_generic_class_fails():
    schema = pydantic.create_model(
        "PlayerInput", team=(fr.views.NumberedEnvelope[TeamRow], ...)
    )

    class EnvelopeInputView(fr.RestView):
        prefix = "/players"
        model = PlayerRow
        create_schema = schema

    with pytest.raises(RestlyConfigurationError, match="holds TeamRow"):
        fr.include_view(fastapi.FastAPI(), EnvelopeInputView)


@pytest.mark.parametrize("view_base", [fr.RestView, fr.AsyncRestView])
@pytest.mark.parametrize(
    "fields",
    [
        {"team": (fr.ReadOnly[TeamRow | None], None)},
        {"team_id": (fr.MustExist[int, TeamRow] | None, None)},
        {"team": (fr.IDRef[TeamRow] | None, None)},
        {"team": (fr.IDSchema[TeamRow] | None, None)},
        {"holder": (ReadOnlyTeamHolder | None, None)},
        {"tree": (TreeNode | None, None)},
        {"kind": (Literal["a", "b"], "a"), "pair": (tuple[int, ...], ())},
    ],
    ids=[
        "read-only",
        "must-exist",
        "id-ref",
        "id-schema",
        "nested-read-only",
        "recursive-class",
        "literal-and-tuple",
    ],
)
def test_a_field_that_takes_no_table_class_registers(view_base, fields):
    schema = pydantic.create_model("PlayerShape", **fields)
    view = type(
        "PlayerShapeView",
        (view_base,),
        {"prefix": "/players", "model": PlayerRow, "schema": schema},
    )

    fr.include_view(fastapi.FastAPI(), view)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="`type` statement")
def test_a_recursive_type_alias_in_a_field_registers():
    namespace: dict[str, Any] = {}
    exec("type Json = dict[str, Json] | list[Json] | str | int | None", namespace)

    class DocumentSchema(fr.IDSchema):
        data: namespace["Json"]

    class DocumentView(fr.RestView):
        prefix = "/players"
        model = PlayerRow
        schema = DocumentSchema

    fr.include_view(fastapi.FastAPI(), DocumentView)


def test_a_view_that_takes_no_body_can_hold_a_table_class_in_a_field():
    class ReadOnlyPlayerSchema(fr.IDSchema):
        team: TeamRow | None = None

    class ReadOnlyPlayerView(fr.RestView):
        prefix = "/players"
        model = PlayerRow
        schema = ReadOnlyPlayerSchema
        include_routes = (fr.views.ViewRoute.GET_ONE, fr.views.ViewRoute.GET_MANY)

    fr.include_view(fastapi.FastAPI(), ReadOnlyPlayerView)


def test_a_table_class_in_the_response_schema_can_be_in_a_field():
    class PlayerOut(fr.IDSchema):
        team: TeamRow | None = None

    class PlayerOutView(fr.RestView):
        prefix = "/players"
        model = PlayerRow
        response_schema = PlayerOut

    fr.include_view(fastapi.FastAPI(), PlayerOutView)


@BOTH_KINDS
def test_a_view_without_a_schema_on_a_model_with_a_relationship_works(
    view_base, make_app
):
    app = make_app()
    view = type(
        "PlainPlayerView",
        (view_base,),
        {"prefix": "/plain-players", "model": PlayerRow},
    )
    fr.include_view(app, view)

    created = TestClient(app).post("/plain-players", json={"name": "Ann"})

    assert created.status_code == 201
    assert "team" not in view.create_schema.model_fields


@BOTH_KINDS
def test_a_read_only_table_class_shows_the_row_and_takes_no_input(view_base, make_app):
    app = make_app()

    class ShownPlayerSchema(fr.IDSchema):
        name: str
        team_id: fr.MustExist[int, TeamRow] | None = None
        team: fr.ReadOnly[TeamRow | None] = None

    view = type(
        "ShownPlayerView",
        (view_base,),
        {"prefix": "/shown-players", "model": PlayerRow, "schema": ShownPlayerSchema},
    )
    fr.include_view(app, view)
    client = TestClient(app)

    created = client.post(
        "/shown-players",
        json={"name": "Ann", "team": {"id": 777, "is_admin_team": True, "name": "x"}},
    )

    assert created.status_code == 201
    assert created.json()["team"] is None
    assert "team" not in view.create_schema.model_fields
    schemas = spec(app)["components"]["schemas"]
    assert "team" not in schemas["ShownPlayerCreate"]["properties"]
    assert "team" in schemas["ShownPlayerResponse"]["properties"]


@BOTH_KINDS
def test_a_table_class_as_the_response_schema_still_works(view_base, make_app):
    app = make_app()
    view = type(
        "HeroOutView",
        (view_base,),
        {
            "prefix": "/out-heroes",
            "model": GeneratedHero,
            "response_schema": GeneratedHero,
        },
    )
    fr.include_view(app, view)

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
    with pytest.raises(RestlyConfigurationError, match="SQLModel table class") as info:
        derive(*args)

    assert "`fr.schemas.derive_schema(GeneratedHero)`" in str(info.value)


@pytest.mark.parametrize(
    "derive, args",
    [
        (fr.schemas.derive_response_schema, ()),
        (fr.schemas.derive_create_schema, (GeneratedHero,)),
        (fr.schemas.derive_update_schema, (GeneratedHero,)),
    ],
)
def test_the_derive_functions_take_the_class_that_their_error_names(derive, args):
    derived = fr.schemas.derive_schema(GeneratedHero)

    assert derive(derived, *args).model_fields


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


@BOTH_KINDS
def test_a_table_class_in_a_list_response_still_works(view_base, make_app):
    """A table class only goes out there, and it is not a base of any class."""
    app = make_app()
    view = type(
        "HeroListView",
        (view_base,),
        {
            "prefix": "/list-heroes",
            "model": GeneratedHero,
            "list_response_schema": list[GeneratedHero],
        },
    )
    fr.include_view(app, view)

    client = TestClient(app)
    assert client.post("/list-heroes", json={"name": "Ann"}).status_code == 201
    assert any(row["name"] == "Ann" for row in client.get("/list-heroes").json())
    assert spec(app)["components"]["schemas"]["GeneratedHero"]["required"] == ["name"]
