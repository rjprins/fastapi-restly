"""``fr.OffsetPagination``: a list that pages with ``limit`` and ``offset``.

Each behaviour of a request runs on an async view and on a sync view.
"""

from typing import Any, Generic, TypeVar

import pydantic
import pytest
from fastapi import FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Mapped
from starlette.datastructures import QueryParams

import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError
from fastapi_restly.query import apply_list_params, derive_list_params_schema
from fastapi_restly.testing import RestlyTestClient
from fastapi_restly.views import ListResponseSchema

from ._models import SharedBase
from .conftest import create_tables

T = TypeVar("T")

LARGEST_OFFSET = 2**63 - 1


class Lists:
    """A client, and the view base that goes with it."""

    def __init__(self, client: Any, view_base: type, create: Any) -> None:
        self.client = client
        self.view_base = view_base
        self.create = create

    def view(self, *, rows: int = 5, base: Any = None, **settings: Any) -> Any:
        """Register ``/things`` with ``settings``, and add ``rows`` rows named
        ``Thing 0`` and up, each of size ``rows - number``."""

        class Thing(fr.IDMixin, SharedBase):
            name: Mapped[str]
            size: Mapped[int]

        class ThingSchema(fr.IDSchema):
            name: str
            size: int

        view_cls = fr.include_view(
            self.client.app,
            type(
                "ThingView",
                (base or self.view_base,),
                {
                    "prefix": "/things",
                    "model": Thing,
                    "schema": ThingSchema,
                    **settings,
                },
            ),
        )
        self.create()
        for number in range(rows):
            self.client.post(
                "/things", json={"name": f"Thing {number}", "size": rows - number}
            )
        return view_cls

    def names(self, **params: Any) -> list[str]:
        body = self.client.get("/things", params=params).json()
        return [row["name"] for row in body["data"]]


@pytest.fixture(params=["async", "sync"])
def lists(request: pytest.FixtureRequest) -> Lists:
    if request.param == "async":
        return Lists(request.getfixturevalue("client"), fr.AsyncRestView, create_tables)
    engine, _ = request.getfixturevalue("sync_db")
    return Lists(
        RestlyTestClient(FastAPI()),
        fr.RestView,
        lambda: SharedBase.metadata.create_all(engine),
    )


def _operation(client: Any, path: str = "/things") -> tuple[dict[str, Any], Any]:
    spec = client.app.openapi()
    return spec, spec["paths"][path]["get"]


def _parameter(operation: Any, name: str) -> dict[str, Any]:
    return next(p for p in operation["parameters"] if p["name"] == name)


def _response_component(spec: dict[str, Any], operation: Any) -> dict[str, Any]:
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]
    return spec["components"]["schemas"][
        ref["$ref"].removeprefix("#/components/schemas/")
    ]


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


def test_limit_and_offset_choose_the_rows(lists):
    lists.view(pagination=fr.OffsetPagination())

    assert lists.names(limit=2) == ["Thing 0", "Thing 1"]
    assert lists.names(limit=2, offset=2) == ["Thing 2", "Thing 3"]
    assert lists.names(offset=4) == ["Thing 4"]


def test_the_envelope_says_what_the_client_got(lists):
    lists.view(pagination=fr.OffsetPagination())

    body = lists.client.get("/things", params={"limit": 2, "offset": 3}).json()
    assert {key: body[key] for key in body if key != "data"} == {
        "total_count": 5,
        "limit": 2,
        "offset": 3,
    }
    assert len(body["data"]) == 2


def test_the_defaults_are_those_of_numbered_pagination(lists):
    settings = fr.OffsetPagination()
    assert settings.default_limit == fr.NumberedPagination().default_page_size
    assert settings.max_limit == fr.NumberedPagination().max_page_size
    assert settings.max_offset is None
    assert settings.envelope is fr.views.OffsetEnvelope

    lists.view(pagination=settings, rows=60)

    body = lists.client.get("/things").json()
    assert len(body["data"]) == 50
    assert {key: body[key] for key in body if key != "data"} == {
        "total_count": 60,
        "limit": 50,
        "offset": 0,
    }
    lists.client.get("/things", params={"limit": 1001}, assert_status_code=422)


