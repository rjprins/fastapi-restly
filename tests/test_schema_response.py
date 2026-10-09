"""The response class and the list response class of a view.

``fr.schemas.derive_schema_response`` and
``fr.schemas.derive_schema_list_response`` return the classes that a view
uses, so a custom route can name them and OpenAPI shows one type for each
resource.
"""

import asyncio
import collections
import warnings
from collections.abc import Iterator
from typing import Any, Generic, TypeVar

import fastapi
import pydantic
import pytest
from sqlalchemy import ForeignKey
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.exc import RestlyDuplicateSchemaNameWarning
from fastapi_restly.testing import RestlyTestClient

from .conftest import create_tables

T = TypeVar("T")


class DataCount(pydantic.BaseModel, Generic[T]):
    data: list[T]
    total_count: int


def _spec(app: fastapi.FastAPI) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("error", RestlyDuplicateSchemaNameWarning)
        return app.openapi()


def _components(app: fastapi.FastAPI) -> set[str]:
    return set(_spec(app)["components"]["schemas"]) - {
        "HTTPValidationError",
        "ValidationError",
    }


def _ref(app: fastapi.FastAPI, path: str, method: str, status: str = "200") -> str:
    operation = _spec(app)["paths"][path][method]
    schema = operation["responses"][status]["content"]["application/json"]["schema"]
    return schema["$ref"].removeprefix("#/components/schemas/")


def _response_model(app: fastapi.FastAPI, path: str, method: str) -> Any:
    for route in app.routes:
        if (
            isinstance(route, fastapi.routing.APIRoute)
            and route.path_format == path
            and method.upper() in route.methods
        ):
            return route.response_model
    raise AssertionError(f"no route {method} {path}")


def test_derive_schema_response_returns_the_class_the_view_uses():
    class Account(fr.IDBase):
        name: Mapped[str]
        password_hash: Mapped[str] = mapped_column(default="")

    class AccountSchema(fr.IDSchema):
        name: str
        password: fr.WriteOnly[str]

    AccountResponse = fr.schemas.derive_schema_response(AccountSchema)

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class AccountView(fr.AsyncRestView):
        prefix = "/accounts"
        model = Account
        schema = AccountSchema

    assert AccountResponse.__name__ == "AccountResponse"
    assert issubclass(AccountResponse, AccountSchema)
    assert set(AccountResponse.model_fields) == {"id", "name"}
    assert fr.schemas.derive_schema_response(AccountSchema) is AccountResponse
    assert _response_model(app, "/accounts/{id}", "get") is AccountResponse


def test_derive_schema_list_response_returns_the_class_the_view_uses():
    class Book(fr.IDBase):
        title: Mapped[str]

    class BookSchema(fr.IDSchema):
        title: str

    BookResponse = fr.schemas.derive_schema_response(BookSchema)
    BookListResponse = fr.schemas.derive_schema_list_response(BookResponse)

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class BookView(fr.AsyncRestView):
        prefix = "/books"
        model = Book
        schema = BookSchema

    assert BookListResponse.__name__ == "BookListResponse"
    assert issubclass(BookListResponse, fr.views.PaginatedEnvelope)
    assert _response_model(app, "/books", "get") is BookListResponse


def test_only_the_envelope_of_the_pagination_matters():
    class LeafSchema(fr.IDSchema):
        name: str

    LeafResponse = fr.schemas.derive_schema_response(LeafSchema)
    paginated = fr.schemas.derive_schema_list_response(LeafResponse)
    small_pages = fr.schemas.derive_schema_list_response(
        LeafResponse, pagination=fr.NumberedPagination(default_page_size=5)
    )
    unpaginated = fr.schemas.derive_schema_list_response(LeafResponse, pagination=None)
    no_pagination = fr.schemas.derive_schema_list_response(
        LeafResponse, pagination=fr.NoPagination()
    )
    counted = fr.schemas.derive_schema_list_response(
        LeafResponse, pagination=fr.NoPagination(envelope=DataCount)
    )

    assert small_pages is paginated
    assert no_pagination is unpaginated
    assert issubclass(unpaginated, fr.views.Envelope)
    assert not issubclass(unpaginated, fr.views.PaginatedEnvelope)
    assert issubclass(counted, DataCount)
    assert {paginated.__name__, unpaginated.__name__, counted.__name__} == {
        "LeafListResponse"
    }


