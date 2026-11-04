"""A row never goes out through a route that declares no response.

Without a response model, FastAPI writes each attribute of a row, so also a
``WriteOnly`` column. A default endpoint method returns the row. So a replaced
endpoint that calls ``super()`` without declaring a response, or a custom route
that calls an endpoint method, must still send the view's response.
"""

import collections.abc
from collections.abc import Iterator
from typing import Annotated, Any, get_origin

import pydantic
import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Mapped
from typing_extensions import TypeAliasType

import fastapi_restly as fr
from fastapi_restly.testing._client import RestlyTestClient
from fastapi_restly.views._include import _with_view_responses

from ._models import SharedBase
from .conftest import create_tables


class UserSchema(fr.IDSchema):
    name: str
    password: fr.WriteOnly[str]


@pytest.fixture
def User():
    class User(fr.IDMixin, SharedBase):
        name: Mapped[str]
        password: Mapped[str]

    return User


def _user_view(User):
    class UserView(fr.AsyncRestView):
        prefix = "/users"
        model = User
        schema = UserSchema

    return UserView


def _with_row(client):
    create_tables()
    response = client.post("/users", json={"name": "a", "password": "SECRET"})
    assert "SECRET" not in response.text


def test_a_list_recipe_that_calls_super(client, User):
    class View(_user_view(User)):
        @fr.get("/")
        async def get_many_endpoint(self, list_params, as_text: bool = False):
            if not as_text:
                return await super().get_many_endpoint(list_params)
            result = await self.handle_get_many(list_params)
            return PlainTextResponse("\n".join(u.name for u in result.objects))

    fr.include_view(client.app, View)
    _with_row(client)

    body = client.get("/users").json()
    assert body["data"] == [{"id": 1, "name": "a"}]
    assert body["total_count"] == 1
    assert client.get("/users", params={"as_text": "true"}).text == "a"


def test_an_item_route_that_calls_super(client, User):
    class View(_user_view(User)):
        @fr.get("/{id}")
        async def get_one_endpoint(self, id: int):
            return await super().get_one_endpoint(id)

        @fr.post("/")
        async def create_endpoint(self, data: fr.views.CreateSchema):
            return await super().create_endpoint(data)

    fr.include_view(client.app, View)
    create_tables()

    created = client.post("/users", json={"name": "a", "password": "SECRET"})
    assert created.json() == {"id": 1, "name": "a"}
    assert client.get("/users/1").json() == {"id": 1, "name": "a"}


def test_a_custom_route_that_calls_an_endpoint_method(client, User):
    class View(_user_view(User)):
        @fr.get("/me")
        async def me(self):
            return await self.get_one_endpoint(1)

        @fr.get("/everyone")
        async def everyone(self):
            return await self.get_many_endpoint({})

    fr.include_view(client.app, View)
    _with_row(client)

    assert client.get("/users/me").json() == {"id": 1, "name": "a"}
    assert client.get("/users/everyone").json()["data"] == [{"id": 1, "name": "a"}]


def test_a_custom_route_that_returns_a_row(client, User):
    class View(_user_view(User)):
        @fr.get("/{id}/plain")
        async def plain(self, id: fr.views.IDType):
            return await self.handle_get_one(id)

    fr.include_view(client.app, View)
    _with_row(client)

    assert client.get("/users/1/plain").json() == {"id": 1, "name": "a"}


def test_a_row_inside_a_dict_or_a_list(client, User):
    class View(_user_view(User)):
        @fr.get("/{id}/wrapped")
        async def wrapped(self, id: fr.views.IDType):
            row = await self.get_one_endpoint(id)
            return {"data": row, "others": [row]}

    fr.include_view(client.app, View)
    _with_row(client)

    row = {"id": 1, "name": "a"}
    assert client.get("/users/1/wrapped").json() == {"data": row, "others": [row]}


@pytest.mark.parametrize("declared", ["none", "any"])
def test_a_route_that_says_it_has_no_model(client, User, declared):
    class View(_user_view(User)):
        if declared == "none":

            @fr.get("/{id}/plain", response_model=None)
            async def plain(self, id: fr.views.IDType):
                return await self.handle_get_one(id)

        else:

            @fr.get("/{id}/plain")
            async def plain(self, id: fr.views.IDType) -> Any:
                return await self.handle_get_one(id)

    fr.include_view(client.app, View)
    _with_row(client)

    assert client.get("/users/1/plain").json() == {"id": 1, "name": "a"}