def test_the_count_is_every_matching_row(lists):
    """The total does not depend on the limit. It follows the filters."""
    lists.view(pagination=fr.OffsetPagination())

    body = lists.client.get("/things", params={"limit": 1}).json()
    assert (len(body["data"]), body["total_count"]) == (1, 5)

    body = lists.client.get("/things", params={"limit": 1, "size__gte": 4}).json()
    assert (len(body["data"]), body["total_count"]) == (1, 2)


def test_the_count_stays_inside_the_scope(lists):
    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ThingSchema(fr.IDSchema):
        name: str

    fr.include_view(
        lists.client.app,
        type(
            "ThingView",
            (lists.view_base,),
            {
                "prefix": "/things",
                "model": Thing,
                "schema": ThingSchema,
                "pagination": fr.OffsetPagination(),
                "scope": fr.clauses.where_clause(Thing.name != "hidden"),
            },
        ),
    )
    lists.create()
    for name in ("a", "hidden", "b"):
        lists.client.post("/things", json={"name": name})

    body = lists.client.get("/things", params={"limit": 1}).json()
    assert (len(body["data"]), body["total_count"]) == (1, 2)


def test_an_offset_past_the_end_is_an_empty_list_with_the_total(lists):
    lists.view(pagination=fr.OffsetPagination())

    body = lists.client.get("/things", params={"offset": 100}).json()
    assert body == {"data": [], "total_count": 5, "limit": 50, "offset": 100}


def test_equal_sort_values_stay_in_one_order_across_requests(lists):
    """The primary key comes after the sort, so no row is lost or repeated."""
    lists.view(pagination=fr.OffsetPagination())
    for number in range(4):
        lists.client.post("/things", json={"name": f"Same {number}", "size": 0})

    params = {"sort": "size", "limit": 2}
    assert lists.names(**params) == ["Same 0", "Same 1"]
    assert lists.names(**params, offset=2) == ["Same 2", "Same 3"]


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": -1},
        {"limit": 101},
        {"limit": "many"},
        {"limit": ""},
        {"offset": -1},
        {"offset": "far"},
        {"offset": LARGEST_OFFSET + 1},
        {"offset": 10**30},
        {"page": 1},
        {"page_size": 10},
    ],
)
def test_a_bad_value_or_a_key_of_page_numbers_returns_422(lists, params):
    lists.view(pagination=fr.OffsetPagination(max_limit=100))

    lists.client.get("/things", params=params, assert_status_code=422)


def test_the_cap_and_the_edges_of_the_values(lists):
    lists.view(pagination=fr.OffsetPagination(max_limit=3, default_limit=2))

    assert len(lists.names()) == 2
    assert len(lists.names(limit=1)) == 1
    assert len(lists.names(limit=3)) == 3
    body = lists.client.get("/things", params={"offset": LARGEST_OFFSET}).json()
    assert (body["data"], body["total_count"]) == ([], 5)


def test_max_offset(lists):
    lists.view(pagination=fr.OffsetPagination(max_offset=3))

    assert lists.names(offset=3) == ["Thing 3", "Thing 4"]
    lists.client.get("/things", params={"offset": 4}, assert_status_code=422)
    _, operation = _operation(lists.client)
    offset = _parameter(operation, "offset")
    assert offset["schema"]["maximum"] == 3
    assert "At most 3." in offset["description"]


def test_a_custom_route_uses_the_same_pages(lists):
    """A route on the view reads the list params and sends the list response
    of the view, as the RealWorld feed does."""

    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ThingSchema(fr.IDSchema):
        name: str

    if lists.view_base is fr.AsyncRestView:

        class Routes(lists.view_base):
            @fr.get("/first")
            async def first(self, list_params) -> ListResponseSchema:
                return await self.get_many(list_params, where=Thing.name != "b")

    else:

        class Routes(lists.view_base):
            @fr.get("/first")
            def first(self, list_params) -> ListResponseSchema:
                return self.get_many(list_params, where=Thing.name != "b")

    fr.include_view(
        lists.client.app,
        type(
            "ThingView",
            (Routes,),
            {
                "prefix": "/things",
                "model": Thing,
                "schema": ThingSchema,
                "pagination": fr.OffsetPagination(default_limit=1),
            },
        ),
    )
    lists.create()
    for name in ("a", "b", "c"):
        lists.client.post("/things", json={"name": name})

    body = lists.client.get("/things/first", params={"offset": 1}).json()
    assert body == {
        "data": [{"id": 3, "name": "c"}],
        "total_count": 2,
        "limit": 1,
        "offset": 1,
    }


