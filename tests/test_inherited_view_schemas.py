"""A view subclass inherits the schemas its base view declares.

Registration generates a schema only where none is declared. A schema Restly
generated for a parent is rebuilt for the subclass, and a schema derived from
``schema`` is rebuilt when the subclass declares a new ``schema``. A copy that
registration generated into the subclass is not a change: it does not make a
declared schema of the base out of date.
"""

import pydantic
import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr

from .conftest import create_tables


@pytest.mark.parametrize("view_base", [fr.AsyncRestView, fr.RestView])
def test_subclass_serves_the_schema_its_base_view_declares(view_base):
    class Rec(fr.IDBase):
        name: Mapped[str]
        secret: Mapped[str]

    class RecSchema(fr.IDSchema):
        name: str

    class Base(view_base):
        model = Rec
        schema = RecSchema

    class Sub(Base):
        prefix = "/recs"

    fr.include_view(FastAPI(), Sub)

    assert Sub.schema is RecSchema
    assert set(Sub.schema_create.model_fields) == {"name"}
    assert set(Sub.schema_update.model_fields) == {"name"}
    assert "schema" not in Sub.__dict__


def test_undeclared_column_does_not_leak_through_a_subclass(client):
    class Account(fr.IDBase):
        name: Mapped[str]
        secret: Mapped[str] = mapped_column(default="hidden")

    class AccountSchema(fr.IDSchema):
        name: str

    class Base(fr.AsyncRestView):
        model = Account
        schema = AccountSchema

    @fr.include_view(client.app)
    class AccountView(Base):
        prefix = "/accounts"

    create_tables()
    created = client.post("/accounts/", json={"name": "Ann"}).json()
    assert created == {"id": created["id"], "name": "Ann"}
    assert client.get("/accounts/").json()["data"] == [created]


def test_declared_write_schemas_are_inherited():
    class Rec(fr.IDBase):
        name: Mapped[str]
        note: Mapped[str]

    class RecSchema(fr.IDSchema):
        name: str
        note: str

    class RecCreate(fr.BaseSchema):
        name: str

    class RecUpdate(fr.BaseSchema):
        note: str | None = None

    class Base(fr.AsyncRestView):
        model = Rec
        schema = RecSchema
        schema_create = RecCreate
        schema_update = RecUpdate

    class Sub(Base):
        prefix = "/recs"

    fr.include_view(FastAPI(), Sub)

    assert Sub.schema_create is RecCreate
    assert Sub.schema_update is RecUpdate


def test_redeclaring_schema_rebuilds_the_write_schemas_a_parent_declared():
    class Rec(fr.IDBase):
        name: Mapped[str]
        note: Mapped[str]

    class RecSchema(fr.IDSchema):
        name: str

    class RecCreate(fr.BaseSchema):
        name: str

    class Base(fr.AsyncRestView):
        model = Rec
        schema = RecSchema
        schema_create = RecCreate

    class WithNote(fr.IDSchema):
        name: str
        note: str

    class Sub(Base):
        prefix = "/recs"
        schema = WithNote

    fr.include_view(FastAPI(), Sub)

    assert Sub.schema is WithNote
    assert Sub.schema_create is not RecCreate
    assert set(Sub.schema_create.model_fields) == {"name", "note"}


def test_generated_schema_of_a_registered_parent_is_rebuilt_for_the_subclass():
    class Pet(fr.IDBase):
        name: Mapped[str]

    class Owner(fr.IDBase):
        email: Mapped[str]

    class PetView(fr.AsyncRestView):
        prefix = "/pets"
        model = Pet

    app = FastAPI()
    fr.include_view(app, PetView)

    class OwnerView(PetView):
        prefix = "/owners"
        model = Owner

    fr.include_view(app, OwnerView)

    assert set(PetView.schema.model_fields) == {"id", "name"}
    assert set(OwnerView.schema.model_fields) == {"id", "email"}
    assert set(OwnerView.schema_create.model_fields) == {"email"}