def test_a_custom_route_that_names_the_classes_shows_one_type_per_resource():
    class Task(fr.IDBase):
        title: Mapped[str]
        archived: Mapped[bool] = False

    class TaskSchema(fr.IDSchema):
        title: str
        archived: bool = False

    TaskResponse = fr.schemas.derive_schema_response(TaskSchema)
    TaskListResponse = fr.schemas.derive_schema_list_response(
        TaskResponse, pagination=None
    )

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class TaskView(fr.AsyncRestView):
        prefix = "/tasks"
        model = Task
        schema = TaskSchema
        pagination = None

        @fr.get("/archived", response_model=TaskListResponse)
        async def archived(self, list_params):
            result = await self.handle_get_many(list_params, where=Task.archived)
            return self.to_response(result, fr.ResponseShape.LIST)

        @fr.post("/{id}/archive", response_model=TaskResponse)
        async def archive(self, id: int):
            task = await self.get_one(id)
            async with self.write_action("archive", obj=task):
                task.archived = True
            return self.to_response(task)

    assert _ref(app, "/tasks/archived", "get") == "TaskListResponse"
    assert _ref(app, "/tasks", "get") == "TaskListResponse"
    assert _ref(app, "/tasks/{id}/archive", "post", "201") == "TaskResponse"
    assert _components(app) == {
        "TaskResponse",
        "TaskCreate",
        "TaskUpdate",
        "TaskListResponse",
    }


def test_the_wrong_pagination_gives_a_second_class_and_a_warning():
    class Shelf(fr.IDBase):
        name: Mapped[str]

    class ShelfSchema(fr.IDSchema):
        name: str

    ShelfResponse = fr.schemas.derive_schema_response(ShelfSchema)
    # The view has no pagination, but the route forgets to say so.
    ShelfListResponse = fr.schemas.derive_schema_list_response(ShelfResponse)

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class ShelfView(fr.AsyncRestView):
        prefix = "/shelves"
        model = Shelf
        schema = ShelfSchema
        pagination = None

        @fr.get("/empty", response_model=ShelfListResponse)
        async def empty(self, list_params):
            result = await self.handle_get_many(list_params)
            return self.to_response(result, fr.ResponseShape.LIST)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", RestlyDuplicateSchemaNameWarning)
        app.openapi()

    [message] = [
        str(warning.message)
        for warning in caught
        if issubclass(warning.category, RestlyDuplicateSchemaNameWarning)
    ]
    assert "More than one class has the name ShelfListResponse." in message
    # The class that the route built says where it comes from, and the
    # warning says how to get the view's class.
    assert "a list response with the envelope PaginatedEnvelope" in message
    assert "passes the view's pagination to derive_schema_list_response" in message


# A view whose own schema_response differs from its schema. The schema is
# what comes in; the response class is what goes out. The response class
# leaves out email, score and team_id, names the team relationship, and has
# a computed field, so each test can see which class a response went through.


class _Base(DeclarativeBase):
    pass


class _Team(_Base):
    __tablename__ = "sr_team"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


class _Member(_Base):
    __tablename__ = "sr_member"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    email: Mapped[str]
    score: Mapped[int] = mapped_column(default=0)
    team_id: Mapped[int | None] = mapped_column(ForeignKey(_Team.id))
    team: Mapped[_Team | None] = relationship()


class TeamSchema(fr.IDSchema):
    name: str


class MemberSchema(fr.IDSchema):
    name: str
    email: str
    score: int = 0
    team_id: int | None = None


