"""``list_response_schema``: the class of the list response is a setting.

Restly fills the class as it fills a pagination's ``envelope``: by field name,
from the page values. So a view changes its list response with one line, and the
spec follows. On a react-admin view the setting is a list, as the route sends a
JSON array.
"""

import collections.abc
import dataclasses
from collections.abc import Iterator
from typing import Annotated, Any, Generic, NamedTuple, TypeVar

import pydantic
import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Mapped
from typing_extensions import TypedDict

import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError
from fastapi_restly.testing._client import RestlyTestClient
from fastapi_restly.views import (
    ListResponseSchema,
    ListResult,
    NumberedEnvelope,
    ResponseSchema,
    SchemaType,
)

from ._models import SharedBase
from .conftest import create_tables


class ItemSchema(fr.IDSchema):
    name: str
    note: str


class ItemSummary(fr.IDSchema):
    name: str


class PlainSummary(pydantic.BaseModel):
    name: str


ItemResponse = fr.schemas.derive_response_schema(ItemSchema)


class ArticleList(pydantic.BaseModel):
    data: list[ItemResponse] = pydantic.Field(alias="articles")
    total_count: int = pydantic.Field(alias="articlesCount")


class Rows(pydantic.BaseModel):
    rows: list[ItemResponse]


class Extra(pydantic.BaseModel):
    extra: str


@pytest.fixture
def Item():
    class Item(fr.IDMixin, SharedBase):
        name: Mapped[str]
        note: Mapped[str]

    return Item


def _fill(client, path="/items"):
    create_tables()
    for name in "ab":
        client.post(path, json={"name": name, "note": "long text"})


def _response_schema(client, path="/items"):
    operation = client.get("/openapi.json").json()["paths"][path]["get"]
    return operation["responses"]["200"]["content"]["application/json"]["schema"]


def _response_properties(client, path="/items"):
    spec = client.get("/openapi.json").json()
    name = _response_schema(client, path)["$ref"].rsplit("/", 1)[-1]
    return name, spec["components"]["schemas"][name]["properties"]


# --- the default --------------------------------------------------------------


