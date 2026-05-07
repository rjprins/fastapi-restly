"""Tests for the existing-project integration patterns documented in the guide."""

from fastapi import APIRouter, FastAPI, Response
from fastapi.testclient import TestClient
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

from .conftest import create_tables


def test_restly_view_coexists_with_existing_fastapi_router():
    class User(fr.IDBase):
        name: Mapped[str]

    class UserRead(fr.IDSchema):
        name: str

    orders_router = APIRouter()

    @orders_router.get("/status")
    def order_status():
        return {"status": "hand-written"}

    app = FastAPI()
    api = APIRouter(prefix="/api")

    api.include_router(orders_router, prefix="/orders")

    class UserView(fr.AsyncRestView):
        prefix = "/users"
        model = User
        schema = UserRead

    fr.include_view(api, UserView)

    @api.get("/users/{id}/export")
    def export_user(id: int):
        return {"id": id, "format": "csv"}

    app.include_router(api)
    create_tables()

    client = TestClient(app)

    existing_response = client.get("/api/orders/status")
    assert existing_response.status_code == 200
    assert existing_response.json() == {"status": "hand-written"}

    created_response = client.post("/api/users/", json={"name": "Ada"})
    assert created_response.status_code == 201, created_response.text
    user_id = created_response.json()["id"]

    restly_response = client.get(f"/api/users/{user_id}")
    assert restly_response.status_code == 200
    assert restly_response.json() == {"id": user_id, "name": "Ada"}

    custom_response = client.get(f"/api/users/{user_id}/export")
    assert custom_response.status_code == 200
    assert custom_response.json() == {"id": user_id, "format": "csv"}


def test_excluded_restly_route_can_be_replaced_by_plain_fastapi_route():
    deleted_ids: list[int] = []

    class User(fr.IDBase):
        name: Mapped[str]

    class UserRead(fr.IDSchema):
        name: str

    app = FastAPI()
    api = APIRouter(prefix="/api")

    class UserView(fr.AsyncRestView):
        prefix = "/users"
        model = User
        schema = UserRead
        exclude_routes = (fr.ViewRoute.DELETE,)

    fr.include_view(api, UserView)

    @api.delete("/users/{id}", status_code=204)
    def delete_user(id: int):
        deleted_ids.append(id)
        return Response(status_code=204)

    app.include_router(api)
    create_tables()

    client = TestClient(app)

    created_response = client.post("/api/users/", json={"name": "Grace"})
    assert created_response.status_code == 201, created_response.text
    user_id = created_response.json()["id"]

    delete_response = client.delete(f"/api/users/{user_id}")
    assert delete_response.status_code == 204
    assert deleted_ids == [user_id]

    # The plain FastAPI route handled DELETE; the Restly delete route is absent,
    # so the row was not removed by Restly's generated CRUD handler.
    get_response = client.get(f"/api/users/{user_id}")
    assert get_response.status_code == 200
    assert get_response.json() == {"id": user_id, "name": "Grace"}