def test_a_mapping_as_list_params_goes_through_the_same_checks(lists):
    if lists.view_base is fr.AsyncRestView:

        class Routes(lists.view_base):
            @fr.get("/two")
            async def two(self) -> ListResponseSchema:
                return await self.get_many({"limit": "2", "offset": "1"})

    else:

        class Routes(lists.view_base):
            @fr.get("/two")
            def two(self) -> ListResponseSchema:
                return self.get_many({"limit": "2", "offset": "1"})

    lists.view(base=Routes, pagination=fr.OffsetPagination())

    body = lists.client.get("/things/two").json()
    assert [row["name"] for row in body["data"]] == ["Thing 1", "Thing 2"]
    assert (body["total_count"], body["limit"], body["offset"]) == (5, 2, 1)


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------


def test_openapi_shows_the_parameters_with_their_bounds(lists):
    lists.view(pagination=fr.OffsetPagination(default_limit=20, max_limit=100))

    _, operation = _operation(lists.client)
    limit = _parameter(operation, "limit")
    assert limit["in"] == "query"
    assert limit["required"] is False
    assert limit["schema"]["type"] == "integer"
    assert limit["schema"]["minimum"] == 1
    assert limit["schema"]["maximum"] == 100
    assert limit["schema"]["default"] == 20
    offset = _parameter(operation, "offset")
    assert offset["required"] is False
    assert offset["schema"]["type"] == "integer"
    assert offset["schema"]["minimum"] == 0
    assert offset["schema"]["maximum"] == LARGEST_OFFSET
    assert offset["schema"]["default"] == 0
    assert "page" not in {p["name"] for p in operation["parameters"]}
    assert "page_size" not in {p["name"] for p in operation["parameters"]}


def test_openapi_shows_the_response_that_the_route_sends(lists):
    lists.view(pagination=fr.OffsetPagination())

    spec, operation = _operation(lists.client)
    component = _response_component(spec, operation)
    assert component["title"] == "ThingListResponse"
    assert set(component["properties"]) == {"data", "total_count", "limit", "offset"}
    assert set(component["required"]) == {"data", "total_count", "limit", "offset"}
    assert set(lists.client.get("/things").json()) == set(component["properties"])


def test_openapi_follows_renamed_parameters_and_aliases(lists):
    class Page(BaseModel, Generic[T]):
        data: list[T] = Field(alias="items")
        total_count: int = Field(alias="count")
        offset: int = Field(alias="skip")

    lists.view(
        pagination=fr.OffsetPagination(
            limit_query_param="take", offset_query_param="skip", envelope=Page
        )
    )

    spec, operation = _operation(lists.client)
    names = {p["name"] for p in operation["parameters"]}
    assert {"take", "skip"} <= names
    assert not {"limit", "offset"} & names
    component = _response_component(spec, operation)
    assert set(component["properties"]) == {"items", "count", "skip"}
    assert set(lists.client.get("/things").json()) == {"items", "count", "skip"}


# ---------------------------------------------------------------------------
# Renamed parameters and the envelope
# ---------------------------------------------------------------------------


def test_renamed_parameters(lists):
    lists.view(pagination=fr.OffsetPagination(offset_query_param="skip"))

    body = lists.client.get("/things", params={"limit": 1, "skip": 1}).json()
    # the JSON keeps its names: a name in the URL does not rename a field
    assert body == {
        "data": [{"id": 2, "name": "Thing 1", "size": 4}],
        "total_count": 5,
        "limit": 1,
        "offset": 1,
    }
    lists.client.get("/things", params={"offset": 1}, assert_status_code=422)


def test_a_list_response_schema_gets_the_values_by_name(lists):
    """RealWorld's list: other names, and no limit or offset in the body."""

    class Articles(BaseModel):
        data: list[Any] = Field(alias="articles")
        total_count: int = Field(alias="articlesCount")

    lists.view(
        pagination=fr.OffsetPagination(default_limit=20, max_limit=100),
        list_response_schema=Articles,
    )

    body = lists.client.get("/things", params={"limit": 1}).json()
    assert body["articlesCount"] == 5
    assert len(body["articles"]) == 1
    assert set(body) == {"articles", "articlesCount"}


