"""Test the shop example."""

import warnings

import pytest
from shop.main import app

from fastapi_restly.exc import RestlyDuplicateSchemaNameWarning
from fastapi_restly.testing import RestlyTestClient


@pytest.fixture
def client() -> RestlyTestClient:
    with RestlyTestClient(app) as client:
        yield client


def test_openapi_spec(client):
    response = client.get("/openapi.json")
    spec = response.json()
    routes: list[str] = []
    for path in spec["paths"]:
        for method in spec["paths"][path]:
            routes.append(f"{method.upper()} {path}")
    assert routes == [
        "GET /customers",
        "POST /customers",
        "PUT /customers/{customer_id}",
        "GET /customers/{customer_id}",
        "PATCH /customers/{customer_id}",
        "DELETE /customers/{customer_id}",
        "GET /products",
        "POST /products",
        "PUT /products/{product_id}",
        "GET /products/{product_id}",
        "PATCH /products/{product_id}",
        "DELETE /products/{product_id}",
        "GET /orders",
        "POST /orders",
        "PUT /orders/{order_id}",
        "GET /orders/{order_id}",
        "PATCH /orders/{order_id}",
        "DELETE /orders/{order_id}",
    ]


def test_orders_rest(client):
    response = client.post("/customers/", json={"email": "test@example.com"})
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["email"] == "test@example.com"


def test_openapi_class_names_are_unique() -> None:
    """Restly warns when two classes in the API have the same name: OpenAPI
    then shows them under long names that can change, and generated clients
    use these names as type names. This test turns the warning into an error."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", RestlyDuplicateSchemaNameWarning)
        app.openapi()
