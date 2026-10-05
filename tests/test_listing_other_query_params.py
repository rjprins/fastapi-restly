"""A listing next to query parameters it does not generate.

A query parameter that a dependency reads, at any level, or the key of an
``APIKeyQuery`` passes the unknown-key guard like a filter. An unknown key
still answers 422. Sync and async.
"""

import asyncio
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.security import APIKeyQuery
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient


def api_key(api_key: str = "") -> str:
    return api_key


token = APIKeyQuery(name="token", auto_error=False)


@pytest.fixture(params=["sync", "async"])
def mode(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture(params=["app", "view", "class attribute", "APIKeyQuery"])
def level(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def key(level: str) -> str:
    return "token" if level == "APIKeyQuery" else "api_key"


def _create_tables(request: pytest.FixtureRequest, sync: bool) -> None:
    if sync:
        engine, _ = request.getfixturevalue("sync_db")
        fr.DataclassBase.metadata.create_all(engine)
    else:
        asyncio.run(fr.db.async_create_all(fr.DataclassBase))


@pytest.fixture
def client(mode: str, level: str, request: pytest.FixtureRequest) -> RestlyTestClient:
    sync = mode == "sync"

    class Item(fr.IDBase):
        name: Mapped[str]

    class ItemRead(fr.IDSchema):
        name: str

    app_dependencies = {"app": [Depends(api_key)], "APIKeyQuery": [Depends(token)]}
    app = FastAPI(dependencies=app_dependencies.get(level, []))

    class ItemView(fr.RestView if sync else fr.AsyncRestView):  # type: ignore[misc]
        prefix = "/items"
        model = Item
        schema = ItemRead
        if level == "view":
            dependencies = [Depends(api_key)]
        if level == "class attribute":
            key: Annotated[str, Depends(api_key)]

    fr.include_view(app, ItemView)
    _create_tables(request, sync)
    test_client = RestlyTestClient(app)
    for name in ("Desk", "Lamp"):
        test_client.post("/items/", json={"name": name})
    return test_client


def test_a_key_that_the_route_reads_is_accepted(client, key):
    response = client.get("/items/", params={key: "secret", "name": "Desk"})

    assert [item["name"] for item in response.json()["data"]] == ["Desk"]


def test_an_unknown_key_is_still_rejected(client, key):
    response = client.get(
        "/items/", params={key: "secret", "typo": "1"}, assert_status_code=422
    )

    assert response.json()["detail"] == [
        {
            "type": "extra_forbidden",
            "loc": ["query", "typo"],
            "msg": "Unknown query parameter 'typo'",
            "input": "1",
        }
    ]


def test_a_react_admin_listing_accepts_a_key_that_a_dependency_reads(mode, request):
    sync = mode == "sync"

    class Item(fr.IDBase):
        name: Mapped[str]

    app = FastAPI()

    class ItemView(fr.ReactAdminView if sync else fr.AsyncReactAdminView):  # type: ignore[misc]
        prefix = "/items"
        model = Item
        dependencies = [Depends(api_key)]

    fr.include_view(app, ItemView)
    _create_tables(request, sync)
    client = RestlyTestClient(app)
    client.post("/items", json={"name": "Desk"})

    response = client.get("/items", params={"api_key": "secret", "range": "[0,9]"})

    assert [item["name"] for item in response.json()] == ["Desk"]
    client.get("/items", params={"typo": "1"}, assert_status_code=422)