def test_a_list_type_as_the_response_has_no_page_values(lists):
    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ThingSchema(fr.IDSchema):
        name: str

    class Row(BaseModel):
        name: str

    fr.include_view(
        lists.client.app,
        type(
            "ThingView",
            (lists.view_base,),
            {
                "prefix": "/things",
                "model": Thing,
                "schema": ThingSchema,
                "list_response_schema": list[Row],
                "pagination": fr.OffsetPagination(),
            },
        ),
    )
    lists.create()
    for name in ("a", "b"):
        lists.client.post("/things", json={"name": name})

    assert lists.client.get("/things", params={"offset": 1}).json() == [{"name": "b"}]


def test_a_list_response_schema_with_page_numbers_fails_at_registration():
    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ThingSchema(fr.IDSchema):
        name: str

    class ThingView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing
        schema = ThingSchema
        list_response_schema = fr.views.NumberedEnvelope[ThingSchema]
        pagination = fr.OffsetPagination()

    with pytest.raises(RestlyConfigurationError, match="page: Field required"):
        fr.include_view(FastAPI(), ThingView)


# ---------------------------------------------------------------------------
# The settings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"max_limit": 0}, "OffsetPagination.max_limit must be an int of at least 1"),
        ({"max_limit": True}, "OffsetPagination.max_limit must be an int"),
        ({"default_limit": 0}, r"OffsetPagination.default_limit must be an int in"),
        ({"default_limit": 51, "max_limit": 50}, r"in \[1, 50\], got 51"),
        ({"default_limit": None}, "set 'pagination = None'"),
        ({"max_offset": 0}, "OffsetPagination.max_offset must be an int of at least 1"),
        ({"limit_query_param": ""}, "limit_query_param must be a non-empty string"),
        ({"offset_query_param": "a__b"}, "'__' is reserved for filter operators"),
        ({"offset_query_param": "a.b"}, "'.' for relation traversal"),
        ({"offset_query_param": "_skip"}, "cannot start with '_'"),
        ({"offset_query_param": "copy"}, "BaseModel attribute"),
        ({"offset_query_param": "limit"}, "query parameters must differ"),
        ({"envelope": dict}, "OffsetPagination.envelope must be a Pydantic model"),
    ],
)
def test_invalid_settings_fail_when_created(settings, message):
    with pytest.raises(RestlyConfigurationError, match=message):
        fr.OffsetPagination(**settings)


def test_replace_returns_a_checked_copy():
    settings = fr.OffsetPagination(max_limit=100)
    changed = settings.replace(default_limit=10)
    assert (changed.default_limit, changed.max_limit) == (10, 100)
    assert settings.default_limit == 50
    with pytest.raises(RestlyConfigurationError, match="default_limit"):
        settings.replace(default_limit=101)


def test_the_settings_cannot_change_after_creation():
    settings = fr.OffsetPagination()
    with pytest.raises(AttributeError):
        settings.max_limit = 1  # type: ignore[misc]


def test_an_envelope_field_restly_cannot_fill_fails_when_created():
    class Page(BaseModel, Generic[T]):
        data: list[T]
        total_pages: int

    with pytest.raises(RestlyConfigurationError) as caught:
        fr.OffsetPagination(envelope=Page)
    assert "OffsetPagination.envelope Page cannot be filled" in str(caught.value)
    assert "data, total_count, limit, offset" in str(caught.value)


def test_pagination_is_the_base_of_every_style():
    for settings in (fr.NumberedPagination(), fr.OffsetPagination(), fr.NoPagination()):
        assert isinstance(settings, fr.Pagination)
    with pytest.raises(TypeError, match="abstract"):
        fr.Pagination()  # type: ignore[abstract]


def test_the_name_of_a_style_is_exported_in_both_places():
    assert fr.views.OffsetPagination is fr.OffsetPagination
    assert fr.views.Pagination is fr.Pagination
    assert issubclass(fr.views.OffsetEnvelope, fr.views.Envelope)
    assert issubclass(fr.views.NumberedEnvelope, fr.views.Envelope)
    assert not hasattr(fr.views, "PaginatedEnvelope")


# ---------------------------------------------------------------------------
# The names in the URL against the other keys
# ---------------------------------------------------------------------------


