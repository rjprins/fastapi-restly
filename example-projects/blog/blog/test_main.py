import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from blog.main import app

root = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def database_tables():
    alembic_cfg = Config(root / "alembic.ini")
    alembic_cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(alembic_cfg, "head")


def test_openapi_spec():
    client = TestClient(app)

    response = client.get("/openapi.json")
    spec = response.json()
    routes: list[str] = []
    for path in spec["paths"]:
        for method in spec["paths"][path]:
            routes.append(f"{method.upper()} {path}")
    assert routes == [
        "GET /blogs/",
        "POST /blogs/",
        "GET /blogs/{id}",
        "PUT /blogs/{id}",
        "DELETE /blogs/{id}",
    ]
    with open(root / "openapi.json", "w") as fp:
        json.dump(response.json(), fp, indent=2)


def test_get_blog_listing():
    client = TestClient(app)

    response = client.post("/blogs/", json={"title": "Yolo"})
    assert response.is_success

    response = client.get("/blogs/")
    assert response.is_success
