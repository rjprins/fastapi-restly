"""Regression: a list view must not advertise filter params for
fields that are not filterable columns.

A to-many relationship (``books: list[BookRef]``) and a reference field that does
not resolve to a column were treated as scalar leaves and got the standard
filter params (``books``, ``books__in``, ``books__ne``, ``books__isnull``) -- all
of which 400 at request time because ``_resolve_column`` rejects non-columns. The
generator now validates each field through that same predicate, so it advertises
exactly the filters that work; to-one dotted traversal still works.

The same principle covers collection-typed columns (``JSON``/``ARRAY``): they
ARE columns, but no query string coerces into ``dict``/``list``, so only
``__isnull`` is advertised for them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Optional

import pydantic
import pytest
from fastapi import FastAPI
from sqlalchemy import JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.query import create_list_params_schema

from .conftest import create_tables


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


class JsonColumnRow(fr.IDBase):
    name: Mapped[str] = mapped_column()
    meta: Mapped[dict | None] = mapped_column(JSON, default=None)
    tags: Mapped[list | None] = mapped_column(JSON, default=None)
    coords: Mapped[list | None] = mapped_column(JSON, default=None)
    labels: Mapped[list | None] = mapped_column(JSON, default=None)
    frozen: Mapped[list | None] = mapped_column(JSON, default=None)
    seq: Mapped[list | None] = mapped_column(JSON, default=None)
    mapping: Mapped[dict | None] = mapped_column(JSON, default=None)
    clist: Mapped[list | None] = mapped_column(JSON, default=None)
    wrapped: Mapped[list | None] = mapped_column(JSON, default=None)
    jmeta: Mapped[dict | None] = mapped_column(JSON, default=None)


def test_collection_typed_fields_generate_only_isnull():
    """A JSON/ARRAY-backed field resolves to a real column, so the column
    predicate keeps it -- but no query string can coerce into a collection
    type, so every operator except ``__isnull`` used to 400 at request time.
    Only ``__isnull`` is advertised now, across every collection spelling:
    bare/parametrized builtins, ``frozenset``, abc ``Sequence``/``Mapping``,
    ``conlist`` (Annotated inside Optional), and Annotated-wrapped lists."""

    class RowSchema(fr.BaseSchema):
        name: str
        meta: dict | None = None
        tags: list[str] | None = None
        coords: tuple[float, float] | None = None
        labels: set[str] | None = None
        frozen: frozenset[str] | None = None
        seq: Sequence[str] | None = None
        mapping: Mapping[str, str] | None = None
        # The shape ``conlist(str, min_length=1)`` desugars to: Annotated with
        # constraint metadata, inside Optional.
        clist: Optional[Annotated[list[str], pydantic.Field(min_length=1)]] = None
        wrapped: Optional[Annotated[list[str], pydantic.Field(description="x")]] = None

    fields = create_list_params_schema(RowSchema, JsonColumnRow).model_fields

    for base in (
        "meta",
        "tags",
        "coords",
        "labels",
        "frozen",
        "seq",
        "mapping",
        "clist",
        "wrapped",
    ):
        assert f"{base}__isnull" in fields, base
        for dead in (
            base,
            f"{base}__in",
            f"{base}__ne",
            f"{base}__gte",
            f"{base}__lte",
            f"{base}__gt",
            f"{base}__lt",
            f"{base}__contains",
            f"{base}__icontains",
        ):
            assert dead not in fields, dead

    # The scalar sibling keeps its full operator set -- no over-removal.
    for alive in ("name", "name__in", "name__ne", "name__isnull", "name__contains"):
        assert alive in fields, alive


def test_json_typed_field_keeps_scalar_filters():
    """``pydantic.Json[dict]`` LOOKS like a collection field (its annotation is
    ``dict``) but its validation parses the query string as JSON, so its
    filters genuinely work -- they must not be removed."""

    class RowSchema(fr.BaseSchema):
        name: str
        jmeta: pydantic.Json[dict] | None = None

    fields = create_list_params_schema(RowSchema, JsonColumnRow).model_fields

    for alive in ("jmeta", "jmeta__in", "jmeta__ne", "jmeta__isnull"):
        assert alive in fields, alive


def test_collection_typed_fields_isnull_works_end_to_end(client):
    """``__isnull`` still executes for JSON columns, and the formerly-dead
    operators are now rejected as unknown parameters (422), not 400."""

    # Defined locally so the autouse ``reset_metadata`` fixture tears the
    # table down with the test.
    class JsonRow(fr.IDBase):
        name: Mapped[str] = mapped_column()
        # none_as_null: a Python None means SQL NULL (not JSON 'null'), so
        # ``__isnull`` has real NULLs to match.
        meta: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), default=None)

    class RowSchema(fr.IDSchema):
        name: str
        meta: dict | None = None

    @fr.include_view(client.app)
    class RowView(fr.AsyncRestView):
        prefix = "/json-rows"
        model = JsonRow
        schema = RowSchema

    create_tables()

    client.post(
        "/json-rows/",
        json={"name": "with-meta", "meta": {"k": "v"}},
        assert_status_code=201,
    )
    client.post("/json-rows/", json={"name": "bare"}, assert_status_code=201)

    with_meta = client.get("/json-rows/?meta__isnull=false").json()
    assert [row["name"] for row in with_meta] == ["with-meta"]
    without_meta = client.get("/json-rows/?meta__isnull=true").json()
    assert [row["name"] for row in without_meta] == ["bare"]

    # The dead operators are no longer advertised, so they fail validation as
    # unknown params -- instead of passing validation and 400ing at execution.
    client.get("/json-rows/?meta=x", assert_status_code=422)
    client.get("/json-rows/?meta__in=x", assert_status_code=422)
