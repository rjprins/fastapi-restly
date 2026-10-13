"""A field that the client leaves out of a create, with the value ``None``.

Restly does not pass such a field to the model, so the model's own default
applies. It still passes ``None`` when the constructor requires the
argument. And a ``None`` default on the constructor is no default for a
NOT NULL column, so the generated field stays required.
"""

import asyncio
import dataclasses
import uuid

import pytest
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import fastapi_restly as fr

from ._models import SharedBase
from .conftest import create_tables


def test_a_hand_written_init_still_gets_its_required_argument(client):
    """``dataclasses.fields()`` does not see a hand-written ``__init__``."""

    class PlainBase(DeclarativeBase):
        pass

    class Memo(PlainBase):
        __tablename__ = "memo"
        id: Mapped[int] = mapped_column(primary_key=True)
        title: Mapped[str]
        body: Mapped[str | None]

        def __init__(self, *, title: str, body: str | None) -> None:
            super().__init__(title=title, body=body or "")

    @fr.include_view(client.app)
    class MemoView(fr.AsyncRestView):
        prefix = "/memos"
        model = Memo

    async def create_memo_table():
        async with fr.db.get_async_engine().begin() as conn:
            await conn.run_sync(PlainBase.metadata.create_all)

    asyncio.run(create_memo_table())
    created = client.post("/memos/", json={"title": "a"}).json()
    assert created == {"id": created["id"], "title": "a", "body": ""}


def test_an_init_var_still_gets_its_required_argument(client):
    """``dataclasses.fields()`` leaves out an ``InitVar``."""

    class Account(fr.IDMixin, SharedBase):
        name: Mapped[str]
        password_hash: Mapped[str] = mapped_column(init=False, default="")
        password: dataclasses.InitVar[str | None]

        def __post_init__(self, password: str | None) -> None:
            self.password_hash = f"hashed:{password}" if password else ""

    class AccountSchema(fr.IDSchema):
        name: str

    class AccountCreate(fr.BaseSchema):
        name: str
        password: fr.WriteOnly[str | None] = None

    @fr.include_view(client.app)
    class AccountView(fr.AsyncRestView):
        prefix = "/accounts"
        model = Account
        schema = AccountSchema
        schema_create = AccountCreate

    create_tables()
    client.post("/accounts/", json={"name": "a"})
    client.post("/accounts/", json={"name": "b", "password": "pw"})


def test_a_none_default_on_a_not_null_column_keeps_the_field_required(client):
    """Before, the generated field became optional, and a create without it
    got a 409 from the database instead of a 422."""

    class Shelf(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Book(fr.IDMixin, SharedBase):
        title: Mapped[str]
        pages: Mapped[int] = mapped_column(default=None)
        shelf_id: Mapped[int] = mapped_column(ForeignKey("shelf.id"), default=None)
        shelf: Mapped[Shelf] = relationship(default=None)

    @fr.include_view(client.app)
    class BookView(fr.AsyncRestView):
        prefix = "/books"
        model = Book

    fields = BookView.schema_create.model_fields
    assert fields["pages"].is_required()
    assert fields["shelf_id"].is_required()

    create_tables()
    response = client.post("/books/", json={"title": "t"}, assert_status_code=422)
    missing = {tuple(error["loc"]) for error in response.json()["detail"]}
    assert missing == {("body", "pages"), ("body", "shelf_id")}


def test_a_default_outside_the_constructor_keeps_the_response_field_required(client):
    """A field that the constructor does not take cannot be left out by the
    client. Its default made the response field optional in OpenAPI."""

    class Gizmo(SharedBase):
        id: Mapped[uuid.UUID] = mapped_column(
            primary_key=True, init=False, default_factory=uuid.uuid4
        )
        name: Mapped[str]

    @fr.include_view(client.app)
    class GizmoView(fr.AsyncRestView):
        prefix = "/gizmos"
        model = Gizmo

    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "id" in schemas["GizmoResponse"]["required"]


@pytest.mark.parametrize("annotated", [False, True], ids=["int", "idref"])
def test_a_left_out_foreign_key_gets_its_default(client, annotated):
    """Before, a left-out ``IDRef`` field passed ``None`` and replaced the
    default of the constructor."""

    class Rack(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Bin(fr.IDMixin, SharedBase):
        name: Mapped[str]
        rack_id: Mapped[int] = mapped_column(
            ForeignKey("rack.id"), default_factory=lambda: 1
        )

    class BinSchema(fr.IDSchema):
        name: str
        if annotated:
            rack_id: fr.IDRef[Rack] | None = None
        else:
            rack_id: int | None = None

    @fr.include_view(client.app)
    class RackView(fr.AsyncRestView):
        prefix = "/racks"
        model = Rack

    @fr.include_view(client.app)
    class BinView(fr.AsyncRestView):
        prefix = "/bins"
        model = Bin
        schema = BinSchema

    create_tables()
    client.post("/racks/", json={"name": "r"})
    assert client.post("/bins/", json={"name": "b"}).json()["rack_id"] == 1
