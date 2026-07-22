"""Regression: a list view must not advertise filter params for
fields that are not filterable columns.

A to-many relationship (``books: list[BookRef]``) and a reference field that does
not resolve to a column were treated as scalar leaves and got the standard
filter params (``books``, ``books__in``, ``books__ne``, ``books__isnull``) -- all
of which 400 at request time because ``_resolve_column`` rejects non-columns. The
generator now validates each field through that same predicate, so it advertises
exactly the filters that work; to-one dotted traversal still works.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.query import create_list_params_schema


class DeadParamPublisher(fr.IDBase):
    name: Mapped[str] = mapped_column()


class DeadParamBook(fr.IDBase):
    title: Mapped[str] = mapped_column()
    author_id: Mapped[int] = mapped_column(ForeignKey("dead_param_author.id"))


class DeadParamAuthor(fr.IDBase):
    name: Mapped[str] = mapped_column()
    publisher_id: Mapped[int | None] = mapped_column(
        ForeignKey("dead_param_publisher.id")
    )
    publisher: Mapped[DeadParamPublisher | None] = relationship()
    books: Mapped[list[DeadParamBook]] = relationship()


def test_dead_relationship_params_are_not_generated():
    class PublisherSchema(fr.BaseSchema):
        name: str

    class AuthorSchema(fr.BaseSchema):
        name: str
        publisher: PublisherSchema | None = None  # to-one nested -> dotted traversal
        books: list[fr.IDRef[DeadParamBook]]  # to-many relationship -> dead

    fields = create_list_params_schema(AuthorSchema, DeadParamAuthor).model_fields

    # A real scalar column is filterable.
    assert "name" in fields
    assert "name__in" in fields

    # A to-one relationship typed as a nested schema still gets dotted traversal.
    assert "publisher.name" in fields

    # A to-many relationship gets NO filter params (they would always 400).
    for key in ("books", "books__in", "books__ne", "books__isnull"):
        assert key not in fields


def test_view_without_model_raises_clear_error():
    # List-param generation needs the model; a view with an explicit schema but
    # no model must fail registration with a clear message, not a raw
    # AttributeError on ``cls.model``.
    class ModellessSchema(fr.BaseSchema):
        name: str

    app = FastAPI()
    with pytest.raises(ValueError, match="must be specified"):

        @fr.include_view(app)
        class ModellessView(fr.AsyncRestView):
            schema = ModellessSchema
