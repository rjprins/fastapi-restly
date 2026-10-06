"""Tests for the ``pagination`` view setting, ``fr.NumberedPagination`` and
``fr.NoPagination``: query parameter names, limits, the envelope, and
inheritance."""

import re
from typing import Any, Generic, TypeVar

import pytest
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.orm import Mapped
from starlette.datastructures import QueryParams

import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError
from fastapi_restly.query import apply_list_params, create_list_params_schema
from fastapi_restly.testing import RestlyTestClient

from .conftest import create_tables

T = TypeVar("T")
U = TypeVar("U")


class FastAPIPaginationPage(BaseModel, Generic[T]):
    """fastapi-pagination's default ``Page`` body."""

    data: list[T] = Field(alias="items")
    total_count: int = Field(alias="total")
    page: int
    page_size: int = Field(alias="size")
    total_pages: int = Field(alias="pages")


class CamelPage(BaseModel, Generic[T]):
    model_config = ConfigDict(alias_generator=to_camel)

    data: list[T]
    total_count: int
    page: int
    page_size: int
    total_pages: int


class DataCount(BaseModel, Generic[T]):
    """The FastAPI full-stack template's list body."""

    data: list[T]
    total_count: int = Field(alias="count")


class PageMeta(BaseModel):
    total_count: int
    page: int
    page_size: int
    total_pages: int


class DataMeta(BaseModel, Generic[T]):
    """The metadata nested under ``meta``, reshaped by a before-validator."""

    data: list[T]
    meta: PageMeta

    @model_validator(mode="before")
    @classmethod
    def _nest(cls, values: Any) -> Any:
        if isinstance(values, dict) and "meta" not in values:
            values = dict(values)
            return {"data": values.pop("data"), "meta": values}
        return values


class Items(RootModel[list[T]], Generic[T]):
    """A bare JSON array of the items."""

    @model_validator(mode="before")
    @classmethod
    def _rows(cls, values: Any) -> Any:
        return values["data"] if isinstance(values, dict) else values


def _list_app(client, settings, *, rows=5):
    """Register ``/things`` with ``settings`` as its pagination, and add
    ``rows`` rows named ``Thing 0`` and up."""

    class Thing(fr.IDBase):
        name: Mapped[str]

    class ThingRead(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class ThingView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing
        schema = ThingRead
        pagination = settings

    create_tables()
    for i in range(rows):
        client.post("/things/", json={"name": f"Thing {i}"})
    return ThingView


def _list_operation(client, prefix="/things"):
    spec = client.app.openapi()
    paths = spec["paths"]
    return spec, (paths.get(prefix) or paths[prefix + "/"])["get"]


def _query_param(operation, name):
    return next(p for p in operation["parameters"] if p["name"] == name)


def _response_component(spec, operation):
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]
    return spec["components"]["schemas"][
        ref["$ref"].removeprefix("#/components/schemas/")
    ]


def _response_properties(spec, operation):
    return list(_response_component(spec, operation)["properties"])


# ---------------------------------------------------------------------------
# The settings
# ---------------------------------------------------------------------------


def test_defaults_keep_the_page_and_page_size_contract():
    pagination = fr.NumberedPagination()

    assert pagination.page_query_param == "page"
    assert pagination.page_size_query_param == "page_size"
    assert pagination.default_page_size == fr.query.DEFAULT_PAGE_SIZE
    assert pagination.max_page_size == fr.query.MAX_PAGE_SIZE
    assert pagination.max_page is None
    assert pagination.envelope is fr.views.PaginatedEnvelope
    assert fr.AsyncRestView.pagination == pagination
    assert fr.RestView.pagination == pagination


