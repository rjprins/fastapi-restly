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

import json
from collections.abc import Iterator

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.db._globals import _fr_globals
from fastapi_restly.testing._client import RestlyTestClient
from fastapi_restly.views._react_admin import (
    DEFAULT_REACT_ADMIN_PAGE_SIZE,
    _ReactAdminMixin,
    _resolve_column,
    parse_react_admin_range,
)

from .conftest import create_tables

# ---------------------------------------------------------------------------
# Async setup helper (shared by the async-variant tests)
# ---------------------------------------------------------------------------


def _setup_async_item_view(client):
    """Register a minimal ItemView backed by AsyncReactAdminView."""

    class Item(fr.IDBase):
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

    class SyncItem(fr.IDBase):
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

    fr.DataclassBase.metadata.create_all(_fr_globals.make_session.kw["bind"])


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


def test_react_admin_invalid_sort_json_returns_400(client):
    _setup_async_item_view(client)
    client.get("/items/", params={"sort": "notjson"}, assert_status_code=400)


def test_react_admin_invalid_range_json_returns_400(client):
    _setup_async_item_view(client)
    client.get("/items/", params={"range": "notjson"}, assert_status_code=400)


def test_react_admin_invalid_filter_json_returns_400(client):
    _setup_async_item_view(client)
    client.get("/items/", params={"filter": "notjson"}, assert_status_code=400)


def test_react_admin_unknown_filter_field_returns_400(client):
    _setup_async_item_view(client)
    client.get(
        "/items/", params={"filter": '{"nonexistent":"value"}'}, assert_status_code=400
    )


def test_react_admin_sort_wrong_shape_returns_400(client):
    """Valid JSON that is not a 2-element ``[field, direction]`` array is rejected."""
    _setup_async_item_view(client)
    client.get("/items/", params={"sort": '["only_one"]'}, assert_status_code=400)


def test_react_admin_sort_bad_direction_returns_400(client):
    """A direction other than ASC/DESC is rejected."""
    _setup_async_item_view(client)
    client.get("/items/", params={"sort": '["name", "UP"]'}, assert_status_code=400)


def test_react_admin_range_wrong_shape_returns_400(client):
    """Valid JSON that is not a 2-element ``[start, end]`` array is rejected."""
    _setup_async_item_view(client)
    client.get("/items/", params={"range": "[1]"}, assert_status_code=400)


def test_react_admin_range_non_integer_returns_400(client):
    """Non-integer range bounds are rejected."""
    _setup_async_item_view(client)
    client.get("/items/", params={"range": '["a", "b"]'}, assert_status_code=400)


def test_react_admin_filter_not_object_returns_400(client):
    """Valid JSON that is not an object (e.g. an array) is rejected."""
    _setup_async_item_view(client)
    client.get("/items/", params={"filter": "[1, 2]"}, assert_status_code=400)


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

    class RaMaker(fr.IDBase):
        name: Mapped[str] = mapped_column()

    class RaMakerSchema(fr.IDSchema):
        name: str

    class RaGadget(fr.IDBase):
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


def test_resolve_column_rejects_writeonly_field():
    """A write-only schema field is not exposed for filtering or sorting."""

    class RaThing(fr.IDBase):
        name: Mapped[str] = mapped_column()
        secret: Mapped[str] = mapped_column()

    class RaThingSchema(fr.IDSchema):
        name: str
        secret: fr.WriteOnly[str]

    with pytest.raises(HTTPException) as exc_info:
        _resolve_column(RaThing, RaThingSchema, "secret")

    assert exc_info.value.status_code == 400
    assert "Unknown filter field" in str(exc_info.value.detail)


# --- Pre-parsed (programmatic) param coercion ---


def test_coerce_params_accepts_prebuilt_values():
    """A handler may pass already-parsed sort/range/filter values directly."""
    params = _ReactAdminMixin()._coerce_react_admin_params(
        {"sort": ["name", "ASC"], "range": [5, 9], "filter": {"name": "x"}}
    )

    assert params.sort == ("name", "ASC")
    assert (params.start, params.end) == (5, 9)
    assert params.filters == {"name": "x"}


def test_coerce_params_rejects_bad_prebuilt_range():
    """A pre-parsed range that is not a 2-element sequence is rejected."""
    with pytest.raises(HTTPException) as exc_info:
        _ReactAdminMixin()._coerce_react_admin_params({"range": [5]})

    assert exc_info.value.status_code == 400


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


def test_sync_react_admin_invalid_sort_json_returns_400(sync_client):
    _setup_sync_item_view(sync_client)
    sync_client.get("/items/", params={"sort": "notjson"}, assert_status_code=400)