def _column_named(name: str) -> tuple[type, type]:
    model = type(
        "Thing", (fr.IDMixin, SharedBase), {"__annotations__": {name: Mapped[str]}}
    )
    schema = pydantic.create_model(name.title(), __base__=fr.IDSchema, **{name: str})
    return model, schema


@pytest.mark.parametrize("name", ["limit", "offset"])
def test_a_filter_named_like_a_parameter_fails_at_registration(name):
    model, schema = _column_named(name)

    class ThingView(fr.AsyncRestView):
        prefix = "/things"
        pagination = fr.OffsetPagination()

    view_cls = type("Named", (ThingView,), {"model": model, "schema": schema})
    with pytest.raises(ValueError, match=f"filter key '{name}': it collides"):
        fr.include_view(FastAPI(), view_cls)


def test_renaming_a_parameter_frees_its_name_for_a_filter(client):
    thing, thing_schema = _column_named("offset")

    @fr.include_view(client.app)
    class ThingView(fr.AsyncRestView):
        prefix = "/things"
        model = thing
        schema = thing_schema
        pagination = fr.OffsetPagination(offset_query_param="skip")

    create_tables()
    client.post("/things", json={"offset": "a"})
    client.post("/things", json={"offset": "b"})

    body = client.get("/things", params={"offset": "b", "skip": 0}).json()
    assert [row["offset"] for row in body["data"]] == ["b"]
    assert body["total_count"] == 1


def test_a_list_filter_named_like_a_parameter_fails_at_registration():
    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ThingSchema(fr.IDSchema):
        name: str

    class ThingView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing
        schema = ThingSchema
        pagination = fr.OffsetPagination()

        @fr.list_filter("limit")
        def filter_limit(self, value: int) -> ColumnElement[bool]:
            return Thing.id < value

    with pytest.raises(RestlyConfigurationError, match="limit"):
        fr.include_view(FastAPI(), ThingView)


def test_a_sort_parameter_named_like_a_parameter_fails_at_registration():
    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ThingSchema(fr.IDSchema):
        name: str

    class ThingView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing
        schema = ThingSchema
        sort_query_param = "limit"
        pagination = fr.OffsetPagination()

    with pytest.raises(RestlyConfigurationError, match="it is a page parameter"):
        fr.include_view(FastAPI(), ThingView)


# ---------------------------------------------------------------------------
# @fr.list_filter and the list settings
# ---------------------------------------------------------------------------


def test_list_filter_and_list_settings_work_with_the_pagination(lists):
    """All the list settings keep working: the reason that this list does not
    need list params of its own."""

    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]
        size: Mapped[int]

    class ThingSchema(fr.IDSchema):
        name: str
        size: int

    if lists.view_base is fr.AsyncRestView:

        class WithFilter(lists.view_base):
            @fr.list_filter("big")
            async def filter_big(self, value: bool) -> ColumnElement[bool]:
                return Thing.size >= 3 if value else Thing.size < 3

    else:

        class WithFilter(lists.view_base):
            @fr.list_filter("big")
            def filter_big(self, value: bool) -> ColumnElement[bool]:
                return Thing.size >= 3 if value else Thing.size < 3

    fr.include_view(
        lists.client.app,
        type(
            "ThingView",
            (WithFilter,),
            {
                "prefix": "/things",
                "model": Thing,
                "schema": ThingSchema,
                "pagination": fr.OffsetPagination(default_limit=2),
                "include_filters": ("name",),
                "include_sort_keys": ("size",),
                "sort_query_param": "order",
                "default_sort": "-size",
            },
        ),
    )
    lists.create()
    for size in range(5):
        lists.client.post("/things", json={"name": f"t{size}", "size": size})

    # the default sort, in pages
    assert [row["size"] for row in lists.client.get("/things").json()["data"]] == [4, 3]
    body = lists.client.get("/things", params={"offset": 2}).json()
    assert [row["size"] for row in body["data"]] == [2, 1]
    # the filter method: the count follows it
    body = lists.client.get("/things", params={"big": "true", "limit": 1}).json()
    assert (body["data"][0]["size"], body["total_count"]) == (4, 2)
    # the renamed sort parameter
    body = lists.client.get("/things", params={"order": "size", "limit": 1}).json()
    assert body["data"][0]["size"] == 0
    # the keys that the settings keep
    body = lists.client.get("/things", params={"name": "t3"}).json()
    assert body["total_count"] == 1
    lists.client.get("/things", params={"size": 3}, assert_status_code=422)
    lists.client.get("/things", params={"order": "name"}, assert_status_code=422)
    # OpenAPI shows all of it
    _, operation = _operation(lists.client)
    names = {p["name"] for p in operation["parameters"]}
    assert {"limit", "offset", "big", "name", "order"} <= names