def test_replace_returns_a_checked_copy():
    base = fr.NumberedPagination(page_size_query_param="size", max_page_size=100)

    small = base.replace(default_page_size=10, max_page_size=10)

    assert small.page_size_query_param == "size"
    assert (small.max_page_size, small.default_page_size) == (10, 10)
    assert base.max_page_size == 100
    # the default page size of 50 would be above the new maximum
    with pytest.raises(RestlyConfigurationError, match="default_page_size"):
        base.replace(max_page_size=10)


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"max_page_size": 0}, "max_page_size must be an int of at least 1"),
        ({"max_page_size": True}, "max_page_size must be an int of at least 1"),
        ({"max_page": 0}, "max_page must be an int of at least 1"),
        ({"page_query_param": ""}, "page_query_param must be a non-empty string"),
        ({"page_size_query_param": "page__size"}, "'__' is reserved"),
        ({"page_query_param": "page.number"}, "'.' for relation traversal"),
        ({"page_query_param": "sort"}, "it is the sort parameter"),
        ({"page_query_param": "n", "page_size_query_param": "n"}, "must differ"),
        ({"page_query_param": "_page"}, "cannot start with '_'"),
        ({"page_size_query_param": "model_config"}, "Pydantic BaseModel attribute"),
        ({"default_page_size": 0}, "set 'pagination = None' on the view"),
        ({"default_page_size": None}, "set 'pagination = None' on the view"),
        ({"default_page_size": 20, "max_page_size": 10}, "an int in [1, 10], got 20"),
    ],
)
def test_invalid_settings_fail_when_created(settings, message):
    with pytest.raises(RestlyConfigurationError, match=re.escape(message)):
        fr.NumberedPagination(**settings)


class _NotGeneric(BaseModel):
    data: list[Any]


class _TwoParameters(BaseModel, Generic[T, U]):
    data: list[T]


@pytest.mark.parametrize(
    ("envelope", "message"),
    [
        (dict, "must be a Pydantic model class"),
        (_NotGeneric, "generic Pydantic model with one type parameter"),
        (
            fr.views.PaginatedEnvelope[int],
            "generic Pydantic model with one type parameter",
        ),
        (_TwoParameters, "generic Pydantic model with one type parameter"),
    ],
)
def test_envelope_must_be_a_generic_model_with_one_parameter(envelope, message):
    with pytest.raises(RestlyConfigurationError, match=re.escape(message)):
        fr.NumberedPagination(envelope=envelope)


def test_envelope_field_restly_cannot_fill_fails_when_created():
    class Misspelled(BaseModel, Generic[T]):
        data: list[T]
        totl_count: int

    with pytest.raises(RestlyConfigurationError) as excinfo:
        fr.NumberedPagination(envelope=Misspelled)

    message = str(excinfo.value)
    assert "totl_count: Field required" in message
    assert "(data, total_count, page, page_size, total_pages)" in message


@pytest.mark.parametrize("extra", ["allow", "forbid"])
def test_envelope_must_keep_the_default_extra(extra):
    class Strict(BaseModel, Generic[T]):
        model_config = ConfigDict(extra=extra)

        data: list[T]

    with pytest.raises(RestlyConfigurationError, match=f"sets extra='{extra}'"):
        fr.NumberedPagination(envelope=Strict)


def test_envelope_field_with_a_default_is_sent_as_is(client):
    class Versioned(BaseModel, Generic[T]):
        data: list[T]
        api_version: str = "2"

    _list_app(client, fr.NumberedPagination(envelope=Versioned), rows=1)

    payload = client.get("/things/").json()
    assert payload == {"data": [{"id": 1, "name": "Thing 0"}], "api_version": "2"}


# ---------------------------------------------------------------------------
# The view setting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "value", "instead"),
    [
        ("paginated", False, "Write 'pagination = None' to return every row."),
        ("paginated", True, "Remove it: views paginate by default."),
        (
            "default_page_size",
            10,
            "Write 'pagination = fr.NumberedPagination(default_page_size=10)'",
        ),
        (
            "max_page_size",
            100,
            "call '.replace(max_page_size=100)' on pagination settings",
        ),
        (
            "max_page_size",
            20,
            "fr.NumberedPagination(default_page_size=20, max_page_size=20)",
        ),
    ],
)
def test_old_pagination_settings_fail_with_what_to_write_instead(name, value, instead):
    with pytest.raises(RestlyConfigurationError) as excinfo:
        type("OldView", (fr.AsyncRestView,), {name: value})

    message = str(excinfo.value)
    assert f"OldView sets {name}, which was replaced by the 'pagination'" in message
    assert instead in message


def test_old_pagination_settings_get_one_suggestion():
    with pytest.raises(RestlyConfigurationError) as excinfo:
        type(
            "OldView",
            (fr.AsyncRestView,),
            {"default_page_size": 25, "max_page_size": 200},
        )

    message = str(excinfo.value)
    assert (
        "OldView sets default_page_size, max_page_size, which were replaced" in message
    )
    assert (
        "Write 'pagination = fr.NumberedPagination(default_page_size=25, "
        "max_page_size=200)'" in message
    )
    # the suggestion works as written
    fr.NumberedPagination(default_page_size=25, max_page_size=200)


