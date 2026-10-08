"""Under PEP 563 every model annotation is a string.

The generator takes its fields from the mapper and evaluates the strings
against the model's module, so a stringized model gets the same schema as a
plain one. Kept in its own ``from __future__ import annotations`` module.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
import fastapi_restly.schemas as fr_schemas

from .conftest import create_tables


def test_view_schema_keeps_every_column(client):
    class Item(fr.IDBase):
        name: Mapped[str]
        price: Mapped[float]
        note: Mapped[str | None]

    assert isinstance(Item.__annotations__["name"], str)  # PEP 563 is in effect

    @fr.include_view(client.app)
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item

    fields = ItemView.schema.model_fields
    assert set(fields) == {"id", "name", "price", "note"}
    assert fields["name"].annotation is str
    assert fields["price"].annotation is float
    assert fields["note"].is_required() is False
    assert set(ItemView.schema_create.model_fields) == {"name", "price", "note"}

    create_tables()
    created = client.post("/items/", json={"name": "Pen", "price": 1.5}).json()
    assert created == {"id": created["id"], "name": "Pen", "price": 1.5, "note": None}


def test_relationships_are_left_out():
    class Author(fr.IDBase):
        name: Mapped[str]
        books: Mapped[list[Book]] = relationship(
            back_populates="author", default_factory=list
        )

    class Book(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
        author: Mapped[Author] = relationship(back_populates="books", default=None)

    author_schema = fr_schemas.derive_schema(Author)
    book_schema = fr_schemas.derive_schema(Book)

    assert set(author_schema.model_fields) == {"id", "name"}
    assert set(book_schema.model_fields) == {"id", "title", "author_id"}