def test_list_filter_can_use_the_name_when_the_pagination_differs():
    """``limit`` is free on a view with page numbers."""

    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ThingSchema(fr.IDSchema):
        name: str

    class ThingView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing
        schema = ThingSchema

        @fr.list_filter("limit")
        def filter_limit(self, value: int) -> ColumnElement[bool]:
            return Thing.id < value

    fr.include_view(FastAPI(), ThingView)
    assert "limit" in ThingView.list_params_schema.model_fields


# ---------------------------------------------------------------------------
# A base view that sets the pagination once
# ---------------------------------------------------------------------------


def test_a_base_view_sets_the_pagination_once(lists):
    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ThingSchema(fr.IDSchema):
        name: str

    shared = fr.OffsetPagination(
        offset_query_param="skip", default_limit=2, max_limit=3
    )
    app_view = type("AppView", (lists.view_base,), {"pagination": shared})

    def register(name: str, prefix: str, **settings: Any) -> None:
        fr.include_view(
            lists.client.app,
            type(
                name,
                (app_view,),
                {"prefix": prefix, "model": Thing, "schema": ThingSchema, **settings},
            ),
        )

    register("Inherits", "/inherits")
    register("Changes", "/changes", pagination=shared.replace(default_limit=1))
    register("Numbered", "/numbered", pagination=fr.NumberedPagination())
    register("Without", "/without", pagination=None)
    lists.create()
    for number in range(4):
        lists.client.post("/inherits", json={"name": str(number)})

    inherits = lists.client.get("/inherits", params={"skip": 1}).json()
    assert (len(inherits["data"]), inherits["limit"], inherits["offset"]) == (2, 2, 1)
    lists.client.get("/inherits", params={"limit": 4}, assert_status_code=422)

    changes = lists.client.get("/changes").json()
    assert (len(changes["data"]), changes["limit"]) == (1, 1)

    numbered = lists.client.get("/numbered", params={"page_size": 3}).json()
    assert (numbered["page"], numbered["page_size"]) == (1, 3)
    lists.client.get("/numbered", params={"skip": 1}, assert_status_code=422)

    assert len(lists.client.get("/without").json()["data"]) == 4
    lists.client.get("/without", params={"limit": 1}, assert_status_code=422)


def test_a_subclass_with_another_style_gets_its_own_list_params():
    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ThingSchema(fr.IDSchema):
        name: str

    class AppView(fr.AsyncRestView):
        pagination = fr.OffsetPagination()

    class ThingView(AppView):
        prefix = "/things"
        model = Thing
        schema = ThingSchema

    class NumberedView(ThingView):
        prefix = "/numbered"
        pagination = fr.NumberedPagination()

    app = FastAPI()
    fr.include_view(app, ThingView)
    fr.include_view(app, NumberedView)
    assert "limit" in ThingView.list_params_schema.model_fields
    assert "page" in NumberedView.list_params_schema.model_fields
    assert "limit" not in NumberedView.list_params_schema.model_fields


def test_derived_list_params_must_match_the_style_of_the_view():
    """Settings with the same names and values, but of another style, give
    other fields: the check does not compare only the values."""

    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ThingSchema(fr.IDSchema):
        name: str

    numbered = derive_list_params_schema(
        fr.schemas.derive_response_schema(ThingSchema),
        Thing,
        pagination=fr.NumberedPagination(
            page_query_param="offset", page_size_query_param="limit"
        ),
    )

    class ThingView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing
        schema = ThingSchema
        list_params_schema = numbered
        pagination = fr.OffsetPagination()

    with pytest.raises(RestlyConfigurationError, match="other pagination settings"):
        fr.include_view(FastAPI(), ThingView)


# ---------------------------------------------------------------------------
# List params of your own
# ---------------------------------------------------------------------------


class Search(fr.query.OffsetListParams):
    q: str | None = None


