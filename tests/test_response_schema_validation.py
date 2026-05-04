import pydantic
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

from .conftest import create_tables


class ResponseUserRead(fr.IDSchema):
    name: str
    email: str
    password: fr.WriteOnly[str]

    @pydantic.field_validator("email", mode="after")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()

    @pydantic.field_serializer("name")
    def serialize_name(self, value: str) -> str:
        return f"user:{value}"


def test_to_response_schema_runs_response_field_validators_and_serializers():
    class ResponseValidationUser(fr.IDBase):
        name: Mapped[str]
        email: Mapped[str]
        password: Mapped[str]

    class ResponseUserView(fr.AsyncRestView):
        model = ResponseValidationUser
        schema = ResponseUserRead

    user = ResponseValidationUser(
        name="Ada", email="ADA@EXAMPLE.COM", password="secret"
    )
    user.id = 1

    schema_obj = ResponseUserView().to_response_schema(user)

    assert isinstance(schema_obj, ResponseUserRead)
    assert schema_obj.email == "ada@example.com"

    payload = schema_obj.model_dump(mode="json")
    assert payload["name"] == "user:Ada"
    assert "password" not in payload


def test_response_serialization_runs_through_fastapi_response_model(client):
    class ResponseApiUser(fr.IDBase):
        name: Mapped[str]
        email: Mapped[str]
        password: Mapped[str]

    @fr.include_view(client.app)
    class UserView(fr.AsyncRestView):
        prefix = "/response-users"
        model = ResponseApiUser
        schema = ResponseUserRead

    create_tables()

    response = client.post(
        "/response-users/",
        json={"name": "Ada", "email": "ADA@EXAMPLE.COM", "password": "secret"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["email"] == "ada@example.com"
    assert payload["name"] == "user:Ada"
    assert "password" not in payload