def test_an_override_of_the_response_step_applies(client, User):
    class View(_user_view(User)):
        def to_single_response(self, obj):
            response = super().to_single_response(obj)
            response.name = response.name.upper()
            return response

        @fr.get("/{id}/plain")
        async def plain(self, id: fr.views.IDType):
            return await self.handle_get_one(id)

    fr.include_view(client.app, View)
    _with_row(client)

    assert client.get("/users/1/plain").json()["name"] == "A"


def test_other_values_go_out_as_they_are(client, User):
    class Other(fr.IDMixin, SharedBase):
        title: Mapped[str]

    class View(_user_view(User)):
        @fr.get("/{id}/plain")
        async def plain(self, id: fr.views.IDType):
            return {"id": id, "items": [1, "two", None]}

        @fr.get("/{id}/other")
        async def other(self, id: fr.views.IDType):
            return Other(title="t")

        @fr.get("/{id}/nothing")
        async def nothing(self, id: fr.views.IDType):
            return None

    fr.include_view(client.app, View)
    _with_row(client)

    assert client.get("/users/1/plain").json() == {"id": 1, "items": [1, "two", None]}
    assert client.get("/users/1/other").json()["title"] == "t"
    assert client.get("/users/1/nothing").json() is None


def test_a_route_that_names_another_class_keeps_fastapi_behaviour(client, User):
    class Names(fr.BaseSchema):
        name: str

    class View(_user_view(User)):
        @fr.get("/{id}/names")
        async def names(self, id: fr.views.IDType) -> Names:
            return await self.handle_get_one(id)

    fr.include_view(client.app, View)
    _with_row(client)

    assert client.get("/users/1/names").json() == {"name": "a"}


def test_the_spec_of_a_route_without_a_response_stays_empty(client, User):
    class View(_user_view(User)):
        @fr.get("/{id}/plain")
        async def plain(self, id: fr.views.IDType):
            return await self.handle_get_one(id)

    fr.include_view(client.app, View)
    create_tables()

    spec = client.get("/openapi.json").json()
    content = spec["paths"]["/users/{user_id}/plain"]["get"]["responses"]["200"][
        "content"
    ]
    assert content["application/json"]["schema"] == {}


@pytest.fixture
def sync_client(sync_db) -> Iterator[RestlyTestClient]:
    with RestlyTestClient(FastAPI()) as client:
        yield client


def test_a_sync_view(sync_db, sync_client, User):
    engine, _ = sync_db

    class View(fr.RestView):
        prefix = "/users"
        model = User
        schema = UserSchema

        @fr.get("/{id}")
        def get_one_endpoint(self, id: int):
            return super().get_one_endpoint(id)

        @fr.get("/")
        def get_many_endpoint(self, list_params):
            return super().get_many_endpoint(list_params)

        @fr.get("/{id}/wrapped")
        def wrapped(self, id: fr.views.IDType):
            return {"data": self.get_one_endpoint(id)}

    fr.include_view(sync_client.app, View)
    SharedBase.metadata.create_all(engine)
    sync_client.post("/users", json={"name": "a", "password": "SECRET"})

    row = {"id": 1, "name": "a"}
    assert sync_client.get("/users/1").json() == row
    assert sync_client.get("/users").json()["data"] == [row]
    assert sync_client.get("/users/1/wrapped").json() == {"data": row}


AnyAlias = TypeAliasType("AnyAlias", Any)


@pytest.mark.parametrize(
    "declared",
    [
        dict,
        dict[str, Any],
        list,
        list[Any],
        tuple[Any, ...],
        object,
        Any | None,
        dict[str, Any] | None,
        Annotated[Any, "note"],
        AnyAlias,
        collections.abc.Mapping[str, Any],
    ],
    ids=str,
)
def test_a_type_that_names_no_fields_does_not_send_each_column(client, User, declared):
    """Such a type says nothing about a row, so it counts as no response."""
    is_list = get_origin(declared) in (list, tuple) or declared is list

    class View(_user_view(User)):
        @fr.get("/first")
        async def first(self):
            row = await self.handle_get_one(1)
            return [row] if is_list else {"user": row}

        first.__annotations__["return"] = declared

    fr.include_view(client.app, View)
    _with_row(client)

    response = client.get("/users/first")
    assert "SECRET" not in response.text
    assert '"name":"a"' in response.text