def test_own_list_params_get_the_fields_of_the_pagination(lists):
    lists.view(
        list_params_schema=Search,
        pagination=fr.OffsetPagination(
            limit_query_param="take",
            offset_query_param="skip",
            default_limit=2,
            max_limit=3,
        ),
    )

    body = lists.client.get("/things", params={"skip": 1}).json()
    assert [row["name"] for row in body["data"]] == ["Thing 1", "Thing 2"]
    assert (body["total_count"], body["limit"], body["offset"]) == (5, 2, 1)
    for params in ({"limit": 1}, {"offset": 1}, {"take": 4}, {"skip": -1}):
        lists.client.get("/things", params=params, assert_status_code=422)

    _, operation = _operation(lists.client)
    take = _parameter(operation, "take")
    assert (take["schema"]["maximum"], take["schema"]["default"]) == (3, 2)
    assert _parameter(operation, "skip")["schema"]["minimum"] == 0


def test_code_reads_the_python_names_of_the_fields(client):
    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    seen = []

    @fr.include_view(client.app)
    class ThingView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing
        list_params_schema = Search
        pagination = fr.OffsetPagination(offset_query_param="skip")

        async def apply_list_params(self, stmt, list_params: Search):
            seen.append((list_params.limit, list_params.offset))
            return stmt.limit(list_params.limit).offset(list_params.offset)

    create_tables()
    client.post("/things", json={"name": "a"})
    client.post("/things", json={"name": "b"})

    body = client.get("/things", params={"skip": 1, "limit": 5}).json()
    assert seen == [(5, 1)]
    assert [row["name"] for row in body["data"]] == ["b"]
    assert (body["total_count"], body["limit"], body["offset"]) == (2, 5, 1)


def test_one_class_serves_views_with_other_limits(client):
    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class BaseView(fr.AsyncRestView):
        model = Thing
        list_params_schema = Search
        pagination = fr.OffsetPagination()

    @fr.include_view(client.app)
    class ThingView(BaseView):
        prefix = "/things"

    @fr.include_view(client.app)
    class LogView(BaseView):
        prefix = "/log"
        pagination = fr.OffsetPagination(default_limit=1)

    create_tables()
    client.post("/things", json={"name": "a"})
    client.post("/things", json={"name": "b"})

    assert client.get("/things").json()["limit"] == 50
    assert client.get("/log").json()["limit"] == 1
    assert ThingView.list_params_schema is Search


def test_outside_a_view_the_fields_keep_their_defaults():
    search = Search()
    assert (search.limit, search.offset) == (fr.query.DEFAULT_PAGE_SIZE, 0)
    with pytest.raises(pydantic.ValidationError):
        Search(limit=fr.query.MAX_PAGE_SIZE + 1)
    with pytest.raises(pydantic.ValidationError):
        Search(offset=-1)
    with pytest.raises(pydantic.ValidationError):
        Search(offset=LARGEST_OFFSET + 1)


def test_apply_list_params_applies_the_page_of_own_list_params():
    """Outside a view too: a list that forgets its limit would be silent."""

    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ThingSchema(fr.IDSchema):
        name: str

    stmt = apply_list_params(
        select(Thing), Search(limit=3, offset=6), Thing, ThingSchema
    )
    assert "LIMIT 3 OFFSET 6" in str(
        stmt.compile(compile_kwargs={"literal_binds": True})
    )


@pytest.mark.parametrize(
    ("params", "settings", "needed"),
    [
        (Search, None, "OffsetPagination"),
        (Search, fr.NoPagination(), "OffsetPagination"),
        (Search, fr.NumberedPagination(), "OffsetPagination"),
        (fr.query.PagedListParams, fr.OffsetPagination(), "NumberedPagination"),
    ],
)
def test_own_list_params_need_the_style_of_their_fields(params, settings, needed):
    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ThingView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing
        list_params_schema = params
        pagination = settings

    with pytest.raises(RestlyConfigurationError) as caught:
        fr.include_view(FastAPI(), ThingView)
    assert f"which needs a fr.{needed}" in str(caught.value)
    assert f"Set a fr.{needed}" in str(caught.value)