def test_subclass_page_size_rebuilds_the_generated_list_params():
    class Rec(fr.IDBase):
        name: Mapped[str]

    class RecSchema(fr.IDSchema):
        name: str

    class Base(fr.AsyncRestView):
        prefix = "/recs"
        model = Rec
        schema = RecSchema

    app = FastAPI()
    fr.include_view(app, Base)

    class Small(Base):
        prefix = "/small-recs"
        pagination = Base.pagination.replace(default_page_size=5)

    fr.include_view(app, Small)

    assert Base.schema_list_params is not Small.schema_list_params
    assert Small.schema_list_params.model_fields["page_size"].default == 5


def test_declared_list_params_are_inherited(client):
    class Rec(fr.IDBase):
        name: Mapped[str]
        note: Mapped[str]

    class RecSchema(fr.IDSchema):
        name: str
        note: str

    class RecListParams(pydantic.BaseModel):
        note: str | None = None

    class Base(fr.AsyncRestView):
        model = Rec
        schema = RecSchema
        schema_list_params = RecListParams

    @fr.include_view(client.app)
    class Sub(Base):
        prefix = "/recs"

    create_tables()
    assert Sub.schema_list_params is RecListParams
    client.get("/recs/?name=a", assert_status_code=422)


def test_declared_response_class_of_a_base_without_schema_is_inherited(client):
    class Vault(fr.IDBase):
        name: Mapped[str]
        secret: Mapped[str] = mapped_column(default="hidden")

    class VaultResponse(fr.IDSchema):
        name: str

    class Base(fr.AsyncRestView):
        model = Vault
        schema_response = VaultResponse

    @fr.include_view(client.app)
    class Sub(Base):
        prefix = "/vaults"

    create_tables()
    created = client.post("/vaults/", json={"name": "a", "secret": "s3"}).json()
    assert Sub.schema_response is VaultResponse
    assert created == {"id": created["id"], "name": "a"}
    assert client.get("/vaults/").json()["data"] == [created]


def test_declared_create_of_a_base_without_schema_is_inherited():
    class Rec(fr.IDBase):
        name: Mapped[str]
        is_admin: Mapped[bool] = mapped_column(default=False)

    class RecCreate(fr.BaseSchema):
        name: str

    class Base(fr.AsyncRestView):
        model = Rec
        schema_create = RecCreate

    class Sub(Base):
        prefix = "/recs"

    fr.include_view(FastAPI(), Sub)

    assert Sub.schema_create is RecCreate


def test_redeclaring_schema_rebuilds_the_list_params_a_parent_declared():
    class Rec(fr.IDBase):
        name: Mapped[str]
        note: Mapped[str]

    class RecSchema(fr.IDSchema):
        name: str

    class RecListParams(pydantic.BaseModel):
        name: str | None = None

    class Base(fr.AsyncRestView):
        model = Rec
        schema = RecSchema
        schema_list_params = RecListParams

    class WithNote(fr.IDSchema):
        name: str
        note: str

    class Sub(Base):
        prefix = "/recs"
        schema = WithNote

    fr.include_view(FastAPI(), Sub)

    assert Sub.schema_list_params is not RecListParams
    assert "note" in Sub.schema_list_params.model_fields


def test_list_params_declared_next_to_a_new_schema_stay():
    class Rec(fr.IDBase):
        name: Mapped[str]
        note: Mapped[str]

    class RecResponse(fr.IDSchema):
        name: str

    class RecSchema(fr.IDSchema):
        name: str
        note: str

    class RecListParams(pydantic.BaseModel):
        note: str | None = None

    class Base(fr.AsyncRestView):
        model = Rec
        schema_response = RecResponse

    class Middle(Base):
        schema = RecSchema
        schema_list_params = RecListParams

    class Sub(Middle):
        prefix = "/recs"

    fr.include_view(FastAPI(), Sub)

    assert Sub.schema_response is fr.schemas.derive_schema_response(RecSchema)
    assert Sub.schema_list_params is RecListParams