def test_a_type_with_fields_stays_as_it_is(client, User):
    class Everything(pydantic.BaseModel):
        name: str
        password: str

    class View(_user_view(User)):
        @fr.get("/first")
        async def first(self) -> dict[str, Everything]:
            return {"user": await self.handle_get_one(1)}

    fr.include_view(client.app, View)
    _with_row(client)

    assert client.get("/users/first").json() == {
        "user": {"name": "a", "password": "SECRET"}
    }


def test_a_map_of_many_rows_sends_the_view_response(client, User):
    """Restly looks at the first values of a dict, whatever its size, so a route
    does not change with the number of rows."""

    class View(_user_view(User)):
        @fr.get("/map")
        async def by_id(self):
            result = await self.session.scalars(select(self.model))
            return {row.id: row for row in result.all()}

    fr.include_view(client.app, View)
    _with_row(client)
    for index in range(30):
        client.post("/users", json={"name": f"u{index}", "password": "SECRET"})

    response = client.get("/users/map")
    assert "SECRET" not in response.text
    assert len(response.json()) == 31


def test_a_dict_with_a_list_of_rows_sends_the_view_response(client, User):
    class View(_user_view(User)):
        @fr.get("/wrapped")
        async def wrapped(self):
            result = await self.session.scalars(select(self.model))
            return {"total": 1, "data": result.all()}

    fr.include_view(client.app, View)
    _with_row(client)

    assert client.get("/users/wrapped").json() == {
        "total": 1,
        "data": [{"id": 1, "name": "a"}],
    }


def test_a_list_keeps_an_item_that_is_not_a_row(client, User):
    class View(_user_view(User)):
        @fr.get("/mixed")
        async def mixed(self):
            return [await self.handle_get_one(1), {"note": "x"}]

    fr.include_view(client.app, View)
    _with_row(client)

    assert client.get("/users/mixed").json() == [{"id": 1, "name": "a"}, {"note": "x"}]


@pytest.mark.parametrize(
    "shape",
    [
        lambda row: {"a": {"b": row}},
        lambda row: {"a": [{"b": row}]},
        lambda row: [{"user": row}],
        lambda row: [1, row],
        lambda row: (row,),
        lambda row: {row},
        lambda row: (item for item in [row]),
        lambda row: {**{f"k{index}": index for index in range(20)}, "user": row},
    ],
    ids=[
        "nested-dict",
        "dict-list-dict",
        "list-of-dicts",
        "row-not-first",
        "tuple",
        "set",
        "generator",
        "dict-value-21",
    ],
)
def test_what_restly_does_not_look_at_goes_out_as_fastapi_sends_it(client, User, shape):
    """The docs name these. Restly checks the sensible shapes, and no more."""

    class View(_user_view(User)):
        @fr.get("/first")
        async def first(self):
            return shape(await self.handle_get_one(1))

    fr.include_view(client.app, View)
    _with_row(client)

    assert "SECRET" in client.get("/users/first").text


@pytest.mark.parametrize(
    "payload",
    [[{"a": 1}] * 3, {"data": [{"a": 1}]}, {"a": {"b": 1}}, (1, 2), [], {}],
    ids=str,
)
def test_a_payload_without_rows_stays_the_same_object(User, payload):
    class View:
        model = User

    assert _with_view_responses(View(), payload) is payload


def test_a_list_result_in_a_dict_on_a_react_admin_view_sends_the_items(client, User):
    class View(fr.AsyncReactAdminView):
        prefix = "/users"
        model = User
        schema = UserSchema

        @fr.get("/wrapped")
        async def wrapped(self, list_params):
            return {"list": await self.handle_get_many(list_params)}

    fr.include_view(client.app, View)
    _with_row(client)

    response = client.get("/users/wrapped")
    assert "SECRET" not in response.text
    assert response.json() == {"list": [{"id": 1, "name": "a"}]}
