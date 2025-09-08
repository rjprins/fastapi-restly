"""Test the shop example."""

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi_restly import settings
from fastapi_restly.testing import (
    create_test_client,
    get_test_database_url,
    setup_test_database,
)
from httpx import AsyncClient

from shop.main import app


root = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def database_tables():
    settings.async_database_url = "sqlite+aiosqlite:///:memory:"
    alembic_cfg = Config(root / "alembic.ini")
    alembic_cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(alembic_cfg, "head")


@pytest.fixture
def client(database_tables) -> AsyncClient:
    return create_test_client(app)


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
        "PUT /customers/{id}",
        "DELETE /customers/{id}",
        "GET /products/",
        "POST /products/",
        "GET /products/{id}",
        "PUT /products/{id}",
        "DELETE /products/{id}",
        "GET /orders/",
        "POST /orders/",
        "GET /orders/{id}",
        "PUT /orders/{id}",
        "DELETE /orders/{id}",
    ]
    with open(root / "openapi.json", "w") as fp:
        json.dump(response.json(), fp, indent=2)


def test_orders_rest(client):
    response = client.post("/customers/", json={"email": "test@example.com"})
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["email"] == "test@example.com"