def test_settings_assigned_after_the_class_fail_at_registration(client):
    class Thing(fr.IDBase):
        name: Mapped[str]

    class ThingRead(fr.IDSchema):
        name: str

    class CappedView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing
        schema = ThingRead

    CappedView.max_page_size = 5  # type: ignore[attr-defined]
    with pytest.raises(RestlyConfigurationError, match="sets max_page_size"):
        fr.include_view(client.app, CappedView)

    class LateView(fr.AsyncRestView):
        prefix = "/late"
        model = Thing
        schema = ThingRead

    LateView.pagination = fr.NumberedPagination  # type: ignore[assignment]
    with pytest.raises(RestlyConfigurationError, match="Create an instance"):
        fr.include_view(client.app, LateView)


def test_old_pagination_setting_on_a_mixin_names_the_mixin():
    class CapMixin:
        max_page_size = 100

    with pytest.raises(
        RestlyConfigurationError, match=re.escape("sets max_page_size (from CapMixin)")
    ):

        class CappedView(CapMixin, fr.AsyncRestView):
            pass


@pytest.mark.parametrize("settings_class", [fr.NumberedPagination, fr.NoPagination])
def test_pagination_must_be_an_instance(settings_class):
    with pytest.raises(
        RestlyConfigurationError,
        match=re.escape(f"Create an instance: fr.{settings_class.__name__}()."),
    ):
        type("ClassNotInstance", (fr.AsyncRestView,), {"pagination": settings_class})


def test_pagination_must_be_settings_or_none():
    with pytest.raises(
        RestlyConfigurationError,
        match=re.escape(
            "must be a fr.NumberedPagination or fr.NoPagination instance, "
            "or None, got 50."
        ),
    ):

        class NumberNotSettings(fr.AsyncRestView):
            pagination = 50  # type: ignore[assignment]


def test_a_base_view_sets_pagination_once(client):
    class Note(fr.IDBase):
        text: Mapped[str]

    class NoteRead(fr.IDSchema):
        text: str

    app_pagination = fr.NumberedPagination(
        page_size_query_param="size",
        default_page_size=3,
        max_page_size=3,
        envelope=DataCount,
    )

    class AppView(fr.AsyncRestView):
        model = Note
        schema = NoteRead
        pagination = app_pagination

    @fr.include_view(client.app)
    class NoteView(AppView):
        prefix = "/notes"

    @fr.include_view(client.app)
    class TinyNoteView(AppView):
        prefix = "/tiny-notes"
        pagination = app_pagination.replace(default_page_size=1, max_page_size=1)

    @fr.include_view(client.app)
    class AllNoteView(AppView):
        prefix = "/all-notes"
        pagination = None

        async def count(self, query):
            raise AssertionError("a view without pagination runs no count query")

    create_tables()
    for i in range(4):
        client.post("/notes/", json={"text": f"Note {i}"})

    notes = client.get("/notes/").json()
    assert (len(notes["data"]), notes["count"]) == (3, 4)
    client.get("/notes/?size=4", assert_status_code=422)

    tiny = client.get("/tiny-notes/").json()
    assert (len(tiny["data"]), tiny["count"]) == (1, 4)
    client.get("/tiny-notes/?size=2", assert_status_code=422)

    every_note = client.get("/all-notes/").json()
    assert set(every_note) == {"data"}
    assert len(every_note["data"]) == 4
    client.get("/all-notes/?size=2", assert_status_code=422)


def test_declared_grammar_must_match_the_pagination(client):
    class Thing(fr.IDBase):
        name: Mapped[str]

    class ThingRead(fr.IDSchema):
        name: str

    with pytest.raises(
        RestlyConfigurationError,
        match="listing_param_schema was created for other pagination settings",
    ):

        @fr.include_view(client.app)
        class MismatchView(fr.AsyncRestView):
            prefix = "/mismatch"
            model = Thing
            schema = ThingRead
            pagination = None
            listing_param_schema = create_list_params_schema(ThingRead, Thing)

    @fr.include_view(client.app)
    class MatchView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing
        schema = ThingRead
        pagination = None
        listing_param_schema = create_list_params_schema(
            ThingRead, Thing, pagination=None
        )

    create_tables()
    client.post("/things/", json={"name": "Thing 0"})
    assert client.get("/things/").json() == {"data": [{"id": 1, "name": "Thing 0"}]}