class MemberResponse(fr.IDSchema):
    name: str
    team: TeamSchema | None = None

    @pydantic.computed_field
    def label(self) -> str:
        return f"{self.name} ({self.team.name if self.team else '-'})"


def _member_view(base: type) -> type:
    class MemberView(base):  # type: ignore[valid-type,misc]
        prefix = "/members"
        model = _Member
        schema = MemberSchema
        schema_response = MemberResponse

    return MemberView


@pytest.fixture(params=[fr.RestView, fr.AsyncRestView], ids=["sync", "async"])
def members(request: pytest.FixtureRequest) -> Iterator[tuple[type, RestlyTestClient]]:
    app = fastapi.FastAPI()
    view = fr.include_view(app, _member_view(request.param))
    rows = [
        _Team(id=1, name="red"),
        _Team(id=2, name="blue"),
        _Member(id=1, name="ann", email="ann@example.com", score=3, team_id=1),
        _Member(id=2, name="bob", email="bob@example.com", score=1, team_id=2),
    ]
    if request.param is fr.RestView:
        engine, make_session = request.getfixturevalue("sync_db")
        _Base.metadata.create_all(engine)
        with make_session() as session:
            session.add_all(rows)
            session.commit()
    else:

        async def prepare() -> None:
            engine = fr.db.get_async_engine()
            async with engine.begin() as connection:
                await connection.run_sync(_Base.metadata.create_all)
            async with async_sessionmaker(engine)() as session:
                session.add_all(rows)
                await session.commit()

        asyncio.run(prepare())

    with RestlyTestClient(app) as client:
        yield view, client


ANN = {"id": 1, "name": "ann", "team": {"id": 1, "name": "red"}, "label": "ann (red)"}


def test_everything_that_goes_out_follows_the_view_s_response_class(members):
    view, client = members

    assert view.schema_response is MemberResponse
    assert client.get("/members/1").json() == ANN
    assert client.get("/members/").json()["data"][0] == ANN

    created = client.post(
        "/members/", json={"name": "cy", "email": "cy@example.com", "team_id": 2}
    ).json()
    assert created == {
        "id": 3,
        "name": "cy",
        "team": {"id": 2, "name": "blue"},
        "label": "cy (blue)",
    }
    updated = client.patch("/members/3", json={"team_id": 1}).json()
    assert updated["team"] == {"id": 1, "name": "red"}


def test_the_view_s_schema_stays_what_comes_in(members):
    view, _ = members

    assert set(view.schema_create.model_fields) == {"name", "email", "score", "team_id"}
    assert view.schema_create.__name__ == "MemberCreate"
    assert view.schema_update.__name__ == "MemberUpdate"


def test_the_list_params_follow_the_response_class(members):
    view, client = members

    assert view.schema_list_params.__name__ == "MemberListParams"
    names = set(view.schema_list_params.model_fields)
    assert "name" in names
    assert "team.name" in {
        field.alias or name
        for name, field in view.schema_list_params.model_fields.items()
    }
    assert not names & {"email", "score", "team_id"}

    page = client.get("/members/", params={"team.name": "blue"}).json()
    assert [member["name"] for member in page["data"]] == ["bob"]
    client.get("/members/", params={"email": "ann@example.com"}, assert_status_code=422)
    client.get("/members/", params={"sort": "score"}, assert_status_code=400)


def test_the_view_loads_the_relationships_the_response_class_names(members):
    view, _ = members

    # The view's schema names no relationship. Without this load, the async
    # view could not send the team: it would fail with MissingGreenlet.
    instance = object.__new__(view)
    assert len(instance.get_relationship_loader_options()) == 1


def test_openapi_shows_the_view_s_response_class(members):
    _, client = members
    app = client.app

    assert _ref(app, "/members/{id}", "get") == "MemberResponse"
    assert _ref(app, "/members", "get") == "MemberListResponse"
    assert _components(app) == {
        "MemberResponse",
        "MemberCreate",
        "MemberUpdate",
        "MemberListResponse",
        "TeamSchema",
    }
    assert _response_model(app, "/members", "get") is (
        fr.schemas.derive_schema_list_response(MemberResponse)
    )


