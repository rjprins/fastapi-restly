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
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly.db import fr_globals
from fastapi_restly.testing._client import RestlyTestClient
from fastapi_restly.views._react_admin import (
    DEFAULT_REACT_ADMIN_PAGE_SIZE,
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

    fr.DataclassBase.metadata.create_all(fr_globals.make_session.kw["bind"])


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
        "/items/",
        params={"filter": '{"nonexistent":"value"}'},
        assert_status_code=400,
    )


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


def test_sync_react_admin_filter_id_array(sync_client):
    _setup_sync_item_view(sync_client)
    r1 = sync_client.post("/items/", json={"name": "Foo", "price": 1.0}).json()
    r2 = sync_client.post("/items/", json={"name": "Bar", "price": 2.0}).json()
    sync_client.post("/items/", json={"name": "Baz", "price": 3.0})

    ids = [r1["id"], r2["id"]]
    data = sync_client.get(
        "/items/", params={"filter": json.dumps({"id": ids})}
    ).json()
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
        "/items/",
        params={"filter": '{"nonexistent":"value"}'},
        assert_status_code=400,
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
