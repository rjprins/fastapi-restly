"""
Contract tests for AsyncReactAdminView - initial slice: list endpoint.

Covers the ra-data-simple-rest wire contract for GET /:
- plain JSON array body
- Content-Range header
- sort, range, and filter translation
- error handling for malformed params
"""
import json

from sqlalchemy.orm import Mapped

import fastapi_restly as fr

from .conftest import create_tables


def _setup_item_view(client):
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


# --- Response body ---


def test_react_admin_list_returns_plain_array_body(client):
    _setup_item_view(client)
    client.post("/items/", json={"name": "Foo", "price": 1.0})
    client.post("/items/", json={"name": "Bar", "price": 2.0})

    data = client.get("/items/").json()
    assert isinstance(data, list)
    assert len(data) == 2


def test_react_admin_list_returns_zero_total_in_content_range_for_empty_result(client):
    _setup_item_view(client)
    response = client.get("/items/")
    assert "/0" in response.headers["content-range"]


# --- Content-Range header ---


def test_react_admin_list_sets_content_range_header(client):
    _setup_item_view(client)
    client.post("/items/", json={"name": "Foo", "price": 1.0})

    response = client.get("/items/")
    assert "content-range" in response.headers
    assert "/1" in response.headers["content-range"]


# --- Range (pagination) ---


def test_react_admin_range_translates_to_limit_offset(client):
    _setup_item_view(client)
    for i in range(10):
        client.post("/items/", json={"name": f"Item {i:02d}", "price": float(i)})

    response = client.get("/items/", params={"range": "[0,2]"})
    data = response.json()
    assert len(data) == 3  # indices 0, 1, 2 (inclusive)
    assert "items 0-2/10" in response.headers["content-range"]


# --- Sort ---


def test_react_admin_sort_asc_translates_to_model_ordering(client):
    _setup_item_view(client)
    client.post("/items/", json={"name": "Zebra", "price": 1.0})
    client.post("/items/", json={"name": "Apple", "price": 2.0})

    data = client.get("/items/", params={"sort": '["name","ASC"]'}).json()
    names = [item["name"] for item in data]
    assert names == sorted(names)


def test_react_admin_sort_desc_translates_to_model_ordering(client):
    _setup_item_view(client)
    client.post("/items/", json={"name": "Zebra", "price": 1.0})
    client.post("/items/", json={"name": "Apple", "price": 2.0})

    data = client.get("/items/", params={"sort": '["name","DESC"]'}).json()
    names = [item["name"] for item in data]
    assert names == sorted(names, reverse=True)


# --- Filter ---


def test_react_admin_list_supports_empty_filter_object(client):
    _setup_item_view(client)
    client.post("/items/", json={"name": "Foo", "price": 1.0})

    data = client.get("/items/", params={"filter": "{}"}).json()
    assert len(data) == 1


def test_react_admin_filter_scalar_field_translates_to_equality_filter(client):
    _setup_item_view(client)
    client.post("/items/", json={"name": "Foo", "price": 1.0})
    client.post("/items/", json={"name": "Bar", "price": 2.0})

    data = client.get("/items/", params={"filter": '{"name":"Foo"}'}).json()
    assert len(data) == 1
    assert data[0]["name"] == "Foo"


def test_react_admin_filter_id_array_translates_to_in_filter(client):
    _setup_item_view(client)
    r1 = client.post("/items/", json={"name": "Foo", "price": 1.0}).json()
    r2 = client.post("/items/", json={"name": "Bar", "price": 2.0}).json()
    client.post("/items/", json={"name": "Baz", "price": 3.0})

    ids = [r1["id"], r2["id"]]
    data = client.get("/items/", params={"filter": json.dumps({"id": ids})}).json()
    assert len(data) == 2
    assert {item["id"] for item in data} == set(ids)


# --- Error handling ---


def test_react_admin_invalid_sort_json_returns_400(client):
    _setup_item_view(client)
    client.get("/items/", params={"sort": "notjson"}, assert_status_code=400)


def test_react_admin_invalid_range_json_returns_400(client):
    _setup_item_view(client)
    client.get("/items/", params={"range": "notjson"}, assert_status_code=400)


def test_react_admin_invalid_filter_json_returns_400(client):
    _setup_item_view(client)
    client.get("/items/", params={"filter": "notjson"}, assert_status_code=400)


def test_react_admin_unknown_filter_field_returns_400(client):
    _setup_item_view(client)
    client.get(
        "/items/",
        params={"filter": '{"nonexistent":"value"}'},
        assert_status_code=400,
    )
