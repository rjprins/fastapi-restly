"""A listing next to query parameters it does not generate.

A query parameter that a dependency reads, at any level, or the key of an
``APIKeyQuery`` passes the unknown-key guard like a filter. A custom listing
takes its own typed query parameters beside ``query_params``, and OpenAPI
lists every filter beside them instead of one collapsed ``query_params``
object. An unknown key still answers 422. Sync and async.
"""

import asyncio
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Optional

import pydantic
import pytest
from fastapi import Depends, FastAPI
from fastapi.security import APIKeyQuery
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient


def api_key(api_key: str = "") -> str:
    return api_key


token = APIKeyQuery(name="token", auto_error=False)


class SearchMode(str, Enum):
    fast = "fast"
    exact = "exact"


@pytest.fixture(params=["sync", "async"])
def flavor(request: pytest.FixtureRequest) -> str:
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
def client(flavor: str, level: str, request: pytest.FixtureRequest) -> RestlyTestClient:
    sync = flavor == "sync"

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


def test_a_react_admin_listing_accepts_a_key_that_a_dependency_reads(flavor, request):
    sync = flavor == "sync"

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


@pytest.fixture
def search_client(flavor: str, request: pytest.FixtureRequest) -> RestlyTestClient:
    sync = flavor == "sync"

    class Item(fr.IDBase):
        name: Mapped[str]

    class ItemRead(fr.IDSchema):
        name: str

    app = FastAPI()

    if sync:

        class ItemView(fr.RestView):
            prefix = "/items"
            model = Item
            schema = ItemRead
            dependencies = [Depends(api_key)]

            @fr.get("/search")
            def search(self, query_params, mode: SearchMode):
                result = self.handle_get_many(query_params)
                return {"mode": mode, "names": [item.name for item in result.objects]}

    else:

        class ItemView(fr.AsyncRestView):  # type: ignore[no-redef]
            prefix = "/items"
            model = Item
            schema = ItemRead
            dependencies = [Depends(api_key)]

            @fr.get("/search")
            async def search(self, query_params, mode: SearchMode):
                result = await self.handle_get_many(query_params)
                return {"mode": mode, "names": [item.name for item in result.objects]}

    fr.include_view(app, ItemView)
    _create_tables(request, sync)
    test_client = RestlyTestClient(app)
    for name in ("Desk", "Lamp"):
        test_client.post("/items/", json={"name": name})
    return test_client


def test_a_custom_listing_takes_its_own_query_parameter(search_client):
    response = search_client.get(
        "/items/search", params={"name": "Desk", "mode": "fast", "api_key": "k"}
    )

    assert response.json() == {"mode": "fast", "names": ["Desk"]}


def test_the_custom_listing_validates_its_parameter(search_client):
    response = search_client.get(
        "/items/search", params={"mode": "slowest"}, assert_status_code=422
    )

    assert [error["loc"] for error in response.json()["detail"]] == [["query", "mode"]]


def test_the_custom_listing_rejects_an_unknown_key(search_client):
    response = search_client.get(
        "/items/search", params={"mode": "fast", "typo": "1"}, assert_status_code=422
    )

    assert response.json()["detail"][0]["loc"] == ["query", "typo"]


@pytest.mark.parametrize(
    "path, own", [("/items", ["api_key"]), ("/items/search", ["mode", "api_key"])]
)
def test_openapi_lists_every_filter_beside_other_parameters(search_client, path, own):
    operation = search_client.app.openapi()["paths"][path]["get"]
    names = [parameter["name"] for parameter in operation["parameters"]]

    assert "query_params" not in names
    assert set(own) <= set(names)
    assert {"page", "page_size", "sort", "name", "name__in"} <= set(names)
    assert "422" in operation["responses"]


class HandWrittenParams(pydantic.BaseModel):
    created_after: Optional[datetime] = pydantic.Field(None, alias="createdAfter")
    tags: list[str] = []
    numbers: Optional[pydantic.Json[list[int]]] = None


@pytest.fixture
def hand_written_client(sync_db) -> RestlyTestClient:
    class Item(fr.IDBase):
        name: Mapped[str]

    app = FastAPI()

    @fr.include_view(app)
    class ItemView(fr.RestView):
        prefix = "/items"
        model = Item
        listing_param_schema = HandWrittenParams

        @fr.get("/custom")
        def custom(self, query_params) -> dict[str, Any]:
            return query_params.model_dump(mode="json")

    return RestlyTestClient(app)


def test_a_hand_written_grammar_reads_keys_as_a_query_model_does(hand_written_client):
    response = hand_written_client.get(
        "/items/custom?createdAfter=2024-01-02T00:00:00&tags=a&tags=b&numbers=[1,2]"
    )

    assert response.json() == {
        "created_after": "2024-01-02T00:00:00",
        "tags": ["a", "b"],
        "numbers": [1, 2],
    }


def test_a_hand_written_grammar_rejects_the_python_name_of_an_alias(
    hand_written_client,
):
    response = hand_written_client.get(
        "/items/custom?created_after=2024-01-02T00:00:00", assert_status_code=422
    )

    assert response.json()["detail"][0]["loc"] == ["query", "created_after"]


def test_an_empty_grammar_declares_no_parameter(sync_db):
    class Item(fr.IDBase):
        name: Mapped[str]

    class NoParams(pydantic.BaseModel):
        pass

    app = FastAPI()

    @fr.include_view(app)
    class ItemView(fr.RestView):
        prefix = "/items"
        model = Item
        listing_param_schema = NoParams

    operation = app.openapi()["paths"]["/items"]["get"]
    assert operation.get("parameters", []) == []
