import pydantic
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

from .conftest import create_tables


def test_field_validator_inherited_by_create_and_patch_request_schemas(client):
    class FieldValidatorUser(fr.IDBase):
        name: Mapped[str]
        email: Mapped[str]

    class FieldValidatorUserSchema(fr.IDSchema):
        name: str
        email: str

        @pydantic.field_validator("email")
        @classmethod
        def reject_known_bad_email(cls, value: str) -> str:
            if value == "bad@example.com":
                raise ValueError("bad email")
            return value

    @fr.include_view(client.app)
    class FieldValidatorUserView(fr.AsyncRestView):
        prefix = "/field-validator-users"
        model = FieldValidatorUser
        schema = FieldValidatorUserSchema

    create_tables()

    create_response = client.post(
        "/field-validator-users/",
        json={"name": "Ada", "email": "bad@example.com"},
        assert_status_code=422,
    )
    assert create_response.status_code == 422

    user_response = client.post(
        "/field-validator-users/", json={"name": "Ada", "email": "ada@example.com"}
    )
    user_id = user_response.json()["id"]

    patch_response = client.patch(
        f"/field-validator-users/{user_id}",
        json={"email": "bad@example.com"},
        assert_status_code=422,
    )
    assert patch_response.status_code == 422


def test_after_field_validator_inherited_by_create_and_patch_request_schemas(client):
    class AfterFieldValidatorUser(fr.IDBase):
        name: Mapped[str]
        email: Mapped[str]

    class AfterFieldValidatorUserSchema(fr.IDSchema):
        name: str
        email: str

        @pydantic.field_validator("email", mode="after")
        @classmethod
        def reject_known_bad_email(cls, value: str) -> str:
            if value == "after-bad@example.com":
                raise ValueError("bad email after validation")
            return value

    @fr.include_view(client.app)
    class AfterFieldValidatorUserView(fr.AsyncRestView):
        prefix = "/after-field-validator-users"
        model = AfterFieldValidatorUser
        schema = AfterFieldValidatorUserSchema

    create_tables()

    create_response = client.post(
        "/after-field-validator-users/",
        json={"name": "Ada", "email": "after-bad@example.com"},
        assert_status_code=422,
    )
    assert create_response.status_code == 422

    user_response = client.post(
        "/after-field-validator-users/",
        json={"name": "Ada", "email": "ada@example.com"},
    )
    user_id = user_response.json()["id"]

    patch_response = client.patch(
        f"/after-field-validator-users/{user_id}",
        json={"email": "after-bad@example.com"},
        assert_status_code=422,
    )
    assert patch_response.status_code == 422


def test_model_validator_inherited_by_create_and_patch_request_schemas(client):
    class ModelValidatorUser(fr.IDBase):
        name: Mapped[str]
        email: Mapped[str]

    class ModelValidatorUserSchema(fr.IDSchema):
        name: str
        email: str

        @pydantic.model_validator(mode="after")
        def reject_matching_name_and_email(self):
            if self.name == self.email:
                raise ValueError("name and email must differ")
            return self

    @fr.include_view(client.app)
    class ModelValidatorUserView(fr.AsyncRestView):
        prefix = "/model-validator-users"
        model = ModelValidatorUser
        schema = ModelValidatorUserSchema

    create_tables()

    create_response = client.post(
        "/model-validator-users/",
        json={"name": "same@example.com", "email": "same@example.com"},
        assert_status_code=422,
    )
    assert create_response.status_code == 422

    user_response = client.post(
        "/model-validator-users/", json={"name": "Ada", "email": "ada@example.com"}
    )
    user_id = user_response.json()["id"]

    patch_response = client.patch(
        f"/model-validator-users/{user_id}",
        json={"name": "patch@example.com", "email": "patch@example.com"},
        assert_status_code=422,
    )
    assert patch_response.status_code == 422