# ---------------------------------------------------------------------------
# Query parameters
# ---------------------------------------------------------------------------


def test_renamed_query_params(client):
    _list_app(
        client,
        fr.NumberedPagination(page_query_param="p", page_size_query_param="size"),
    )

    payload = client.get("/things/?p=2&size=2").json()
    assert [item["name"] for item in payload["data"]] == ["Thing 2", "Thing 3"]
    assert (payload["page"], payload["page_size"], payload["total_pages"]) == (2, 2, 3)

    rejected = client.get("/things/?page_size=2", assert_status_code=422)
    assert rejected.json()["detail"][0]["loc"] == ["query", "page_size"]

    _, operation = _list_operation(client)
    names = {parameter["name"] for parameter in operation["parameters"]}
    assert {"p", "size"} <= names
    assert not {"page", "page_size"} & names


def test_bracketed_query_param_names(client):
    """JSON:API spells the parameters ``page[number]`` and ``page[size]``."""
    _list_app(
        client,
        fr.NumberedPagination(
            page_query_param="page[number]", page_size_query_param="page[size]"
        ),
    )

    payload = client.get("/things/", params={"page[number]": 3, "page[size]": 2})
    assert payload.json()["page"] == 3
    assert [item["name"] for item in payload.json()["data"]] == ["Thing 4"]


def test_renaming_a_param_frees_its_name_for_a_filter(client):
    class Chapter(fr.IDBase):
        page: Mapped[int]

    class ChapterRead(fr.IDSchema):
        page: int

    @fr.include_view(client.app)
    class ChapterView(fr.AsyncRestView):
        prefix = "/chapters"
        model = Chapter
        schema = ChapterRead
        pagination = fr.NumberedPagination(page_query_param="p")

    create_tables()
    for page in (1, 2, 2):
        client.post("/chapters/", json={"page": page})

    payload = client.get("/chapters/?page=2").json()
    assert payload["total_count"] == 2
    assert payload["page"] == 1  # the page number comes from ``p``


def test_a_filter_named_like_a_pagination_param_fails_at_registration(client):
    """fastapi-pagination's ``size`` parameter hides a ``size`` column's filter;
    Restly refuses the clash when the view registers."""

    class Shirt(fr.IDBase):
        size: Mapped[str]

    class ShirtRead(fr.IDSchema):
        size: str

    with pytest.raises(ValueError, match="cannot expose field 'size'"):

        @fr.include_view(client.app)
        class ShirtView(fr.AsyncRestView):
            prefix = "/shirts"
            model = Shirt
            schema = ShirtRead
            pagination = fr.NumberedPagination(page_size_query_param="size")


def test_max_page(client):
    _list_app(client, fr.NumberedPagination(max_page=2, default_page_size=2))

    assert client.get("/things/?page=2").json()["page"] == 2
    rejected = client.get("/things/?page=3", assert_status_code=422)
    assert rejected.json()["detail"][0]["loc"] == ["query", "page"]

    _, operation = _list_operation(client)
    assert _query_param(operation, "page")["schema"]["maximum"] == 2


# ---------------------------------------------------------------------------
# Envelopes
# ---------------------------------------------------------------------------


def test_fastapi_pagination_contract(client):
    """A view can serve fastapi-pagination's default ``Page`` contract:
    ``?page=&size=`` and ``items`` / ``total`` / ``page`` / ``size`` /
    ``pages``."""
    _list_app(
        client,
        fr.NumberedPagination(
            page_size_query_param="size",
            max_page_size=100,
            envelope=FastAPIPaginationPage,
        ),
    )

    assert client.get("/things/?page=2&size=2").json() == {
        "items": [{"id": 3, "name": "Thing 2"}, {"id": 4, "name": "Thing 3"}],
        "total": 5,
        "page": 2,
        "size": 2,
        "pages": 3,
    }
    spec, operation = _list_operation(client)
    assert _response_properties(spec, operation) == [
        "items",
        "total",
        "page",
        "size",
        "pages",
    ]


def test_camel_case_envelope(client):
    _list_app(client, fr.NumberedPagination(envelope=CamelPage))

    payload = client.get("/things/?page_size=2").json()
    assert {key: value for key, value in payload.items() if key != "data"} == {
        "totalCount": 5,
        "page": 1,
        "pageSize": 2,
        "totalPages": 3,
    }
    spec, operation = _list_operation(client)
    assert _response_properties(spec, operation) == [
        "data",
        "totalCount",
        "page",
        "pageSize",
        "totalPages",
    ]