def test_own_list_params_without_the_fields_fail_with_advice():
    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Plain(pydantic.BaseModel):
        q: str | None = None

    class ThingView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing
        list_params_schema = Plain
        pagination = fr.OffsetPagination()

    with pytest.raises(RestlyConfigurationError) as caught:
        fr.include_view(FastAPI(), ThingView)
    assert str(caught.value) == (
        "ThingView.list_params_schema has no 'limit' and no 'offset' field, so "
        "the list would not page while its response says that it has pages. "
        "Subclass fr.query.OffsetListParams, which gets the fields of the view's "
        "pagination. For a list that pages with page numbers, set pagination = "
        "fr.NumberedPagination() and subclass fr.query.PagedListParams. For a "
        "list that returns every row, set pagination = None."
    )


def test_a_list_with_its_own_limit_and_page_numbers_gets_advice_that_works():
    """The error used to offer page numbers or ``pagination = None``. On a list
    with its own ``limit`` and ``offset``, the first does not fit and the second
    gives a wrong count."""

    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class OwnLimit(pydantic.BaseModel):
        limit: int = 20
        offset: int = 0

    class ThingView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing
        list_params_schema = OwnLimit

    with pytest.raises(RestlyConfigurationError) as caught:
        fr.include_view(FastAPI(), ThingView)
    message = str(caught.value)
    assert "set pagination = fr.OffsetPagination()" in message
    assert "fr.query.OffsetListParams" in message
    assert "For a list that returns every row, set pagination = None." in message


def test_hand_written_fields_with_the_names_of_the_pagination_are_read(lists):
    class Own(pydantic.BaseModel):
        limit: int = 2
        offset: int = 0

    lists.view(list_params_schema=Own, pagination=fr.OffsetPagination())

    body = lists.client.get("/things", params={"offset": 1}).json()
    assert [row["name"] for row in body["data"]] == ["Thing 1", "Thing 2"]
    assert (body["total_count"], body["limit"], body["offset"]) == (5, 2, 1)


# ---------------------------------------------------------------------------
# The query helpers
# ---------------------------------------------------------------------------


def test_the_query_helpers_take_the_pagination():
    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ThingSchema(fr.IDSchema):
        name: str

    settings = fr.OffsetPagination(
        offset_query_param="skip", default_limit=2, max_limit=5
    )
    response = fr.schemas.derive_response_schema(ThingSchema)
    params_model = derive_list_params_schema(response, Thing, pagination=settings)

    def sql(list_params: Any, **kwargs: Any) -> str:
        stmt = apply_list_params(select(Thing), list_params, Thing, response, **kwargs)
        return str(stmt.compile(compile_kwargs={"literal_binds": True}))

    assert "LIMIT 5 OFFSET 2" in sql(
        params_model.model_validate({"limit": 5, "skip": 2})
    )
    assert "LIMIT 2 OFFSET 0" in sql(params_model.model_validate({}))
    with pytest.raises(pydantic.ValidationError):
        params_model.model_validate({"limit": 6})
    # raw query parameters: no list params to give the pagination or a default
    raw = QueryParams("limit=4&skip=8")
    assert "LIMIT 4 OFFSET 8" in sql(raw, pagination=settings)
    raw_offset = sql(QueryParams("skip=8"), pagination=settings)
    assert "OFFSET 8" in raw_offset
    assert "LIMIT 4" not in raw_offset


def test_derive_list_response_schema_takes_the_pagination():
    class ThingSchema(fr.IDSchema):
        name: str

    response = fr.schemas.derive_response_schema(ThingSchema)
    filled = fr.schemas.derive_list_response_schema(
        response, pagination=fr.OffsetPagination()
    )
    assert filled.__name__ == "ThingListResponse"
    assert issubclass(filled, fr.views.OffsetEnvelope)


# ---------------------------------------------------------------------------
# React-admin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base", [fr.AsyncReactAdminView, fr.ReactAdminView])
def test_a_react_admin_view_keeps_its_own_range(base):
    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class ThingSchema(fr.IDSchema):
        name: str

    with pytest.raises(RestlyConfigurationError) as caught:
        type(
            "ThingView",
            (base,),
            {
                "prefix": "/things",
                "model": Thing,
                "schema": ThingSchema,
                "pagination": fr.OffsetPagination(),
            },
        )
    assert "pages with the 'range' parameter" in str(caught.value)
    assert "must stay a fr.NumberedPagination" in str(caught.value)