def test_a_subclass_keeps_or_rebuilds_the_response_class():
    class Plant(fr.IDBase):
        name: Mapped[str]
        secret: Mapped[str] = mapped_column(default="")

    class PlantSchema(fr.IDSchema):
        name: str

    class SecretPlantSchema(fr.IDSchema):
        name: str
        secret: str

    class PlantResponse(fr.IDSchema):
        name: str

    class PlantView(fr.AsyncRestView):
        prefix = "/plants"
        model = Plant
        schema = PlantSchema

    class OwnResponseView(PlantView):
        schema_response = PlantResponse

    class InheritsOwnResponseView(OwnResponseView):
        prefix = "/inherits"

    class NewSchemaView(OwnResponseView):
        prefix = "/new-schema"
        schema = SecretPlantSchema

    app = fastapi.FastAPI()
    for view in (PlantView, OwnResponseView, InheritsOwnResponseView, NewSchemaView):
        fr.include_view(app, view)

    assert PlantView.schema_response is fr.schemas.derive_schema_response(PlantSchema)
    assert OwnResponseView.schema_response is PlantResponse
    assert InheritsOwnResponseView.schema_response is PlantResponse
    # A schema set nearer than the response class rebuilds it, as it rebuilds
    # schema_create and schema_update.
    assert NewSchemaView.schema_response is fr.schemas.derive_schema_response(
        SecretPlantSchema
    )


