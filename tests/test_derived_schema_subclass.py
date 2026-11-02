"""A subclass of a class that Restly derived does not get the dropped fields back.

Pydantic collects a class's fields from the annotations of all its parents. A
field that a derived class dropped is no longer in the parent's fields, so
Pydantic builds it again, without its ``ReadOnly`` or ``WriteOnly`` marker. Restly
removes such a field again, unless the subclass declares it itself.
"""

import pydantic
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly.schemas._base import (
    create_model_with_optional_fields,
    create_model_without_read_only_fields,
)

from ._models import SharedBase
from .conftest import create_tables


class AccountSchema(fr.BaseSchema):
    id: fr.ReadOnly[int]
    name: str
    password: fr.WriteOnly[str] = "pw"
    is_admin: fr.ReadOnly[bool] = False


def _account_view():
    class Account(fr.IDMixin, SharedBase):
        name: Mapped[str]
        password: Mapped[str]
        is_admin: Mapped[bool] = mapped_column(default=False)

    class AccountView(fr.AsyncRestView):
        prefix = "/accounts"
        model = Account
        schema = AccountSchema

    return Account, AccountView


def test_a_subclass_of_the_update_class_stays_without_the_dropped_fields():
    class MyUpdate(create_model_with_optional_fields(AccountSchema)):
        note: str = ""

    assert set(MyUpdate.model_fields) == {"name", "password", "note"}


def test_a_subclass_of_the_create_class_stays_without_the_dropped_fields():
    class MyCreate(create_model_without_read_only_fields(AccountSchema)):
        note: str = ""

    assert set(MyCreate.model_fields) == {"name", "password", "note"}
    assert MyCreate(name="a").password == "pw"


def test_a_subclass_of_the_response_class_stays_without_the_dropped_fields():
    class MyResponse(fr.schemas.derive_schema_response(AccountSchema)):
        note: str = ""

    assert set(MyResponse.model_fields) == {"id", "name", "is_admin", "note"}
    row = MyResponse(id=1, name="a", password="secret")
    assert "password" not in row.model_dump()


def test_a_field_in_omit_stays_out_of_a_subclass():
    class MySchema(fr.BaseSchema):
        id: int
        name: str

    derived = create_model_without_read_only_fields(MySchema, omit={"id"})

    class MyCreate(derived):
        note: str = ""

    assert set(MyCreate.model_fields) == {"name", "note"}


def test_a_subclass_of_a_subclass_stays_without_the_dropped_fields():
    class First(create_model_without_read_only_fields(AccountSchema)):
        first: str = ""

    class Second(First):
        second: str = ""

    assert set(Second.model_fields) == {"name", "password", "first", "second"}


def test_a_class_derived_from_a_derived_class_stays_without_the_dropped_fields():
    response = fr.schemas.derive_schema_response(AccountSchema)
    assert set(create_model_without_read_only_fields(response).model_fields) == {"name"}


def test_a_subclass_can_declare_a_dropped_field_again():
    class MyUpdate(create_model_with_optional_fields(AccountSchema)):
        is_admin: bool = False

    assert set(MyUpdate.model_fields) == {"name", "password", "is_admin"}

    class Child(MyUpdate):
        note: str = ""

    assert "is_admin" in Child.model_fields


def test_a_subclass_can_declare_a_field_in_omit_again():
    class MySchema(fr.BaseSchema):
        id: int
        name: str

    derived = create_model_without_read_only_fields(MySchema, omit={"id"})

    class MyCreate(derived):
        id: int

    assert set(MyCreate.model_fields) == {"id", "name"}


def test_a_field_from_another_parent_stays():
    class WithAdmin(pydantic.BaseModel):
        is_admin: bool = False

    class MyUpdate(WithAdmin, create_model_with_optional_fields(AccountSchema)):
        pass

    assert "is_admin" in MyUpdate.model_fields


def test_a_subclass_can_send_a_writeonly_field_it_declares_again():
    class MyResponse(fr.schemas.derive_schema_response(AccountSchema)):
        password: str = ""

    data = {"id": 1, "name": "a", "password": "shown"}
    assert MyResponse.model_validate(data).model_dump()["password"] == "shown"


def test_a_new_writeonly_field_keeps_the_writeonly_keys_of_the_parent():
    class MyResponse(fr.schemas.derive_schema_response(AccountSchema)):
        token: fr.WriteOnly[str] = "t"
        model_config = pydantic.ConfigDict(extra="allow")

    row = MyResponse.model_validate(
        {"id": 1, "name": "a", "password": "secret", "token": "secret"}
    )
    dumped = row.model_dump()
    assert "password" not in dumped
    assert "token" not in dumped


def test_a_client_cannot_write_a_readonly_field_through_a_subclass(client):
    Account, AccountView = _account_view()

    class MyUpdate(create_model_with_optional_fields(AccountSchema)):
        pass

    class View(AccountView):
        schema_update = MyUpdate

    fr.include_view(client.app, View)
    create_tables()

    created = client.post("/accounts/", json={"name": "a", "password": "p"}).json()
    client.patch(f"/accounts/{created['id']}", json={"is_admin": True})
    assert client.get(f"/accounts/{created['id']}").json()["is_admin"] is False


def test_a_view_with_a_subclass_of_the_response_class_works(client):
    Account, AccountView = _account_view()

    class MyResponse(fr.schemas.derive_schema_response(AccountSchema)):
        pass

    class View(AccountView):
        schema_response = MyResponse

    fr.include_view(client.app, View)
    create_tables()

    created = client.post("/accounts/", json={"name": "a", "password": "p"}).json()
    assert created == {"id": created["id"], "name": "a", "is_admin": False}
    schema = client.app.openapi()["components"]["schemas"]["MyResponse"]
    assert "password" not in schema["properties"]
    assert "password" not in schema.get("required", [])
