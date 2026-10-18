"""
Contract tests for ReactAdminView and AsyncReactAdminView.

Covers the ra-data-simple-rest wire contract for GET /:
- plain JSON array body
- Content-Range header
- sort, range, and filter translation
- error handling for malformed params

Both the async and sync variants are exercised. The sync tests reuse the same
HTTP-level assertions as the async ones to guarantee parity.
"""

import datetime
import decimal
import json
import uuid
from collections.abc import Iterator
from typing import Annotated

import pydantic
import pytest
import sqlalchemy
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from starlette.datastructures import QueryParams

import fastapi_restly as fr
from fastapi_restly.db._globals import _fr_globals
from fastapi_restly.exc import RestlyConfigurationError
from fastapi_restly.testing._client import RestlyTestClient
from fastapi_restly.views._react_admin import (
    DEFAULT_REACT_ADMIN_PAGE_SIZE,
    _resolve_column,
)

from ._models import SharedBase
from .conftest import create_tables

# ---------------------------------------------------------------------------
# Async setup helper (shared by the async-variant tests)
# ---------------------------------------------------------------------------


def _setup_async_item_view(client):
    """Register a minimal ItemView backed by AsyncReactAdminView."""

    class Item(fr.IDMixin, SharedBase):
        name: Mapped[str]
        price: Mapped[float]

    class ItemSchema(fr.IDSchema):
        name: str
        price: float

    @fr.include_view(client.app)
    class ItemView(fr.AsyncReactAdminView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

    create_tables()


# ---------------------------------------------------------------------------
# Sync setup: RestlyTestClient backed by the shared sync SQLite fixture.
# ---------------------------------------------------------------------------


@pytest.fixture
def sync_client(sync_db) -> Iterator[RestlyTestClient]:
    """Yield a RestlyTestClient backed by a sync SQLAlchemy session."""
    app = FastAPI()
    yield RestlyTestClient(app)


def _setup_sync_item_view(client):
    """Register a minimal ItemView backed by ReactAdminView."""

    class SyncItem(fr.IDMixin, SharedBase):
        name: Mapped[str]
        price: Mapped[float]

    class SyncItemSchema(fr.IDSchema):
        name: str
        price: float

    @fr.include_view(client.app)
    class SyncItemView(fr.ReactAdminView):
        prefix = "/items"
        model = SyncItem
        schema = SyncItemSchema

    SharedBase.metadata.create_all(_fr_globals.make_session.kw["bind"])


@pytest.fixture(params=["sync", "async"])
def custom_list_client(request):
    asynchronous = request.param == "async"
    client = request.getfixturevalue("client" if asynchronous else "sync_client")

    class CustomItem(fr.IDMixin, SharedBase):
        name: Mapped[str]

    base = fr.AsyncReactAdminView if asynchronous else fr.ReactAdminView

    @fr.include_view(client.app)
    class CustomItemView(base):
        prefix = "/custom-items"
        model = CustomItem

        if asynchronous:

            @fr.get("/custom")
            async def custom(self, list_params, trace: str | None = None):
                result = await self.handle_get_many(list_params)
                return self.to_response(result, fr.ResponseShape.LIST)

        else:

            @fr.get("/custom")
            def custom(self, list_params, trace: str | None = None):
                result = self.handle_get_many(list_params)
                return self.to_response(result, fr.ResponseShape.LIST)

    if asynchronous:
        create_tables()
    else:
        SharedBase.metadata.create_all(_fr_globals.make_session.kw["bind"])
    for name in ("a", "b", "c"):
        client.post("/custom-items", json={"name": name})
    return client


@pytest.mark.parametrize(
    "params, names, content_range",
    [
        ({"filter": '{"name":"b"}', "range": "[0,0]"}, ["b"], "items 0-0/1"),
        ({"sort": '["name","DESC"]', "range": "[1,1]"}, ["b"], "items 1-1/3"),
        ({"trace": "yes"}, ["a", "b", "c"], "items 0-2/3"),
    ],
)
def test_custom_react_admin_list_uses_its_dialect(
    custom_list_client, params, names, content_range
):
    response = custom_list_client.get("/custom-items/custom", params=params)
    assert [item["name"] for item in response.json()] == names
    assert response.headers["content-range"] == content_range


@pytest.mark.parametrize("parameter", ["filter", "sort", "range"])
def test_custom_react_admin_list_rejects_invalid_json(custom_list_client, parameter):
    custom_list_client.get(
        "/custom-items/custom", params={parameter: "bad-json"}, assert_status_code=422
    )


def test_custom_react_admin_list_keeps_unknown_key_guard(custom_list_client):
    custom_list_client.get(
        "/custom-items/custom", params={"page": "2"}, assert_status_code=422
    )


def test_custom_react_admin_list_openapi_uses_its_dialect(custom_list_client):
    operation = custom_list_client.app.openapi()["paths"]["/custom-items/custom"]["get"]
    assert {p["name"] for p in operation["parameters"] if p["in"] == "query"} == {
        "sort",
        "range",
        "filter",
        "trace",
    }


@pytest.mark.parametrize("flavor", ["sync", "async"])
def test_react_admin_list_shows_its_list_params_in_openapi(flavor, request):
    """GET / declares its list params, as every list route does. Before, it
    read them from the request, and OpenAPI showed no query parameters."""
    asynchronous = flavor == "async"
    client = request.getfixturevalue("client" if asynchronous else "sync_client")
    if asynchronous:
        _setup_async_item_view(client)
    else:
        _setup_sync_item_view(client)

    operation = client.app.openapi()["paths"]["/items"]["get"]

    parameters = {p["name"]: p for p in operation["parameters"]}
    assert list(parameters) == ["sort", "range", "filter"]
    for parameter in parameters.values():
        assert parameter["in"] == "query"
        assert parameter["required"] is False
        assert "as JSON" in parameter["description"]
        json_text = parameter["schema"]["anyOf"][0]
        assert json_text["contentMediaType"] == "application/json"
    assert parameters["range"]["schema"]["anyOf"][0]["contentSchema"]["type"] == "array"
    assert (
        parameters["filter"]["schema"]["anyOf"][0]["contentSchema"]["type"] == "object"
    )


# ===========================================================================
# Async variant tests
# ===========================================================================


# --- Response body ---


def test_react_admin_list_returns_plain_array_body(client):
    _setup_async_item_view(client)
    client.post("/items/", json={"name": "Foo", "price": 1.0})
    client.post("/items/", json={"name": "Bar", "price": 2.0})

    data = client.get("/items/").json()
    assert isinstance(data, list)
    assert len(data) == 2


def test_react_admin_list_returns_zero_total_in_content_range_for_empty_result(client):
    _setup_async_item_view(client)
    response = client.get("/items/")
    assert "/0" in response.headers["content-range"]


# --- Content-Range header ---


def test_react_admin_list_sets_content_range_header(client):
    _setup_async_item_view(client)
    client.post("/items/", json={"name": "Foo", "price": 1.0})

    response = client.get("/items/")
    assert "content-range" in response.headers
    assert "/1" in response.headers["content-range"]


# --- Range (pagination) ---


def test_react_admin_range_translates_to_limit_offset(client):
    _setup_async_item_view(client)
    for i in range(10):
        client.post("/items/", json={"name": f"Item {i:02d}", "price": float(i)})

    response = client.get("/items/", params={"range": "[0,2]"})
    data = response.json()
    assert len(data) == 3  # indices 0, 1, 2 (inclusive)
    assert "items 0-2/10" in response.headers["content-range"]


# --- Sort ---


def test_react_admin_sort_asc_translates_to_model_ordering(client):
    _setup_async_item_view(client)
    client.post("/items/", json={"name": "Zebra", "price": 1.0})
    client.post("/items/", json={"name": "Apple", "price": 2.0})

    data = client.get("/items/", params={"sort": '["name","ASC"]'}).json()
    names = [item["name"] for item in data]
    assert names == sorted(names)


def test_react_admin_sort_desc_translates_to_model_ordering(client):
    _setup_async_item_view(client)
    client.post("/items/", json={"name": "Zebra", "price": 1.0})
    client.post("/items/", json={"name": "Apple", "price": 2.0})

    data = client.get("/items/", params={"sort": '["name","DESC"]'}).json()
    names = [item["name"] for item in data]
    assert names == sorted(names, reverse=True)


# --- Filter ---


def test_react_admin_list_supports_empty_filter_object(client):
    _setup_async_item_view(client)
    client.post("/items/", json={"name": "Foo", "price": 1.0})

    data = client.get("/items/", params={"filter": "{}"}).json()
    assert len(data) == 1


def test_react_admin_filter_scalar_field_translates_to_equality_filter(client):
    _setup_async_item_view(client)
    client.post("/items/", json={"name": "Foo", "price": 1.0})
    client.post("/items/", json={"name": "Bar", "price": 2.0})

    data = client.get("/items/", params={"filter": '{"name":"Foo"}'}).json()
    assert len(data) == 1
    assert data[0]["name"] == "Foo"


def test_react_admin_filter_id_array_translates_to_in_filter(client):
    _setup_async_item_view(client)
    r1 = client.post("/items/", json={"name": "Foo", "price": 1.0}).json()
    r2 = client.post("/items/", json={"name": "Bar", "price": 2.0}).json()
    client.post("/items/", json={"name": "Baz", "price": 3.0})

    ids = [r1["id"], r2["id"]]
    data = client.get("/items/", params={"filter": json.dumps({"id": ids})}).json()
    assert len(data) == 2
    assert {item["id"] for item in data} == set(ids)


# --- Error handling ---


@pytest.mark.parametrize(
    ("params", "loc"),
    [
        ({"sort": "notjson"}, ["query", "sort"]),
        ({"sort": '["name"]'}, ["query", "sort", 1]),
        ({"sort": '["name", "UP"]'}, ["query", "sort", 1]),
        ({"range": "notjson"}, ["query", "range"]),
        ({"range": "[1]"}, ["query", "range", 1]),
        ({"range": '["a", "b"]'}, ["query", "range", 0]),
        ({"range": "[5,1]"}, ["query", "range"]),
        ({"range": "[-1,3]"}, ["query", "range"]),
        ({"range": f"[0,{2**63}]"}, ["query", "range"]),
        ({"range": "[0,1000]"}, ["query", "range"]),
        ({"filter": "notjson"}, ["query", "filter"]),
        ({"filter": "[1, 2]"}, ["query", "filter"]),
    ],
)
def test_react_admin_json_of_the_wrong_shape_returns_422(client, params, loc):
    """The list params parse the JSON of ``sort``, ``range`` and ``filter``.
    JSON that does not have their shape gets a 422 that names the place, as a
    bad value of other list params does. So does a range that asks for more
    rows than ``max_page_size`` (1000)."""
    _setup_async_item_view(client)

    detail = client.get("/items/", params=params, assert_status_code=422).json()

    assert detail["detail"][0]["loc"] == loc


@pytest.mark.parametrize(
    ("params", "detail"),
    [
        ({"sort": '["nope","ASC"]'}, "Unknown sort key: 'nope'"),
        ({"filter": '{"nope":1}'}, "Unknown filter key: 'nope'"),
        (
            {"filter": '{"price":"cheap"}'},
            "Invalid filter value for 'price': 'cheap'. "
            "Input should be a valid number, unable to parse string as a number",
        ),
    ],
)
def test_react_admin_query_errors_are_bad_query_params(client, params, detail):
    """A field name or a filter value that the view cannot apply is a 400
    ``BadQueryParam``, so a handler for ``RestlyHTTPError`` shapes all of
    them."""

    app = FastAPI()

    @app.exception_handler(fr.exc.RestlyHTTPError)
    async def problem(request, exc: fr.exc.RestlyHTTPError):
        return JSONResponse(
            {"type": type(exc).__name__, "detail": exc.detail},
            status_code=exc.status_code,
        )

    problem_client = RestlyTestClient(app)
    _setup_async_item_view(problem_client)
    body = problem_client.get("/items/", params=params, assert_status_code=400).json()
    assert body["type"] == "BadQueryParam"
    if detail is not None:
        assert body["detail"] == detail


def test_react_admin_filter_values_are_validated_as_the_column_type(client):
    """A filter value comes from JSON, so a date or a UUID arrives as a
    string. Before, Restly called the column's Python type on it: a valid
    date returned 400, ``"false"`` matched the true rows, and a bad decimal
    or UUID returned 500."""

    class Ware(fr.IDMixin, SharedBase):
        day: Mapped[datetime.date]
        active: Mapped[bool]
        stock: Mapped[int]
        cost: Mapped[decimal.Decimal | None] = mapped_column(
            sqlalchemy.Numeric(10, 2), default=None
        )
        code: Mapped[uuid.UUID | None] = mapped_column(default=None)

    class WareSchema(fr.IDSchema):
        model_config = pydantic.ConfigDict(populate_by_name=True)
        day: datetime.date
        active: bool
        stock: int = pydantic.Field(alias="inStock")
        cost: decimal.Decimal | None = None
        code: uuid.UUID | None = None

    @fr.include_view(client.app)
    class WareView(fr.AsyncReactAdminView):
        prefix = "/wares"
        model = Ware
        schema = WareSchema

    create_tables()
    client.post("/wares/", json={"day": "2020-01-01", "active": True, "inStock": 1})
    client.post("/wares/", json={"day": "2020-01-02", "active": False, "inStock": 2})

    def matches(filter_: dict) -> list[int]:
        rows = client.get("/wares/", params={"filter": json.dumps(filter_)}).json()
        return [row["inStock"] for row in rows]

    assert matches({"day": "2020-01-01"}) == [1]
    assert matches({"active": "false"}) == [2]
    assert matches({"inStock": ["1", 2]}) == [1, 2]

    for filter_, start in [
        ({"cost": "cheap"}, "Invalid filter value for 'cost': 'cheap'. "),
        ({"code": 5}, "Invalid filter value for 'code': 5. "),
        ({"inStock": "many"}, "Invalid filter value for 'inStock': 'many'. "),
    ]:
        response = client.get(
            "/wares/", params={"filter": json.dumps(filter_)}, assert_status_code=400
        )
        assert response.json()["detail"].startswith(start), filter_


def test_react_admin_unknown_filter_field_returns_400(client):
    _setup_async_item_view(client)
    client.get(
        "/items/", params={"filter": '{"nonexistent":"value"}'}, assert_status_code=400
    )


def test_react_admin_unknown_query_key_returns_422(client):
    """A typo would otherwise widen the result set silently, which is the
    hazard the default dialect's guard exists for."""
    _setup_async_item_view(client)

    response = client.get("/items/", params={"nmae": "Foo"}, assert_status_code=422)

    detail = response.json()["detail"]
    assert [entry["loc"] for entry in detail] == [["query", "nmae"]]
    assert detail[0]["type"] == "extra_forbidden"


def test_react_admin_rejects_the_default_dialects_keys(client):
    """``page`` and the per-field filters belong to the standard grammar. This
    dialect ignores them, so they are rejected rather than silently dropped."""
    _setup_async_item_view(client)

    client.get("/items/", params={"page": "2"}, assert_status_code=422)
    client.get("/items/", params={"name": "Foo"}, assert_status_code=422)


def test_react_admin_contract_keys_are_accepted(client):
    _setup_async_item_view(client)
    client.post("/items/", json={"name": "Foo", "price": 1.0})

    body = client.get(
        "/items/",
        params={"sort": '["name","ASC"]', "range": "[0,9]", "filter": '{"name":"Foo"}'},
    ).json()

    assert [row["name"] for row in body] == ["Foo"]


def test_react_admin_accepts_a_key_that_a_class_attribute_reads(client):
    """As on the default list routes."""

    class Verbose(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class VerboseSchema(fr.IDSchema):
        name: str

    def read_verbose(verbose: bool = False) -> bool:
        return verbose

    @fr.include_view(client.app)
    class VerboseView(fr.AsyncReactAdminView):
        prefix = "/verbose"
        model = Verbose
        schema = VerboseSchema
        verbose: Annotated[bool, Depends(read_verbose)]

    create_tables()

    client.get("/verbose/", params={"verbose": "true"})
    client.get("/verbose/", params={"chatty": "true"}, assert_status_code=422)


def test_a_react_admin_view_keeps_the_list_params_of_its_base(client):
    """A base view adds a search key to the list params, and its subclass
    keeps them. Before, the subclass got the three keys of react-admin."""

    class SearchParams(fr.views.ReactAdminListParams):
        q: str | None = None

    class RaNote(fr.IDMixin, SharedBase):
        text: Mapped[str]

    class SearchAdmin(fr.AsyncReactAdminView):
        schema_list_params = SearchParams

        async def apply_filters(self, stmt, list_params):
            stmt = await super().apply_filters(stmt, list_params)
            if list_params.q:
                stmt = stmt.where(RaNote.text.icontains(list_params.q))
            return stmt

    @fr.include_view(client.app)
    class NoteAdmin(SearchAdmin):
        prefix = "/notes"
        model = RaNote

    create_tables()
    client.post("/notes/", json={"text": "buy milk"})
    client.post("/notes/", json={"text": "call mom"})

    response = client.get("/notes/", params={"q": "milk", "range": "[0,9]"})

    assert [row["text"] for row in response.json()] == ["buy milk"]
    assert response.headers["Content-Range"] == "items 0-0/1"


def test_react_admin_follows_unknown_query_params(client):
    """As on the default list routes, also for a ``QueryParams`` that a
    method gets."""

    class Quiet(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class QuietSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class StrictView(fr.AsyncReactAdminView):
        prefix = "/strict"
        model = Quiet
        schema = QuietSchema

        @fr.get("/raw")
        async def raw(self) -> list[str]:
            query = QueryParams(
                [*self.request.query_params.multi_items(), ("tenant_id", "1")]
            )
            stmt = await self.apply_filters(sqlalchemy.select(Quiet), query)
            return [row.name for row in await self.session.scalars(stmt)]

    @fr.include_view(client.app)
    class QuietView(StrictView):
        prefix = "/quiet"
        unknown_query_params = "ignore"

    create_tables()
    client.post("/strict/quiet", json={"name": "Desk"})

    client.get("/strict/", params={"utm_source": "mail"}, assert_status_code=422)
    detail = client.get("/strict/raw", assert_status_code=422).json()["detail"]
    assert detail[0]["msg"] == "Unknown query parameter 'tenant_id'"
    rows = client.get("/strict/quiet/", params={"utm_source": "mail"}).json()
    assert [row["name"] for row in rows] == ["Desk"]
    assert client.get("/strict/quiet/raw").json() == ["Desk"]


def test_react_admin_uncoercible_filter_value_returns_400(client):
    """A filter value that cannot be coerced to the column type yields a 400."""
    _setup_async_item_view(client)
    client.get(
        "/items/",
        params={"filter": '{"price": "not-a-number"}'},
        assert_status_code=400,
    )


# --- Column resolution guards (shared by both view variants) ---


def test_resolve_column_rejects_relationship_field():
    """A relationship field cannot be used to filter or sort (it is not a column)."""

    class RaMaker(fr.IDMixin, SharedBase):
        name: Mapped[str] = mapped_column()

    class RaMakerSchema(fr.IDSchema):
        name: str

    class RaGadget(fr.IDMixin, SharedBase):
        name: Mapped[str] = mapped_column()
        maker_id: Mapped[int | None] = mapped_column(ForeignKey("ra_maker.id"))
        maker: Mapped[RaMaker | None] = relationship()

    class RaGadgetSchema(fr.IDSchema):
        name: str
        maker: RaMakerSchema

    with pytest.raises(HTTPException) as exc_info:
        _resolve_column(RaGadget, RaGadgetSchema, "maker")

    assert exc_info.value.status_code == 400
    assert "relationship" in str(exc_info.value.detail)


@pytest.mark.parametrize("hidden", ["writeonly", "exclude"])
def test_resolve_column_rejects_a_field_that_never_goes_out(hidden):
    """A WriteOnly field, or one with exclude=True, is not exposed for
    filtering or sorting."""

    class RaThing(fr.IDMixin, SharedBase):
        name: Mapped[str] = mapped_column()
        secret: Mapped[str] = mapped_column()

    class RaThingSchema(fr.IDSchema):
        name: str
        if hidden == "writeonly":
            secret: fr.WriteOnly[str]
        else:
            secret: str = pydantic.Field(exclude=True)

    with pytest.raises(HTTPException) as exc_info:
        _resolve_column(RaThing, RaThingSchema, "secret")

    assert exc_info.value.status_code == 400
    assert "Unknown filter key" in str(exc_info.value.detail)


# --- Overrides get the parsed values ---


def test_an_apply_filters_override_gets_the_parsed_values(client):
    """React-admin sends its search as ``filter={"q": ...}``. An override of
    ``apply_filters`` gets ``filter`` as a dict, and ``sort`` and ``range`` as
    tuples, so it can take ``q`` out and pass the rest on."""
    seen = {}

    class RaMemo(fr.IDMixin, SharedBase):
        text: Mapped[str]

    @fr.include_view(client.app)
    class MemoView(fr.AsyncReactAdminView):
        prefix = "/memos"
        model = RaMemo

        async def apply_filters(self, stmt, list_params):
            seen.update(sort=list_params.sort, range=list_params.range)
            filters = dict(list_params.filter or {})
            q = filters.pop("q", None)
            rest = list_params.model_copy(update={"filter": filters})
            stmt = await super().apply_filters(stmt, rest)
            if q:
                stmt = stmt.where(RaMemo.text.icontains(q))
            return stmt

    create_tables()
    for text in ("buy milk", "call mom", "milk the goat"):
        client.post("/memos/", json={"text": text})

    response = client.get(
        "/memos/",
        params={
            "filter": '{"q": "milk", "id": [1, 2]}',
            "sort": '["text", "ASC"]',
            "range": "[0, 9]",
        },
    )

    assert [row["text"] for row in response.json()] == ["buy milk"]
    assert seen == {"sort": ("text", "ASC"), "range": (0, 9)}


def test_react_admin_list_params_must_be_react_admin_list_params(client):
    """The view reads the parsed ``sort``, ``range`` and ``filter`` from its
    list params. Other list params fail when Python defines the class, not
    with a 500 on the first request."""

    class PlainParams(pydantic.BaseModel):
        sort: str | None = None

    with pytest.raises(RestlyConfigurationError, match="ReactAdminListParams"):

        class PlainView(fr.AsyncReactAdminView):
            schema_list_params = PlainParams


# --- React-admin specific endpoints (PUT /{id}) ---


def test_react_admin_put_endpoint_updates_resource(client):
    """ra-data-simple-rest issues PUT for update; the view must accept it."""
    _setup_async_item_view(client)
    created = client.post("/items/", json={"name": "Foo", "price": 1.0}).json()

    response = client.put(
        f"/items/{created['id']}", json={"name": "Foo Updated", "price": 9.0}
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Foo Updated"
    assert response.json()["price"] == 9.0


# ===========================================================================
# Sync variant tests
# ===========================================================================


def test_sync_react_admin_list_returns_plain_array_body(sync_client):
    _setup_sync_item_view(sync_client)
    sync_client.post("/items/", json={"name": "Foo", "price": 1.0})
    sync_client.post("/items/", json={"name": "Bar", "price": 2.0})

    data = sync_client.get("/items/").json()
    assert isinstance(data, list)
    assert len(data) == 2


def test_sync_react_admin_list_returns_zero_total_for_empty_result(sync_client):
    _setup_sync_item_view(sync_client)
    response = sync_client.get("/items/")
    assert "/0" in response.headers["content-range"]


def test_sync_react_admin_list_sets_content_range_header(sync_client):
    _setup_sync_item_view(sync_client)
    sync_client.post("/items/", json={"name": "Foo", "price": 1.0})

    response = sync_client.get("/items/")
    assert "content-range" in response.headers
    assert "/1" in response.headers["content-range"]


def test_sync_react_admin_range_translates_to_limit_offset(sync_client):
    _setup_sync_item_view(sync_client)
    for i in range(10):
        sync_client.post("/items/", json={"name": f"Item {i:02d}", "price": float(i)})

    response = sync_client.get("/items/", params={"range": "[0,2]"})
    data = response.json()
    assert len(data) == 3
    assert "items 0-2/10" in response.headers["content-range"]


def test_sync_react_admin_sort_asc(sync_client):
    _setup_sync_item_view(sync_client)
    sync_client.post("/items/", json={"name": "Zebra", "price": 1.0})
    sync_client.post("/items/", json={"name": "Apple", "price": 2.0})

    data = sync_client.get("/items/", params={"sort": '["name","ASC"]'}).json()
    names = [item["name"] for item in data]
    assert names == sorted(names)


def test_sync_react_admin_sort_desc(sync_client):
    _setup_sync_item_view(sync_client)
    sync_client.post("/items/", json={"name": "Zebra", "price": 1.0})
    sync_client.post("/items/", json={"name": "Apple", "price": 2.0})

    data = sync_client.get("/items/", params={"sort": '["name","DESC"]'}).json()
    names = [item["name"] for item in data]
    assert names == sorted(names, reverse=True)


def test_sync_react_admin_filter_scalar(sync_client):
    _setup_sync_item_view(sync_client)
    sync_client.post("/items/", json={"name": "Foo", "price": 1.0})
    sync_client.post("/items/", json={"name": "Bar", "price": 2.0})

    data = sync_client.get("/items/", params={"filter": '{"name":"Foo"}'}).json()
    assert len(data) == 1
    assert data[0]["name"] == "Foo"


def test_sync_react_admin_list_supports_empty_filter_object(sync_client):
    _setup_sync_item_view(sync_client)
    sync_client.post("/items/", json={"name": "Foo", "price": 1.0})

    data = sync_client.get("/items/", params={"filter": "{}"}).json()
    assert len(data) == 1


def test_sync_react_admin_filter_id_array(sync_client):
    _setup_sync_item_view(sync_client)
    r1 = sync_client.post("/items/", json={"name": "Foo", "price": 1.0}).json()
    r2 = sync_client.post("/items/", json={"name": "Bar", "price": 2.0}).json()
    sync_client.post("/items/", json={"name": "Baz", "price": 3.0})

    ids = [r1["id"], r2["id"]]
    data = sync_client.get("/items/", params={"filter": json.dumps({"id": ids})}).json()
    assert len(data) == 2
    assert {item["id"] for item in data} == set(ids)


def test_sync_react_admin_invalid_sort_json_returns_422(sync_client):
    _setup_sync_item_view(sync_client)
    sync_client.get("/items/", params={"sort": "notjson"}, assert_status_code=422)


def test_sync_react_admin_invalid_range_json_returns_422(sync_client):
    _setup_sync_item_view(sync_client)
    sync_client.get("/items/", params={"range": "notjson"}, assert_status_code=422)


def test_sync_react_admin_invalid_filter_json_returns_422(sync_client):
    _setup_sync_item_view(sync_client)
    sync_client.get("/items/", params={"filter": "notjson"}, assert_status_code=422)


def test_sync_react_admin_unknown_filter_field_returns_400(sync_client):
    _setup_sync_item_view(sync_client)
    sync_client.get(
        "/items/", params={"filter": '{"nonexistent":"value"}'}, assert_status_code=400
    )


def test_sync_react_admin_unknown_query_key_returns_422(sync_client):
    _setup_sync_item_view(sync_client)

    response = sync_client.get(
        "/items/", params={"nmae": "Foo"}, assert_status_code=422
    )

    detail = response.json()["detail"]
    assert [entry["loc"] for entry in detail] == [["query", "nmae"]]
    assert detail[0]["type"] == "extra_forbidden"


def test_sync_react_admin_rejects_the_default_dialects_keys(sync_client):
    _setup_sync_item_view(sync_client)

    sync_client.get("/items/", params={"page": "2"}, assert_status_code=422)
    sync_client.get("/items/", params={"name": "Foo"}, assert_status_code=422)


def test_sync_react_admin_put_endpoint_updates_resource(sync_client):
    _setup_sync_item_view(sync_client)
    created = sync_client.post("/items/", json={"name": "Foo", "price": 1.0}).json()

    response = sync_client.put(
        f"/items/{created['id']}", json={"name": "Foo Updated", "price": 9.0}
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Foo Updated"
    assert response.json()["price"] == 9.0


def test_sync_react_admin_post_endpoint_creates_resource(sync_client):
    _setup_sync_item_view(sync_client)

    response = sync_client.post("/items/", json={"name": "Foo", "price": 1.0})
    assert response.status_code == 201
    assert response.json()["name"] == "Foo"


def test_sync_react_admin_get_one_endpoint(sync_client):
    """Inherited GET /{id} must continue to work for ReactAdminView."""
    _setup_sync_item_view(sync_client)
    created = sync_client.post("/items/", json={"name": "Foo", "price": 1.0}).json()

    response = sync_client.get(f"/items/{created['id']}")
    assert response.json()["id"] == created["id"]
    assert response.json()["name"] == "Foo"


def test_sync_react_admin_delete_endpoint(sync_client):
    """Inherited DELETE /{id} must continue to work for ReactAdminView."""
    _setup_sync_item_view(sync_client)
    created = sync_client.post("/items/", json={"name": "Foo", "price": 1.0}).json()

    sync_client.delete(f"/items/{created['id']}")
    sync_client.get(f"/items/{created['id']}", assert_status_code=404)


# ===========================================================================
# Configurable default page size
# ===========================================================================


def test_default_page_size_is_25():
    """The framework default matches DEFAULT_REACT_ADMIN_PAGE_SIZE."""
    assert DEFAULT_REACT_ADMIN_PAGE_SIZE == 25


def test_default_page_size_setting_overrides_default(client):
    """A view subclass can set pagination's default_page_size to change the
    implicit page size."""

    class Widget(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class WidgetSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class WidgetView(fr.AsyncReactAdminView):
        prefix = "/widgets"
        model = Widget
        schema = WidgetSchema
        pagination = fr.NumberedPagination(default_page_size=3)

    create_tables()
    for i in range(10):
        client.post("/widgets/", json={"name": f"W{i}"})

    # No explicit range → default_page_size kicks in → only 3 items returned.
    data = client.get("/widgets/").json()
    assert len(data) == 3


# ---------------------------------------------------------------------------
# Read scope / authorization contract (regression)
# ---------------------------------------------------------------------------


def test_react_admin_list_respects_build_query_scope(client):
    """The React Admin list must route through build_query, so a row hidden by
    the read scope is not leaked, and Content-Range total reflects the scope."""

    class ScopedItem(fr.IDMixin, SharedBase):
        name: Mapped[str]
        hidden: Mapped[bool]

    class ScopedItemSchema(fr.IDSchema):
        name: str
        hidden: bool

    @fr.include_view(client.app)
    class ScopedItemView(fr.AsyncReactAdminView):
        prefix = "/scoped-items"
        model = ScopedItem
        schema = ScopedItemSchema
        scope = fr.where_clause(ScopedItem.hidden.is_(False))

    create_tables()
    client.post("/scoped-items/", json={"name": "visible", "hidden": False})
    client.post("/scoped-items/", json={"name": "hidden", "hidden": True})

    resp = client.get("/scoped-items/")
    names = sorted(item["name"] for item in resp.json())
    assert names == ["visible"]  # the hidden row is not leaked by the RA list
    assert resp.headers["Content-Range"].endswith("/1")  # total respects scope


def test_react_admin_list_runs_the_handler_domain_and_response_seams(client):
    """React Admin list is still an endpoint-method replacement: it should delegate
    through the standard handler, domain method, and response chokepoint.

    The handler is final, so its run is observed through the ``authorize``
    call it owns, and the total it carries is observed through ``count``.
    """

    events: list[tuple[str, object | None]] = []

    class SeamItem(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class SeamItemSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class SeamItemView(fr.AsyncReactAdminView):
        prefix = "/seam-items"
        model = SeamItem
        schema = SeamItemSchema

        async def authorize(self, action, obj=None, data=None):
            events.append(("authorize", action))
            await super().authorize(action, obj, data)

        async def get_many(self, list_params, *, scope=None):
            events.append(("get_many", None))
            return await super().get_many(list_params, scope=scope)

        async def count(self, query):
            events.append(("count", None))
            return (await super().count(query)) + 10

        def to_response(self, result, shape=fr.ResponseShape.SINGLE):
            events.append(("to_response", shape))
            return super().to_response(result, shape)

    create_tables()
    client.post("/seam-items/", json={"name": "a"})
    events.clear()

    response = client.get("/seam-items/")

    assert [name for name, _ in events] == [
        "authorize",
        "get_many",
        "count",
        "to_response",
    ]
    assert events[0] == ("authorize", "get_many")
    assert events[-1] == ("to_response", fr.ResponseShape.LIST)
    assert response.json()[0]["name"] == "a"
    # the header reports the total the domain method put in the ListResult
    assert response.headers["Content-Range"].endswith("/11")


def test_sync_react_admin_list_runs_the_handler_domain_and_response_seams(sync_client):
    events: list[tuple[str, object | None]] = []

    class SyncSeamItem(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class SyncSeamItemSchema(fr.IDSchema):
        name: str

    @fr.include_view(sync_client.app)
    class SyncSeamItemView(fr.ReactAdminView):
        prefix = "/sync-seam-items"
        model = SyncSeamItem
        schema = SyncSeamItemSchema

        def authorize(self, action, obj=None, data=None):
            events.append(("authorize", action))
            super().authorize(action, obj, data)

        def get_many(self, list_params, *, scope=None):
            events.append(("get_many", None))
            return super().get_many(list_params, scope=scope)

        def count(self, query):
            events.append(("count", None))
            return super().count(query) + 10

        def to_response(self, result, shape=fr.ResponseShape.SINGLE):
            events.append(("to_response", shape))
            return super().to_response(result, shape)

    SharedBase.metadata.create_all(_fr_globals.make_session.kw["bind"])
    sync_client.post("/sync-seam-items/", json={"name": "a"})
    events.clear()

    response = sync_client.get("/sync-seam-items/")

    assert [name for name, _ in events] == [
        "authorize",
        "get_many",
        "count",
        "to_response",
    ]
    assert events[0] == ("authorize", "get_many")
    assert events[-1] == ("to_response", fr.ResponseShape.LIST)
    assert response.json()[0]["name"] == "a"
    assert response.headers["Content-Range"].endswith("/11")


@pytest.mark.parametrize("flavor", ["sync", "async"])
def test_react_admin_list_response_is_changed_in_to_list_response(flavor, request):
    """``to_list_response`` builds the react-admin list response, so an
    override that calls ``super()`` keeps the array and ``Content-Range``."""
    asynchronous = flavor == "async"
    client = request.getfixturevalue("client" if asynchronous else "sync_client")

    class CountedItem(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class CountedItemSchema(fr.IDSchema):
        name: str

    base = fr.AsyncReactAdminView if asynchronous else fr.ReactAdminView

    @fr.include_view(client.app)
    class CountedItemView(base):
        prefix = "/counted-items"
        model = CountedItem
        schema = CountedItemSchema

        def to_list_response(self, list_result):
            response = super().to_list_response(list_result)
            response.headers["X-Total-Count"] = str(list_result.total_count)
            return response

    if asynchronous:
        create_tables()
    else:
        SharedBase.metadata.create_all(_fr_globals.make_session.kw["bind"])
    for name in ("a", "b"):
        client.post("/counted-items/", json={"name": name})

    response = client.get("/counted-items/", params={"range": "[0,0]"})

    assert [item["name"] for item in response.json()] == ["a"]
    assert response.headers["Content-Range"] == "items 0-0/2"
    assert response.headers["X-Total-Count"] == "2"


@pytest.mark.parametrize("unpaginated", [None, fr.NoPagination()])
def test_react_admin_view_cannot_disable_pagination(client, unpaginated):
    """A react-admin view reports its total via Content-Range, which needs the
    count query, so ``pagination = None`` or a ``NoPagination`` is rejected at
    registration."""

    class Gadget(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class GadgetSchema(fr.IDSchema):
        name: str

    with pytest.raises(RestlyConfigurationError, match="cannot disable pagination"):

        @fr.include_view(client.app)
        class GadgetView(fr.AsyncReactAdminView):
            prefix = "/gadgets"
            model = Gadget
            schema = GadgetSchema
            pagination = unpaginated
