"""A derived write schema leaves out a primary key the server generates."""

from uuid import UUID, uuid4

import pydantic
import pytest
from fastapi import FastAPI
from sqlalchemy import Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import fastapi_restly as fr

from .conftest import create_tables


def _register(base, model_cls, schema_cls, **attrs):
    view = type(
        f"{model_cls.__name__}View",
        (base,),
        {"prefix": "/things", "model": model_cls, "schema": schema_cls, **attrs},
    )
    fr.include_view(FastAPI(), view)
    return view


@pytest.fixture(params=[fr.AsyncRestView, fr.RestView], ids=["async", "sync"])
def base(request):
    return request.param


def test_autoincrement_id_is_left_out_of_derived_write_schemas(base):
    class Gadget(fr.IDBase):
        name: Mapped[str]

    class GadgetRead(pydantic.BaseModel):
        id: int
        name: str

    view = _register(base, Gadget, GadgetRead)

    assert set(view.schema.model_fields) == {"id", "name"}
    assert set(view.schema_create.model_fields) == {"name"}
    assert set(view.schema_update.model_fields) == {"name"}


def test_primary_key_with_a_column_default_is_left_out(base):
    class Token(fr.DataclassBase):
        id: Mapped[UUID] = mapped_column(
            Uuid, primary_key=True, insert_default=uuid4, init=False
        )
        name: Mapped[str]

    class TokenRead(pydantic.BaseModel):
        id: UUID
        name: str

    view = _register(base, Token, TokenRead)

    assert set(view.schema_create.model_fields) == {"name"}
    assert set(view.schema_update.model_fields) == {"name"}


def test_dataclass_primary_key_outside_init_is_left_out(base):
    class Ticket(fr.DataclassBase):
        id: Mapped[UUID] = mapped_column(
            Uuid, primary_key=True, default_factory=uuid4, init=False
        )
        name: Mapped[str]

    class TicketRead(pydantic.BaseModel):
        id: UUID
        name: str

    view = _register(base, Ticket, TicketRead)

    assert set(view.schema_create.model_fields) == {"name"}


def test_generated_primary_key_with_another_name_is_left_out(base):
    class Base(DeclarativeBase):
        pass

    class Widget(Base):
        __tablename__ = "pk_named_widget"
        widget_id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str]

    class WidgetRead(pydantic.BaseModel):
        widget_id: int
        name: str

    view = _register(base, Widget, WidgetRead)

    assert set(view.schema_create.model_fields) == {"name"}
    assert set(view.schema_update.model_fields) == {"name"}


def test_natural_key_stays_in_the_derived_create_schema(base):
    class Country(fr.DataclassBase):
        code: Mapped[str] = mapped_column(primary_key=True)
        name: Mapped[str]

    class CountryRead(pydantic.BaseModel):
        code: str
        name: str

    view = _register(base, Country, CountryRead)

    assert set(view.schema_create.model_fields) == {"code", "name"}
    assert view.schema_create.model_fields["code"].is_required()


def test_composite_key_stays_in_the_derived_create_schema(base):
    class Membership(fr.DataclassBase):
        user_id: Mapped[int] = mapped_column(primary_key=True)
        group_id: Mapped[int] = mapped_column(primary_key=True)
        role: Mapped[str]

    class MembershipRead(pydantic.BaseModel):
        user_id: int
        group_id: int
        role: str

    view = _register(base, Membership, MembershipRead)

    assert set(view.schema_create.model_fields) == {"user_id", "group_id", "role"}


def test_explicit_write_schemas_are_left_alone(base):
    class Device(fr.IDBase):
        name: Mapped[str]

    class DeviceRead(pydantic.BaseModel):
        id: int
        name: str

    class DeviceWrite(pydantic.BaseModel):
        id: int | None = None
        name: str

    view = _register(
        base, Device, DeviceRead, schema_create=DeviceWrite, schema_update=DeviceWrite
    )

    assert view.schema_create is DeviceWrite
    assert view.schema_update is DeviceWrite


def test_client_cannot_choose_or_change_a_generated_id(client):
    class Sprocket(fr.IDBase):
        name: Mapped[str]

    class SprocketRead(pydantic.BaseModel):
        id: int
        name: str

    @fr.include_view(client.app)
    class SprocketView(fr.AsyncRestView):
        prefix = "/sprockets"
        model = Sprocket
        schema = SprocketRead

    create_tables()

    created = client.post("/sprockets/", json={"name": "a"}).json()
    assert created == {"id": created["id"], "name": "a"}

    chosen = client.post("/sprockets/", json={"id": 77, "name": "b"}).json()
    assert chosen["id"] != 77

    patched = client.patch(f"/sprockets/{created['id']}", json={"id": 55, "name": "c"})
    assert patched.json() == {"id": created["id"], "name": "c"}

    create_schema = client.app.openapi()["components"]["schemas"][
        SprocketView.schema_create.__name__
    ]
    assert set(create_schema["properties"]) == {"name"}