def test_the_default_is_the_class_of_the_pagination(client, Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.get("/archived")
        async def archived(self, list_params) -> ListResponseSchema:
            return await self.handle_get_many(list_params)

    before = ItemView.list_response_schema
    assert before.__name__ == "ItemListResponse"
    assert before is fr.schemas.derive_list_response_schema(ItemView.response_schema)

    fr.include_view(client.app, ItemView)
    _fill(client)

    assert ItemView.list_response_schema is before
    name, properties = _response_properties(client)
    assert name == "ItemListResponse"
    assert set(properties) == {
        "data",
        "total_count",
        "page",
        "page_size",
        "total_pages",
    }
    assert _response_properties(client, "/items/archived")[0] == name
    for path in ("/items", "/items/archived"):
        assert client.get(path).json()["total_count"] == 2


def test_a_view_without_a_model_has_no_list_response_yet():
    class ApiView(fr.AsyncRestView):
        pass

    assert not hasattr(ApiView, "list_response_schema")


# --- an own class -------------------------------------------------------------


def test_an_alias_renames_a_key_of_the_list_response(client, Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = ArticleList

    fr.include_view(client.app, ItemView)
    _fill(client)

    name, properties = _response_properties(client)
    assert name == "ArticleList"
    assert set(properties) == {"articles", "articlesCount"}
    body = client.get("/items").json()
    assert set(body) == {"articles", "articlesCount"}
    assert body["articlesCount"] == 2
    assert [row["name"] for row in body["articles"]] == ["a", "b"]


@pytest.mark.parametrize("item", [ItemSummary, PlainSummary])
def test_the_list_can_have_smaller_items_than_the_other_routes(client, Item, item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = NumberedEnvelope[item]

    fr.include_view(client.app, ItemView)
    _fill(client)

    spec = client.get("/openapi.json").json()
    name = _response_schema(client)["$ref"].rsplit("/", 1)[-1]
    data = spec["components"]["schemas"][name]["properties"]["data"]
    item_name = data["items"]["$ref"].rsplit("/", 1)[-1]
    assert item_name == item.__name__

    body = client.get("/items").json()
    assert body["total_count"] == 2
    assert body["page"] == 1
    assert [set(row) for row in body["data"]] == [set(item.model_fields)] * 2
    assert "note" in client.get("/items/1").json()


def test_a_custom_route_with_the_marker_fills_the_class_too(client, Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = ArticleList

        @fr.get("/archived")
        async def archived(self, list_params) -> ListResponseSchema:
            return await self.handle_get_many(list_params)

    fr.include_view(client.app, ItemView)
    _fill(client)

    assert _response_properties(client, "/items/archived")[0] == "ArticleList"
    assert client.get("/items/archived").json()["articlesCount"] == 2


def test_a_list_response_from_an_own_to_single_response_fills_the_items(client, Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = NumberedEnvelope[ItemSummary]

        def to_single_response(self, obj):
            response = super().to_single_response(obj)
            return response.model_copy(update={"name": response.name.upper()})

    fr.include_view(client.app, ItemView)
    _fill(client)

    assert [row["name"] for row in client.get("/items").json()["data"]] == ["A", "B"]


# --- a function ---------------------------------------------------------------


def _keyed_list(view):
    key = view.list_key
    return pydantic.create_model(
        f"{key.title()}List",
        data=(list[view.response_schema], pydantic.Field(alias=key)),
        total_count=(int, pydantic.Field(alias=f"{key}Count")),
    )


def test_a_function_on_a_base_view_gives_each_view_its_own_keys(client, Item):
    class Note(fr.IDMixin, SharedBase):
        text: Mapped[str]

    class ApiView(fr.AsyncRestView):
        list_response_schema = SchemaType(_keyed_list)

    class ItemView(ApiView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_key = "items"

    class NoteView(ApiView):
        prefix = "/notes"
        model = Note
        list_key = "notes"

    fr.include_view(client.app, ItemView)
    fr.include_view(client.app, NoteView)
    _fill(client)

    assert _response_properties(client)[0] == "ItemsList"
    assert set(_response_properties(client)[1]) == {"items", "itemsCount"}
    assert _response_properties(client, "/notes")[0] == "NotesList"
    assert client.get("/items").json()["itemsCount"] == 2
    assert client.get("/notes").json() == {"notes": [], "notesCount": 0}


def test_the_function_runs_once_for_each_view(Item):
    calls = []

    def page(view):
        calls.append(view)
        return ArticleList

    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = SchemaType(page)

    fr.include_view(FastAPI(), ItemView)
    assert ItemView.list_response_schema is ArticleList
    assert calls == [ItemView]


def test_the_decorator_form(client, Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_key = "items"

        @SchemaType
        def list_response_schema(view):
            return _keyed_list(view)

    fr.include_view(client.app, ItemView)
    _fill(client)

    assert client.get("/items").json()["itemsCount"] == 2


# --- what Restly checks at registration ---------------------------------------


def test_a_class_that_the_default_route_cannot_fill_raises(Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = Rows

    with pytest.raises(RestlyConfigurationError) as caught:
        fr.include_view(FastAPI(), ItemView)
    message = str(caught.value)
    assert "ItemView.list_response_schema" in message
    assert "Rows" in message
    assert "rows" in message
    assert "get_many_endpoint" in message


def test_a_class_from_a_function_is_checked_too(Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = SchemaType(lambda view: Rows)

    with pytest.raises(RestlyConfigurationError, match="rows"):
        fr.include_view(FastAPI(), ItemView)


def test_a_class_with_page_fields_on_a_view_without_pages_raises(Item):
    class ParentView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = NumberedEnvelope[ItemSummary]

    class ItemView(ParentView):
        pagination = None

    fr.include_view(FastAPI(), ParentView)
    with pytest.raises(RestlyConfigurationError, match="page"):
        fr.include_view(FastAPI(), ItemView)


@pytest.mark.parametrize("extra", ["allow", "forbid"])
def test_a_class_that_sets_extra_raises(Item, extra):
    class Page(pydantic.BaseModel):
        model_config = pydantic.ConfigDict(extra=extra)
        data: list[ItemResponse]

    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = Page

    with pytest.raises(RestlyConfigurationError, match="extra"):
        fr.include_view(FastAPI(), ItemView)


def test_another_type_on_the_default_route_raises(Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = dict[str, ItemResponse]

    with pytest.raises(RestlyConfigurationError) as caught:
        fr.include_view(FastAPI(), ItemView)
    message = str(caught.value)
    assert "ItemView.list_response_schema" in message
    assert "get_many_endpoint" in message


def test_an_item_field_that_the_response_class_lacks_raises(Item):
    class Extra(fr.IDSchema):
        name: str
        secret: str

    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = NumberedEnvelope[Extra]

    with pytest.raises(RestlyConfigurationError) as caught:
        fr.include_view(FastAPI(), ItemView)
    message = str(caught.value)
    assert "secret" in message
    assert "ItemResponse" in message


def test_an_item_field_with_a_default_or_from_a_property_is_fine(client, Item):
    class Named(ItemSchema):
        @pydantic.computed_field
        @property
        def label(self) -> str:
            return self.name.upper()

        @property
        def plain(self) -> str:
            return "plain"

    class Summary(pydantic.BaseModel):
        label: str
        plain: str
        tag: str = "none"

    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = Named
        list_response_schema = NumberedEnvelope[Summary]

    fr.include_view(client.app, ItemView)
    _fill(client)

    assert client.get("/items").json()["data"][0] == {
        "label": "A",
        "plain": "plain",
        "tag": "none",
    }


def test_items_of_any_type_are_not_checked(client, Item):
    class Page(pydantic.BaseModel):
        data: list[Any]
        total_count: int

    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = Page

    fr.include_view(client.app, ItemView)
    _fill(client)

    assert client.get("/items").json()["total_count"] == 2


def test_a_replaced_endpoint_can_use_any_class(client, Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = Rows

        @fr.get("/")
        async def get_many_endpoint(self, list_params) -> ListResponseSchema:
            result = await self.handle_get_many(list_params)
            return Rows(rows=[self.to_single_response(row) for row in result.objects])

    fr.include_view(client.app, ItemView)
    _fill(client)

    assert _response_properties(client)[0] == "Rows"
    assert len(client.get("/items").json()["rows"]) == 2


def test_a_replaced_endpoint_can_use_a_bare_list(client, Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = list[ItemSummary]

        @fr.get("/")
        async def get_many_endpoint(self, list_params) -> ListResponseSchema:
            result = await self.handle_get_many(list_params)
            return [
                ItemSummary.model_validate(row, from_attributes=True)
                for row in result.objects
            ]

    fr.include_view(client.app, ItemView)
    _fill(client)

    schema = _response_schema(client)
    assert schema["type"] == "array"
    assert schema["items"]["$ref"].endswith("/ItemSummary")
    assert [set(row) for row in client.get("/items").json()] == [{"id", "name"}] * 2


def test_an_own_to_list_response_can_use_any_class(client, Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = Rows

        def to_list_response(self, list_result: ListResult):
            rows = [self.to_single_response(row) for row in list_result.objects]
            return Rows(rows=rows)

    fr.include_view(client.app, ItemView)
    _fill(client)

    assert len(client.get("/items").json()["rows"]) == 2


def test_an_excluded_list_route_can_use_any_class(Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = Rows
        exclude_routes = ("get_many_endpoint",)

    fr.include_view(FastAPI(), ItemView)


# --- subclasses ---------------------------------------------------------------


class Page(pydantic.BaseModel):
    data: list[Any]
    total_count: int


def test_a_subclass_keeps_the_class_unless_it_has_a_new_response_class(Item):
    class Other(fr.IDSchema):
        name: str

    class ParentView(fr.AsyncRestView):
        model = Item
        schema = ItemSchema
        list_response_schema = Page

    class SameView(ParentView):
        pass

    class PagesView(ParentView):
        pagination = fr.NumberedPagination(default_page_size=1)

    class NoPagesView(ParentView):
        pagination = None

    class NewSchemaView(ParentView):
        schema = Other

    class NewResponseView(ParentView):
        response_schema = Other

    assert SameView.list_response_schema is Page
    assert PagesView.list_response_schema is Page
    assert NoPagesView.list_response_schema is Page
    assert NewSchemaView.list_response_schema.__name__ == "OtherListResponse"
    assert NewResponseView.list_response_schema.__name__ == "OtherListResponse"


def test_a_stale_class_gives_back_the_function_of_the_base_view(Item):
    def own(view):
        return NumberedEnvelope[view.response_schema]

    class ApiView(fr.AsyncRestView):
        list_response_schema = SchemaType(own)

    class ParentView(ApiView):
        model = Item
        schema = ItemSchema
        list_response_schema = Page

    class Other(fr.IDSchema):
        name: str

    class ItemView(ParentView):
        schema = Other

    assert ItemView.list_response_schema is NumberedEnvelope[ItemView.response_schema]


# --- the names in OpenAPI -----------------------------------------------------


def test_a_name_clash_says_the_view_declares_the_class(Item):
    def first():
        class Page(pydantic.BaseModel):
            data: list[ItemResponse]

        return Page

    def second():
        class Page(pydantic.BaseModel):
            data: list[ItemResponse]
            total_count: int

        return Page

    class FirstView(fr.AsyncRestView):
        prefix = "/first"
        model = Item
        schema = ItemSchema
        list_response_schema = first()

    class SecondView(fr.AsyncRestView):
        prefix = "/second"
        model = Item
        schema = ItemSchema
        list_response_schema = second()

    app = FastAPI()
    fr.include_view(app, FirstView)
    fr.include_view(app, SecondView)
    with pytest.warns(fr.exc.RestlyDuplicateSchemaNameWarning) as record:
        app.openapi()

    message = str(record[0].message)
    assert "More than one class has the name" in message
    assert "the list response of FirstView" in message
    assert "that Restly generates" not in message


# --- react-admin --------------------------------------------------------------


@pytest.fixture
def sync_client(sync_db) -> Iterator[RestlyTestClient]:
    yield RestlyTestClient(FastAPI())


@pytest.fixture(params=["async", "sync"])
def react_admin(request):
    """A react-admin setup, on the async view and on the sync view."""
    if request.param == "async":
        client = request.getfixturevalue("client")
        return client, fr.AsyncReactAdminView, create_tables, _async_archived

    engine, _ = request.getfixturevalue("sync_db")
    client = request.getfixturevalue("sync_client")
    return (
        client,
        fr.ReactAdminView,
        lambda: SharedBase.metadata.create_all(engine),
        _sync_archived,
    )


def _async_archived():
    @fr.get("/archived")
    async def archived(self, list_params) -> ListResponseSchema:
        return await self.handle_get_many(list_params)

    return archived


def _sync_archived():
    @fr.get("/archived")
    def archived(self, list_params) -> ListResponseSchema:
        return self.handle_get_many(list_params)

    return archived


def _fill_with(client, tables):
    tables()
    for name in "ab":
        client.post("/items", json={"name": name, "note": "long text"})


def test_a_react_admin_list_has_the_array_in_the_spec(react_admin, Item):
    client, base, tables, make_archived = react_admin

    class ItemView(base):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        archived = make_archived()

    fr.include_view(client.app, ItemView)
    _fill_with(client, tables)

    assert ItemView.list_response_schema == list[ItemView.response_schema]
    for path in ("/items", "/items/archived"):
        schema = _response_schema(client, path)
        assert schema["type"] == "array"
        assert schema["items"]["$ref"].endswith("/ItemResponse")

        response = client.get(path)
        assert [row["name"] for row in response.json()] == ["a", "b"]
        assert set(response.json()[0]) == {"id", "name", "note"}
        assert response.headers["content-range"] == "items 0-1/2"
        assert response.headers["content-type"] == "application/json"


def test_a_react_admin_list_can_have_smaller_items(react_admin, Item):
    client, base, tables, _ = react_admin

    class ItemView(base):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = list[ItemSummary]

    fr.include_view(client.app, ItemView)
    _fill_with(client, tables)

    assert _response_schema(client)["items"]["$ref"].endswith("/ItemSummary")
    response = client.get("/items")
    assert response.json() == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    assert response.headers["content-range"] == "items 0-1/2"
    assert "note" in client.get("/items/1").json()


def test_a_react_admin_list_writes_an_alias_as_the_class_says(react_admin, Item):
    client, base, tables, _ = react_admin

    class Aliased(pydantic.BaseModel):
        name: str = pydantic.Field(alias="title")

    class ItemView(base):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = list[Aliased]

    fr.include_view(client.app, ItemView)
    _fill_with(client, tables)

    assert client.get("/items").json() == [{"title": "a"}, {"title": "b"}]


def test_a_react_admin_list_response_must_be_a_list(react_admin, Item):
    client, base, _, _ = react_admin

    class ItemView(base):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = NumberedEnvelope[ItemSummary]

    with pytest.raises(RestlyConfigurationError) as caught:
        fr.include_view(client.app, ItemView)
    assert "ItemView.list_response_schema" in str(caught.value)
    assert "JSON array" in str(caught.value)


def test_a_replaced_react_admin_endpoint_can_use_any_class(client, Item):
    class ItemView(fr.AsyncReactAdminView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = NumberedEnvelope[ItemSummary]

        @fr.get("/")
        async def get_many_endpoint(self, list_params) -> ListResponseSchema:
            result = await self.handle_get_many(list_params)
            return NumberedEnvelope[ItemSummary](
                data=[],
                total_count=result.total_count,
                page=1,
                page_size=1,
                total_pages=0,
            )

    fr.include_view(client.app, ItemView)
    create_tables()

    assert client.get("/items").json()["total_count"] == 0


def test_a_stale_class_on_a_react_admin_view_gives_the_list_back(Item):
    class Other(fr.IDSchema):
        name: str

    class ParentView(fr.AsyncReactAdminView):
        model = Item
        schema = ItemSchema
        list_response_schema = list[ItemSummary]

    class ItemView(ParentView):
        schema = Other

    assert ItemView.list_response_schema == list[ItemView.response_schema]


def test_a_single_response_marker_still_works_next_to_the_list(client, Item):
    class ItemView(fr.AsyncReactAdminView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.get("/first")
        async def first(self) -> ResponseSchema:
            return await self.handle_get_one(1)

    fr.include_view(client.app, ItemView)
    _fill(client)

    assert client.get("/items/first").json()["name"] == "a"


# --- the rest of the list response ---------------------------------------------


def test_a_generic_class_without_its_type_raises(client, Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = NumberedEnvelope

    with pytest.raises(RestlyConfigurationError, match="NumberedEnvelope.*type"):
        fr.include_view(client.app, ItemView)


def test_none_raises_also_with_a_replaced_endpoint(client, Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = None

        @fr.get("/")
        async def get_many_endpoint(self, list_params) -> ListResponseSchema:
            return []

    with pytest.raises(RestlyConfigurationError, match="list_response_schema is None"):
        fr.include_view(client.app, ItemView)


def test_a_function_that_returns_nothing_raises_also_with_an_override(client, Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = SchemaType(lambda view: None)

        def to_list_response(self, list_result):
            return []

    with pytest.raises(RestlyConfigurationError, match="None"):
        fr.include_view(client.app, ItemView)


def test_a_list_type_on_the_default_route(client, Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = list[ItemSummary]

    fr.include_view(client.app, ItemView)
    _fill(client)

    schema = _response_schema(client)
    assert schema["type"] == "array"
    assert schema["items"] == {"$ref": "#/components/schemas/ItemSummary"}
    assert client.get("/items").json() == [
        {"id": 1, "name": "a"},
        {"id": 2, "name": "b"},
    ]


def test_a_list_type_with_a_replaced_endpoint_that_returns_a_list_result(client, Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = list[ItemSummary]

        @fr.get("/")
        async def get_many_endpoint(self, list_params) -> ListResponseSchema:
            return await self.handle_get_many(list_params)

    fr.include_view(client.app, ItemView)
    _fill(client)

    assert client.get("/items").json() == [
        {"id": 1, "name": "a"},
        {"id": 2, "name": "b"},
    ]


class WideSummary(fr.IDSchema):
    name: str
    nope: str


@pytest.mark.parametrize(
    "list_response",
    [
        list[WideSummary],
        collections.abc.Sequence[WideSummary],
        NumberedEnvelope[WideSummary],
    ],
    ids=["list", "sequence", "envelope"],
)
def test_an_item_field_that_the_response_lacks_raises(client, Item, list_response):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = list_response

    with pytest.raises(RestlyConfigurationError, match="nope"):
        fr.include_view(client.app, ItemView)


def test_an_item_field_that_the_response_lacks_raises_on_react_admin(react_admin, Item):
    client, base, _, _ = react_admin

    class ItemView(base):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = list[WideSummary]

    with pytest.raises(RestlyConfigurationError, match="nope"):
        fr.include_view(client.app, ItemView)


def test_a_before_validator_can_fill_an_item_field(client, Item):
    class Filled(fr.IDSchema):
        name: str
        label: str

        @pydantic.model_validator(mode="before")
        @classmethod
        def fill_label(cls, data):
            return {"id": data.id, "name": data.name, "label": data.name.upper()}

    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = NumberedEnvelope[Filled]

    fr.include_view(client.app, ItemView)
    _fill(client)

    assert client.get("/items").json()["data"][0]["label"] == "A"


def test_react_admin_writes_only_the_public_fields_of_an_override(react_admin, Item):
    client, base, create, _ = react_admin

    class Secret(fr.IDSchema):
        name: str
        note: fr.WriteOnly[str]

    class ItemView(base):
        prefix = "/items"
        model = Item
        schema = Secret

        def to_single_response(self, obj):
            return Secret(id=obj.id, name=obj.name, note=obj.note)

    fr.include_view(client.app, ItemView)
    create()
    client.post("/items", json={"name": "a", "note": "SECRET"})

    response = client.get("/items")
    assert response.json() == [{"id": 1, "name": "a"}]


def test_the_recipe_with_a_map_and_a_replaced_endpoint(client, Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = dict[int, str]

        @fr.get("/")
        async def get_many_endpoint(self, list_params) -> ListResponseSchema:
            result = await self.handle_get_many(list_params)
            return {item.id: item.name for item in result.objects}

    fr.include_view(client.app, ItemView)
    _fill(client)

    assert client.get("/items").json() == {"1": "a", "2": "b"}
    assert _response_schema(client)["additionalProperties"]["type"] == "string"


class SummaryDict(TypedDict):
    name: str


@dataclasses.dataclass
class SummaryData:
    name: str


class SummaryTuple(NamedTuple):
    name: str


@pytest.mark.parametrize(
    "shape",
    [
        list[dict[str, Any]],
        list[int],
        list[SummaryDict],
        list[SummaryData],
        list[SummaryTuple],
        NumberedEnvelope[dict[str, Any]],
    ],
    ids=str,
)
def test_items_that_restly_cannot_fill_raise_at_registration(client, Item, shape):
    """Before, an empty table gave a 200, and the first row a 500."""

    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = shape

    with pytest.raises(RestlyConfigurationError, match="Pydantic class"):
        fr.include_view(client.app, ItemView)


def test_items_that_restly_cannot_fill_work_with_a_replaced_endpoint(client, Item):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = list[int]

        @fr.get("/")
        async def get_many_endpoint(self, list_params) -> ListResponseSchema:
            result = await self.handle_get_many(list_params)
            return [item.id for item in result.objects]

    fr.include_view(client.app, ItemView)
    _fill(client)

    assert client.get("/items").json() == [1, 2]


def test_a_list_that_allows_none_raises_at_registration(client, Item):
    """Before, each GET / failed, also on an empty table."""

    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = list[ItemSummary] | None

    with pytest.raises(RestlyConfigurationError, match="list type"):
        fr.include_view(client.app, ItemView)


@pytest.mark.parametrize(
    "shape", [list[ItemSummary | None], list[Annotated[ItemSummary, "note"]]], ids=str
)
def test_a_wrapped_item_class_still_gets_filled(client, Item, shape):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = shape

    fr.include_view(client.app, ItemView)
    _fill(client)

    assert [item["name"] for item in client.get("/items").json()] == ["a", "b"]


@pytest.mark.parametrize(
    "shape", [list[Extra | None], list[Annotated[Extra, "note"]]], ids=str
)
def test_a_wrapped_item_class_gets_the_missing_field_check(client, Item, shape):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = shape

    with pytest.raises(RestlyConfigurationError, match="extra"):
        fr.include_view(client.app, ItemView)


def test_a_class_without_a_data_field_raises_at_registration(client, Item):
    """Before, the list was always empty, and nothing said why."""

    class Renamed(pydantic.BaseModel):
        rows: list[ItemResponse] = []

    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        list_response_schema = Renamed

    with pytest.raises(RestlyConfigurationError, match="data"):
        fr.include_view(client.app, ItemView)


def test_a_pagination_envelope_without_a_data_field_raises():
    T = TypeVar("T")

    class Renamed(pydantic.BaseModel, Generic[T]):
        rows: list[T] = []

    with pytest.raises(RestlyConfigurationError, match="no field named data"):
        fr.views.NumberedPagination(envelope=Renamed)
