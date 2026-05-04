"""
React Admin acceptance tests for the shop example.

These tests prove the ra-data-simple-rest wire contract works on real models:
- Customer  — integer primary key
- Product   — UUID primary key
- Order     — relationships + timestamps
"""

import json
from uuid import UUID

import pytest
from shop.main import app

from fastapi_restly.testing import RestlyTestClient


@pytest.fixture
def client() -> RestlyTestClient:
    with RestlyTestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post_customer(client, email="test@example.com"):
    return client.post("/customers/", json={"email": email}).json()


def _post_product(client, name="Widget", price=9.99):
    return client.post("/products/", json={"name": name, "price": price}).json()


def _post_order(client, customer_id, product_ids=()):
    return client.post(
        "/orders/",
        json={
            "customer_id": customer_id,
            "products": [{"id": pid} for pid in product_ids],
        },
    ).json()


# ---------------------------------------------------------------------------
# Customer — integer primary key
# ---------------------------------------------------------------------------


def test_customers_list_returns_plain_array(client):
    _post_customer(client, "a@example.com")
    _post_customer(client, "b@example.com")

    data = client.get("/customers/").json()
    assert isinstance(data, list)
    assert len(data) == 2


def test_customers_list_sets_content_range_header(client):
    _post_customer(client)

    response = client.get("/customers/")
    assert "content-range" in response.headers
    assert "/1" in response.headers["content-range"]


def test_customers_sort_and_range(client):
    for letter in "dcba":
        _post_customer(client, f"{letter}@example.com")

    response = client.get(
        "/customers/", params={"sort": '["email","ASC"]', "range": "[0,1]"}
    )
    data = response.json()
    assert len(data) == 2
    assert data[0]["email"] < data[1]["email"]
    assert "items 0-1/4" in response.headers["content-range"]


def test_customers_filter_by_email(client):
    _post_customer(client, "find@example.com")
    _post_customer(client, "other@example.com")

    data = client.get(
        "/customers/", params={"filter": '{"email":"find@example.com"}'}
    ).json()
    assert len(data) == 1
    assert data[0]["email"] == "find@example.com"


def test_customers_get_many_via_id_array(client):
    c1 = _post_customer(client, "one@example.com")
    c2 = _post_customer(client, "two@example.com")
    _post_customer(client, "three@example.com")

    ids = [c1["id"], c2["id"]]
    data = client.get("/customers/", params={"filter": json.dumps({"id": ids})}).json()
    assert len(data) == 2
    assert {r["id"] for r in data} == set(ids)


# ---------------------------------------------------------------------------
# Product — UUID primary key
# ---------------------------------------------------------------------------


def test_products_list_returns_plain_array(client):
    _post_product(client, "Gadget", 5.0)
    _post_product(client, "Doohickey", 10.0)

    data = client.get("/products/").json()
    assert isinstance(data, list)
    assert len(data) == 2


def test_products_list_sets_content_range_header(client):
    _post_product(client)

    response = client.get("/products/")
    assert "content-range" in response.headers
    assert "/1" in response.headers["content-range"]


def test_products_filter_by_name(client):
    _post_product(client, "FindMe", 1.0)
    _post_product(client, "Ignore", 2.0)

    data = client.get("/products/", params={"filter": '{"name":"FindMe"}'}).json()
    assert len(data) == 1
    assert data[0]["name"] == "FindMe"


def test_products_get_many_via_uuid_id_array(client):
    p1 = _post_product(client, "Alpha", 1.0)
    p2 = _post_product(client, "Beta", 2.0)
    _post_product(client, "Gamma", 3.0)

    ids = [p1["id"], p2["id"]]
    assert UUID(ids[0])  # confirm these are valid UUIDs
    data = client.get("/products/", params={"filter": json.dumps({"id": ids})}).json()
    assert len(data) == 2
    assert {r["id"] for r in data} == set(ids)


def test_products_sort_by_price_desc(client):
    for price in [3.0, 1.0, 2.0]:
        _post_product(client, f"Item {price}", price)

    data = client.get("/products/", params={"sort": '["price","DESC"]'}).json()
    prices = [item["price"] for item in data]
    assert prices == sorted(prices, reverse=True)


# ---------------------------------------------------------------------------
# Order — relationships + timestamps
# ---------------------------------------------------------------------------


def test_orders_list_returns_plain_array(client):
    c = _post_customer(client)
    _post_order(client, c["id"])
    _post_order(client, c["id"])

    data = client.get("/orders/").json()
    assert isinstance(data, list)
    assert len(data) == 2


def test_orders_list_sets_content_range_header(client):
    c = _post_customer(client)
    _post_order(client, c["id"])

    response = client.get("/orders/")
    assert "content-range" in response.headers
    assert "/1" in response.headers["content-range"]


def test_orders_filter_by_customer_id(client):
    c1 = _post_customer(client, "owner@example.com")
    c2 = _post_customer(client, "other@example.com")
    _post_order(client, c1["id"])
    _post_order(client, c1["id"])
    _post_order(client, c2["id"])

    data = client.get(
        "/orders/", params={"filter": json.dumps({"customer_id": c1["id"]})}
    ).json()
    # Filter returns only the 2 orders belonging to c1, not the 1 belonging to c2
    assert len(data) == 2


def test_orders_list_includes_timestamps(client):
    c = _post_customer(client)
    _post_order(client, c["id"])

    data = client.get("/orders/").json()
    order = data[0]
    assert "created_at" in order
    assert "updated_at" in order


def test_sort_by_relationship_returns_400(client):
    _post_customer(client)

    response = client.get(
        "/customers/", params={"sort": '["orders","ASC"]'}, assert_status_code=400
    )
    assert "relationship" in response.json()["detail"]
