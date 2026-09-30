"""A view subclass inherits the schemas its base view declares.

Registration generates a schema only where none is declared. A schema Restly
generated for a parent is rebuilt for the subclass, and a schema derived from
``schema`` is rebuilt when the subclass declares a new ``schema``.
"""

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

    class RecRead(fr.IDSchema):
        name: str

    class Base(view_base):
        model = Rec
        schema = RecRead

    class Sub(Base):
        prefix = "/recs"

    fr.include_view(FastAPI(), Sub)

    assert Sub.schema is RecRead
    assert set(Sub.schema_create.model_fields) == {"name"}
    assert set(Sub.schema_update.model_fields) == {"name"}
    assert "schema" not in Sub.__dict__


def test_undeclared_column_does_not_leak_through_a_subclass(client):
    class Account(fr.IDBase):
        name: Mapped[str]
        secret: Mapped[str] = mapped_column(default="hidden")

    class AccountRead(fr.IDSchema):
        name: str

    class Base(fr.AsyncRestView):
        model = Account
        schema = AccountRead

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

    class RecRead(fr.IDSchema):
        name: str
        note: str

    class RecCreate(fr.BaseSchema):
        name: str

    class RecUpdate(fr.BaseSchema):
        note: str | None = None

    class Base(fr.AsyncRestView):
        model = Rec
        schema = RecRead
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

    class RecRead(fr.IDSchema):
        name: str

    class RecCreate(fr.BaseSchema):
        name: str

    class Base(fr.AsyncRestView):
        model = Rec
        schema = RecRead
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


def test_subclass_page_size_rebuilds_the_generated_listing_params():
    class Rec(fr.IDBase):
        name: Mapped[str]

    class RecRead(fr.IDSchema):
        name: str

    class Base(fr.AsyncRestView):
        prefix = "/recs"
        model = Rec
        schema = RecRead

    app = FastAPI()
    fr.include_view(app, Base)

    class Small(Base):
        prefix = "/small-recs"
        default_page_size = 5

    fr.include_view(app, Small)

    assert Base.listing_param_schema is not Small.listing_param_schema
    assert Small.listing_param_schema.model_fields["page_size"].default == 5
