import pydantic
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

from .conftest import create_tables


class ResponseUserSchema(fr.IDSchema):
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


def test_to_single_response_runs_response_field_validators_and_serializers():
    class ResponseValidationUser(fr.IDBase):
        name: Mapped[str]
        email: Mapped[str]
        password: Mapped[str]

    class ResponseUserView(fr.AsyncRestView):
        model = ResponseValidationUser
        schema = ResponseUserSchema

    user = ResponseValidationUser(
        name="Ada", email="ADA@EXAMPLE.COM", password="secret"
    )
    user.id = 1

    schema_obj = ResponseUserView().to_single_response(user)

    assert isinstance(schema_obj, ResponseUserSchema)
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
        schema = ResponseUserSchema

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


def test_to_single_response_with_a_narrowed_model_validate():
    # SQLModel overrides model_validate without by_alias and by_name.
    class NarrowSchema(pydantic.BaseModel):
        model_config = pydantic.ConfigDict(from_attributes=True)

        @classmethod
        def model_validate(cls, obj, *, strict=None, from_attributes=None):  # type: ignore[override]
            return super().model_validate(
                obj, strict=strict, from_attributes=from_attributes
            )

    class NarrowGadgetSchema(NarrowSchema):
        id: int
        display_name: str = pydantic.Field(alias="displayName")

    class NarrowGadget(fr.IDBase):
        display_name: Mapped[str]

    class NarrowGadgetView(fr.AsyncRestView):
        model = NarrowGadget
        schema = NarrowGadgetSchema

    gadget = NarrowGadget(display_name="Widget")
    gadget.id = 1

    schema_obj = NarrowGadgetView().to_single_response(gadget)

    assert isinstance(schema_obj, NarrowGadgetSchema)
    assert schema_obj.model_dump(by_alias=True) == {"id": 1, "displayName": "Widget"}
