"""Sync vs async view parity tests (C5).

For the same logical model + schema, the sync `RestView` and async
`AsyncRestView` should produce identical HTTP response shapes for every
CRUD verb. Each scenario is parametrized over both view types so adding
a new scenario automatically covers both implementations.
"""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Mapped, sessionmaker
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr
from fastapi_restly.db import fr_globals

# ---------------------------------------------------------------------------
# A pair of fixtures that each yield a TestClient with one view registered.
# The view models the same Pydantic schema; only the View base class
# differs (sync vs async).
# ---------------------------------------------------------------------------


def _save_globals():
    return {
        "database_url": fr_globals.database_url,
        "make_session": fr_globals.make_session,
        "sync_session_generator": fr_globals.sync_session_generator,
    }


def _restore_globals(saved):
    fr_globals.database_url = saved["database_url"]
    fr_globals.make_session = saved["make_session"]
    fr_globals.sync_session_generator = saved["sync_session_generator"]


@pytest.fixture
def sync_client() -> Iterator[TestClient]:
    saved = _save_globals()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    make_session = sessionmaker(bind=engine, expire_on_commit=False)
    fr.configure(make_session=make_session)

    class Gadget(fr.IDBase):
        name: Mapped[str]
        price: Mapped[float]

    class GadgetSchema(fr.IDSchema):
        name: str
        price: float

    app = FastAPI()

    @fr.include_view(app)
    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

    fr.DataclassBase.metadata.create_all(engine)
    try:
        yield TestClient(app)
    finally:
        engine.dispose()
        _restore_globals(saved)


@pytest.fixture
def async_client() -> Iterator[TestClient]:
    saved = _save_globals()

    fr.configure(async_database_url="sqlite+aiosqlite:///:memory:")

    class GadgetA(fr.IDBase):
        name: Mapped[str]
        price: Mapped[float]

    class GadgetASchema(fr.IDSchema):
        name: str
        price: float

    app = FastAPI()

    @fr.include_view(app)
    class GadgetView(fr.AsyncRestView):
        prefix = "/gadgets"
        model = GadgetA
        schema = GadgetASchema

    # Create tables on the async engine.
    import asyncio

    async def _create():
        engine = fr.get_async_engine()
        async with engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)

    asyncio.run(_create())

    try:
        yield TestClient(app)
    finally:
        _restore_globals(saved)


# ---------------------------------------------------------------------------
# Parametrized scenarios — each one runs for both view types via the
# `client_fixture` indirect parameter.
# ---------------------------------------------------------------------------


@pytest.fixture
def view_client(request) -> TestClient:
    """Return either the sync or async client based on the param.

    Uses getfixturevalue so we only build the fixture we need, avoiding
    cross-contamination from creating both engines/apps in the same test.
    """
    fixture_name = "sync_client" if request.param == "sync" else "async_client"
    return request.getfixturevalue(fixture_name)


@pytest.mark.parametrize("view_client", ["sync", "async"], indirect=True)
def test_create_returns_201_and_id(view_client: TestClient):
    response = view_client.post("/gadgets/", json={"name": "Knob", "price": 1.5})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Knob"
    assert body["price"] == 1.5
    assert isinstance(body["id"], int)


@pytest.mark.parametrize("view_client", ["sync", "async"], indirect=True)
def test_list_returns_200_and_array(view_client: TestClient):
    view_client.post("/gadgets/", json={"name": "A", "price": 1.0})
    view_client.post("/gadgets/", json={"name": "B", "price": 2.0})

    response = view_client.get("/gadgets/")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 2
    assert {item["name"] for item in body} == {"A", "B"}
    # Same shape: id, name, price keys (order doesn't matter)
    assert set(body[0].keys()) == {"id", "name", "price"}


@pytest.mark.parametrize("view_client", ["sync", "async"], indirect=True)
def test_get_existing_returns_200(view_client: TestClient):
    created = view_client.post("/gadgets/", json={"name": "Knob", "price": 1.5}).json()

    response = view_client.get(f"/gadgets/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


@pytest.mark.parametrize("view_client", ["sync", "async"], indirect=True)
def test_get_missing_returns_404(view_client: TestClient):
    response = view_client.get("/gadgets/99999")
    assert response.status_code == 404


@pytest.mark.parametrize("view_client", ["sync", "async"], indirect=True)
def test_patch_updates_partially(view_client: TestClient):
    created = view_client.post("/gadgets/", json={"name": "Old", "price": 1.0}).json()

    response = view_client.patch(
        f"/gadgets/{created['id']}", json={"name": "New", "price": 1.0}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["name"] == "New"
    assert body["price"] == 1.0


@pytest.mark.parametrize("view_client", ["sync", "async"], indirect=True)
def test_patch_missing_returns_404(view_client: TestClient):
    response = view_client.patch(
        "/gadgets/99999", json={"name": "X", "price": 0.0}
    )
    assert response.status_code == 404


@pytest.mark.parametrize("view_client", ["sync", "async"], indirect=True)
def test_delete_returns_204(view_client: TestClient):
    created = view_client.post("/gadgets/", json={"name": "Z", "price": 1.0}).json()

    response = view_client.delete(f"/gadgets/{created['id']}")
    assert response.status_code == 204
    # And the resource is gone
    follow_up = view_client.get(f"/gadgets/{created['id']}")
    assert follow_up.status_code == 404


@pytest.mark.parametrize("view_client", ["sync", "async"], indirect=True)
def test_delete_missing_returns_404(view_client: TestClient):
    response = view_client.delete("/gadgets/99999")
    assert response.status_code == 404


@pytest.mark.parametrize("view_client", ["sync", "async"], indirect=True)
def test_create_with_invalid_payload_returns_422(view_client: TestClient):
    """Both sync and async should reject missing required fields with 422."""
    response = view_client.post("/gadgets/", json={"name": "no price"})
    assert response.status_code == 422