def test_data_count_envelope(client):
    _list_app(client, fr.NumberedPagination(envelope=DataCount))

    assert client.get("/things/?page_size=2").json() == {
        "data": [{"id": 1, "name": "Thing 0"}, {"id": 2, "name": "Thing 1"}],
        "count": 5,
    }


def test_nested_meta_envelope(client):
    _list_app(client, fr.NumberedPagination(envelope=DataMeta))

    payload = client.get("/things/?page=2&page_size=2").json()
    assert payload["meta"] == {
        "total_count": 5,
        "page": 2,
        "page_size": 2,
        "total_pages": 3,
    }
    assert [item["name"] for item in payload["data"]] == ["Thing 2", "Thing 3"]


def test_sync_view_takes_the_same_settings(sync_db):
    engine, _ = sync_db

    class Thing(fr.IDBase):
        name: Mapped[str]

    class ThingRead(fr.IDSchema):
        name: str

    app = FastAPI()

    @fr.include_view(app)
    class ThingView(fr.RestView):
        prefix = "/things"
        model = Thing
        schema = ThingRead
        pagination = fr.NumberedPagination(
            page_size_query_param="size", envelope=FastAPIPaginationPage
        )

    @fr.include_view(app)
    class AllThingView(fr.RestView):
        prefix = "/all-things"
        model = Thing
        schema = ThingRead
        pagination = fr.NoPagination(envelope=DataCount)

        def count(self, query):
            raise AssertionError("a view without pagination runs no count query")

    fr.DataclassBase.metadata.create_all(engine)
    client = RestlyTestClient(app)
    for i in range(5):
        client.post("/things/", json={"name": f"Thing {i}"})

    payload = client.get("/things/?page=3&size=2").json()
    assert payload == {
        "items": [{"id": 5, "name": "Thing 4"}],
        "total": 5,
        "page": 3,
        "size": 2,
        "pages": 3,
    }
    every_thing = client.get("/all-things/").json()
    assert (len(every_thing["data"]), every_thing["count"]) == (5, 5)


# ---------------------------------------------------------------------------
# No pagination
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("unpaginated", [None, fr.NoPagination()])
def test_no_pagination_is_what_none_means(client, unpaginated):
    assert fr.NoPagination().envelope is fr.views.Envelope

    view = _list_app(client, unpaginated, rows=3)

    payload = client.get("/things/").json()
    assert set(payload) == {"data"}
    assert len(payload["data"]) == 3
    client.get("/things/?page_size=2", assert_status_code=422)
    spec, operation = _list_operation(client)
    assert _response_properties(spec, operation) == ["data"]
    assert view.listing_param_schema.model_fields.keys().isdisjoint(
        {"page", "page_size"}
    )


def test_no_pagination_envelope_gets_the_row_count(client):
    view = _list_app(client, fr.NoPagination(envelope=DataCount))

    async def count(self, query):
        raise AssertionError("a view without pagination runs no count query")

    view.count = count

    payload = client.get("/things/?name__icontains=thing").json()
    assert (len(payload["data"]), payload["count"]) == (5, 5)
    spec, operation = _list_operation(client)
    assert _response_properties(spec, operation) == ["data", "count"]


def test_no_pagination_bare_array(client):
    _list_app(client, fr.NoPagination(envelope=Items), rows=2)

    assert client.get("/things/").json() == [
        {"id": 1, "name": "Thing 0"},
        {"id": 2, "name": "Thing 1"},
    ]
    spec, operation = _list_operation(client)
    component = _response_component(spec, operation)
    assert component["type"] == "array"
    assert component["items"]["$ref"].endswith("/ThingRead")


def test_no_pagination_envelope_field_restly_cannot_fill_fails_when_created():
    with pytest.raises(RestlyConfigurationError) as excinfo:
        fr.NoPagination(envelope=fr.views.PaginatedEnvelope)

    message = str(excinfo.value)
    assert message.startswith("NoPagination.envelope PaginatedEnvelope cannot be")
    assert "(data, total_count)" in message
    assert "page: Field required" in message
    with pytest.raises(RestlyConfigurationError, match="one type parameter"):
        fr.NoPagination(envelope=_NotGeneric)


