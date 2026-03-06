"""Regression tests for WriteOnly field exclusion in API responses."""

import fastapi_restly as fr
from fastapi_restly.schemas import WriteOnly

from .conftest import create_tables


def test_writeonly_fields_are_excluded_from_post_get_and_list(client):
    class UserSchema(fr.IDSchema):
        id: fr.ReadOnly[int]
        name: str
        email: str
        password: WriteOnly[str]

    class User(fr.IDBase):
        name: str
        email: str
        password: str

    @fr.include_view(client.app)
    class UserView(fr.AsyncAlchemyView):
        prefix = "/users"
        model = User
        schema = UserSchema

    create_tables()

    response = client.post(
        "/users/",
        json={"name": "John", "email": "john@example.com", "password": "secret123"},
    )
    assert response.status_code == 201
    created = response.json()
    assert "password" not in created

    user_id = created["id"]
    response = client.get(f"/users/{user_id}")
    assert response.status_code == 200
    fetched = response.json()
    assert "password" not in fetched

    response = client.get("/users/")
    assert response.status_code == 200
    users = response.json()
    assert users
    assert "password" not in users[0]