def test_sync_react_admin_invalid_range_json_returns_400(sync_client):
    _setup_sync_item_view(sync_client)
    sync_client.get("/items/", params={"range": "notjson"}, assert_status_code=400)


def test_sync_react_admin_invalid_filter_json_returns_400(sync_client):
    _setup_sync_item_view(sync_client)
    sync_client.get("/items/", params={"filter": "notjson"}, assert_status_code=400)


def test_sync_react_admin_unknown_filter_field_returns_400(sync_client):
    _setup_sync_item_view(sync_client)
    sync_client.get(
        "/items/", params={"filter": '{"nonexistent":"value"}'}, assert_status_code=400
    )


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


def test_parse_range_uses_custom_default_page_size():
    """parse_react_admin_range respects the default_page_size argument."""
    assert parse_react_admin_range(None, default_page_size=10) == (0, 9)
    assert parse_react_admin_range(None, default_page_size=50) == (0, 49)


def test_default_page_size_class_attribute_overrides_default(client):
    """A view subclass can override default_page_size to change implicit pagination."""

    class Widget(fr.IDBase):
        name: Mapped[str]

    class WidgetSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class WidgetView(fr.AsyncReactAdminView):
        prefix = "/widgets"
        model = Widget
        schema = WidgetSchema
        default_page_size = 3

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

    class ScopedItem(fr.IDBase):
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

        def build_query(self):
            return super().build_query().where(ScopedItem.hidden.is_(False))

    create_tables()
    client.post("/scoped-items/", json={"name": "visible", "hidden": False})
    client.post("/scoped-items/", json={"name": "hidden", "hidden": True})

    resp = client.get("/scoped-items/")
    names = sorted(item["name"] for item in resp.json())
    assert names == ["visible"]  # the hidden row is not leaked by the RA list
    assert resp.headers["Content-Range"].endswith("/1")  # total respects scope


def test_react_admin_list_uses_handle_get_many_get_many_and_response_seams(client):
    """React Admin list is still a route-shell replacement: it should delegate
    through the standard request handler, domain method, and response chokepoint."""

    events: list[tuple[str, object | None]] = []

    class SeamItem(fr.IDBase):
        name: Mapped[str]

    class SeamItemSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class SeamItemView(fr.AsyncReactAdminView):
        prefix = "/seam-items"
        model = SeamItem
        schema = SeamItemSchema

        async def handle_get_many(self, query_params):
            events.append(("handle_get_many", None))
            result = await super().handle_get_many(query_params)
            return fr.ListingResult(
                result.objects, result.total_count + 10, result.query_params
            )

        async def get_many(self, query_params):
            events.append(("get_many", None))
            return await super().get_many(query_params)

        def to_response(self, obj_or_list, shape=fr.ResponseShape.SINGLE):
            events.append(("to_response", shape))
            return super().to_response(obj_or_list, shape)

    create_tables()
    client.post("/seam-items/", json={"name": "a"})
    events.clear()

    response = client.get("/seam-items/")

    assert [name for name, _ in events] == [
        "handle_get_many",
        "get_many",
        "to_response",
    ]
    assert events[-1] == ("to_response", fr.ResponseShape.LISTING)
    assert response.json()[0]["name"] == "a"
    assert response.headers["Content-Range"].endswith("/11")


def test_sync_react_admin_list_uses_handle_get_many_get_many_and_response_seams(
    sync_client,
):
    events: list[tuple[str, object | None]] = []

    class SyncSeamItem(fr.IDBase):
        name: Mapped[str]

    class SyncSeamItemSchema(fr.IDSchema):
        name: str

    @fr.include_view(sync_client.app)
    class SyncSeamItemView(fr.ReactAdminView):
        prefix = "/sync-seam-items"
        model = SyncSeamItem
        schema = SyncSeamItemSchema

        def handle_get_many(self, query_params):
            events.append(("handle_get_many", None))
            result = super().handle_get_many(query_params)
            return fr.ListingResult(
                result.objects, result.total_count + 10, result.query_params
            )

        def get_many(self, query_params):
            events.append(("get_many", None))
            return super().get_many(query_params)

        def to_response(self, obj_or_list, shape=fr.ResponseShape.SINGLE):
            events.append(("to_response", shape))
            return super().to_response(obj_or_list, shape)

    fr.DataclassBase.metadata.create_all(_fr_globals.make_session.kw["bind"])
    sync_client.post("/sync-seam-items/", json={"name": "a"})
    events.clear()

    response = sync_client.get("/sync-seam-items/")

    assert [name for name, _ in events] == [
        "handle_get_many",
        "get_many",
        "to_response",
    ]
    assert events[-1] == ("to_response", fr.ResponseShape.LISTING)
    assert response.json()[0]["name"] == "a"
    assert response.headers["Content-Range"].endswith("/11")