def test_a_react_admin_view_follows_the_response_class(client):
    class Herb(fr.IDBase):
        name: Mapped[str]
        notes: Mapped[str] = mapped_column(default="")

    class HerbSchema(fr.IDSchema):
        name: str
        notes: str = ""

    class HerbResponse(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class HerbView(fr.AsyncReactAdminView):
        prefix = "/herbs"
        model = Herb
        schema = HerbSchema
        schema_response = HerbResponse

    create_tables()
    client.post("/herbs/", json={"name": "mint", "notes": "fresh"})

    assert client.get("/herbs/").json() == [{"id": 1, "name": "mint"}]
    assert client.put("/herbs/1", json={"notes": "dry"}).json() == {
        "id": 1,
        "name": "mint",
    }
    client.get("/herbs/", params={"filter": '{"notes": "dry"}'}, assert_status_code=400)


@pytest.mark.parametrize(
    "base", [fr.AsyncRestView, fr.AsyncReactAdminView], ids=["rest", "react-admin"]
)
def test_an_instance_of_the_schema_goes_out_through_the_view_s_response_class(
    client, base
):
    """An override returns an instance of the view's schema. Restly validates
    it into the view's own response class, on every route: also on the list
    of a react-admin view, which has no response model to do it."""
    runs: collections.Counter[str] = collections.Counter()

    class Bird(fr.IDBase):
        name: Mapped[str]
        secret: Mapped[str] = mapped_column(default="")

    class BirdSchema(fr.IDSchema):
        name: str
        secret: str = ""

        @pydantic.field_validator("name")
        @classmethod
        def count_schema(cls, value: str) -> str:
            runs["schema"] += 1
            return value

    class BirdResponse(fr.IDSchema):
        name: str

        @pydantic.field_validator("name")
        @classmethod
        def count_response(cls, value: str) -> str:
            runs["response"] += 1
            return value

    @fr.include_view(client.app)
    class BirdView(base):  # type: ignore[valid-type,misc]
        prefix = "/birds"
        model = Bird
        schema = BirdSchema
        schema_response = BirdResponse

        def to_single_response(self, obj):
            return BirdSchema.model_validate(obj, from_attributes=True)

    create_tables()
    client.post("/birds/", json={"name": "tit", "secret": "s3"})

    for path in ("/birds/1", "/birds/"):
        runs.clear()
        body = client.get(path).json()
        if path == "/birds/":
            body = body[0] if isinstance(body, list) else body["data"][0]
        assert body == {"id": 1, "name": "tit"}, path
        # Each class validates once: the override into the schema, and Restly
        # into the response class.
        assert runs == {"schema": 1, "response": 1}, path


@pytest.mark.parametrize(
    "base", [fr.AsyncRestView, fr.AsyncReactAdminView], ids=["rest", "react-admin"]
)
def test_an_instance_of_a_subclass_goes_out_without_its_other_fields(client, base):
    """The view's schema adds fields to its own response class. An override
    returns an instance of the schema: only the fields of the response class
    go out, also where no response model removes the others."""

    class Eel(fr.IDBase):
        name: Mapped[str]
        secret: Mapped[str] = mapped_column(default="")

    class EelResponse(fr.IDSchema):
        name: str

    class EelSchema(EelResponse):
        secret: str = ""

    @fr.include_view(client.app)
    class EelView(base):  # type: ignore[valid-type,misc]
        prefix = "/eels"
        model = Eel
        schema = EelSchema
        schema_response = EelResponse

        def to_single_response(self, obj):
            return EelSchema.model_validate(obj, from_attributes=True)

        @fr.get("/{id}/plain", response_model=None)
        async def plain(self, id: int) -> Any:
            return self.to_response(await self.handle_get_one(id))

    create_tables()
    client.post("/eels/", json={"name": "conger", "secret": "s3"})

    for path in ("/eels/1", "/eels/", "/eels/1/plain"):
        body = client.get(path).json()
        if path == "/eels/":
            body = body[0] if isinstance(body, list) else body["data"][0]
        assert body == {"id": 1, "name": "conger"}, path


def test_writeonly_keys_in_the_extra_fields_do_not_go_out():
    class Key(fr.IDSchema):
        model_config = pydantic.ConfigDict(extra="allow")

        password: fr.WriteOnly[str] = pydantic.Field(alias="pwd")

    KeyResponse = fr.schemas.derive_schema_response(Key)
    key = Key.model_validate({"id": 1, "pwd": "p1", "password": "p2", "tag": "a"})

    response = fr.schemas._base._as_response(KeyResponse, key)

    assert response.model_dump() == {"id": 1, "tag": "a"}


def test_a_sqlmodel_table_row_goes_out_as_an_orm_object():
    sqlmodel = pytest.importorskip("sqlmodel")

    class Hero(sqlmodel.SQLModel, table=True):
        id: int | None = sqlmodel.Field(default=None, primary_key=True)
        name: str

    class HeroSchema(fr.IDSchema):
        title: str = pydantic.Field(alias="name")

    class HeroView(fr.RestView):
        prefix = "/heroes"
        model = Hero
        schema = HeroSchema

    fr.include_view(fastapi.FastAPI(), HeroView)
    view = object.__new__(HeroView)

    response = view.to_single_response(Hero(id=1, name="Ann"))

    assert response.model_dump() == {"id": 1, "title": "Ann"}


def test_an_instance_of_another_model_goes_out_through_the_response_class(client):
    """Also on a view whose response class Restly derives: an instance of a
    model that is neither the schema nor the response class is validated
    into the response class, so its other fields stay behind."""

    class Fish(fr.IDBase):
        name: Mapped[str]
        secret: Mapped[str] = mapped_column(default="")

    class FishSchema(fr.IDSchema):
        name: str

    class FishWithSecret(fr.IDSchema):
        name: str
        secret: str

    @fr.include_view(client.app)
    class FishView(fr.AsyncReactAdminView):
        prefix = "/fish"
        model = Fish
        schema = FishSchema

        def to_single_response(self, obj):
            return FishWithSecret.model_validate(obj, from_attributes=True)

    create_tables()
    client.post("/fish/", json={"name": "cod"})

    assert client.get("/fish/").json() == [{"id": 1, "name": "cod"}]
    assert client.get("/fish/1").json() == {"id": 1, "name": "cod"}
