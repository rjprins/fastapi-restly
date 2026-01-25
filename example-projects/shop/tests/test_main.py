"""Test the shop example."""

import pytest
from fastapi_restly.testing import RestlyTestClient

from shop.main import app


@pytest.fixture
def client() -> RestlyTestClient:
    return RestlyTestClient(app)


def test_openapi_spec(client):
    response = client.get("/openapi.json")
    spec = response.json()
    routes: list[str] = []
    for path in spec["paths"]:
        for method in spec["paths"][path]:
            routes.append(f"{method.upper()} {path}")
    assert routes == [
        "GET /customers/",
        "POST /customers/",
        "GET /customers/{id}",
        "PATCH /customers/{id}",
        "DELETE /customers/{id}",
        "GET /products/",
        "POST /products/",
        "GET /products/{id}",
        "PATCH /products/{id}",
        "DELETE /products/{id}",
        "GET /orders/",
        "POST /orders/",
        "GET /orders/{id}",
        "PATCH /orders/{id}",
        "DELETE /orders/{id}",
    ]


def test_orders_rest(client):
    response = client.post("/customers/", json={"email": "test@example.com"})
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["email"] == "test@example.com"