def test_no_pagination_replace():
    settings = fr.NoPagination(envelope=DataCount)

    assert settings.replace(envelope=Items).envelope is Items
    assert settings.envelope is DataCount


# ---------------------------------------------------------------------------
# The query helpers and react-admin
# ---------------------------------------------------------------------------


def test_query_helpers_take_the_pagination():
    class Widget(fr.IDBase):
        name: Mapped[str]

    class WidgetRead(fr.IDSchema):
        name: str

    pagination = fr.NumberedPagination(page_size_query_param="size")

    params_model = create_list_params_schema(WidgetRead, Widget, pagination=pagination)
    assert {"page", "size", "sort"} <= set(params_model.model_fields)
    assert "page_size" not in params_model.model_fields

    def sql(query_string: str, **kwargs: Any) -> str:
        query = apply_list_params(
            QueryParams(query_string), select(Widget), Widget, WidgetRead, **kwargs
        )
        return str(query.compile(compile_kwargs={"literal_binds": True}))

    assert "LIMIT 10 OFFSET 20" in sql("page=3&size=10", pagination=pagination)
    assert "LIMIT" not in sql("sort=name", pagination=None)
    assert "LIMIT" not in sql("sort=name", pagination=fr.NoPagination())
    unpaginated = create_list_params_schema(
        WidgetRead, Widget, pagination=fr.NoPagination()
    )
    assert unpaginated.model_fields.keys().isdisjoint({"page", "page_size"})
    # without pagination, ``size`` is no parameter but an unknown filter
    with pytest.raises(fr.exc.BadQueryParam, match="Invalid attribute in URL query"):
        sql("size=10", pagination=None)


def test_apply_list_params_reads_the_pagination_of_its_params_model():
    class Bookmark(fr.IDBase):
        page: Mapped[int]

    class BookmarkRead(fr.IDSchema):
        page: int

    def sql(params: Any) -> str:
        query = apply_list_params(params, select(Bookmark), Bookmark, BookmarkRead)
        return str(query.compile(compile_kwargs={"literal_binds": True}))

    # without pagination, ``page`` is a filter, not a page number
    unpaginated = create_list_params_schema(BookmarkRead, Bookmark, pagination=None)
    filtered = sql(unpaginated.model_validate({"page": ["3"]}))
    assert "bookmark.page = 3" in filtered
    assert "LIMIT" not in filtered

    renamed = create_list_params_schema(
        BookmarkRead,
        Bookmark,
        pagination=fr.NumberedPagination(
            page_query_param="p", page_size_query_param="size"
        ),
    )
    assert "LIMIT 10 OFFSET 10" in sql(renamed.model_validate({"p": 2, "size": 10}))


@pytest.mark.parametrize(
    ("settings", "ignored"),
    [
        ({"page_size_query_param": "size"}, "page_size_query_param"),
        ({"max_page": 10}, "max_page"),
        ({"envelope": DataCount}, "envelope"),
    ],
)
def test_react_admin_view_rejects_settings_it_would_ignore(client, settings, ignored):
    class Gadget(fr.IDBase):
        name: Mapped[str]

    class GadgetRead(fr.IDSchema):
        name: str

    with pytest.raises(
        RestlyConfigurationError,
        match=f"uses only pagination.default_page_size and max_page_size.*: {ignored}",
    ):

        @fr.include_view(client.app)
        class GadgetView(fr.AsyncReactAdminView):
            prefix = "/gadgets"
            model = Gadget
            schema = GadgetRead
            pagination = fr.NumberedPagination(**settings)


def test_react_admin_range_is_checked(client):
    class Gadget(fr.IDBase):
        name: Mapped[str]

    class GadgetRead(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class GadgetView(fr.AsyncReactAdminView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetRead
        pagination = fr.NumberedPagination(default_page_size=5, max_page_size=10)

    create_tables()
    for i in range(12):
        client.post("/gadgets/", json={"name": f"Gadget {i}"})

    assert len(client.get("/gadgets/?range=[0,9]").json()) == 10
    too_wide = client.get("/gadgets/?range=[0,10]", assert_status_code=400)
    assert "at most 10 rows" in too_wide.json()["detail"]
    for backwards in ("[0,-5]", "[-3,4]", "[5,2]"):
        response = client.get(f"/gadgets/?range={backwards}", assert_status_code=400)
        assert "0 <= start <= end" in response.json()["detail"]
