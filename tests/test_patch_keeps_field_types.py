"""A field of the update body keeps its type.

The client may leave out each field of ``PATCH``, but ``null`` is valid only
where the type allows ``None``, as for ``POST``. Before, the update body made
each type ``Optional``. Then ``{"title": null}`` passed validation, and the
database rejected the ``NULL``: 409 instead of 422.
"""

import warnings
from typing import get_args

import pydantic
import pytest
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr

from ._models import SharedBase
from .conftest import create_tables


@pytest.mark.parametrize("declared", [True, False], ids=["schema", "generated"])
def test_null_for_a_field_that_does_not_allow_it_is_a_422(client, declared):
    class Memo(fr.IDMixin, SharedBase):
        title: Mapped[str]
        note: Mapped[str | None] = None

    class MemoSchema(fr.IDSchema):
        title: str
        note: str | None = None

    @fr.include_view(client.app)
    class MemoView(fr.AsyncRestView):
        prefix = "/memos"
        model = Memo
        if declared:
            schema = MemoSchema

    create_tables()
    memo = client.post("/memos/", json={"title": "a", "note": "n"}).json()
    url = f"/memos/{memo['id']}"

    response = client.patch(url, json={"title": None}, assert_status_code=422)
    assert response.json()["detail"][0]["loc"] == ["body", "title"]
    # The same answer as POST.
    client.post("/memos/", json={"title": None}, assert_status_code=422)

    # A field that the client leaves out keeps its value.
    assert client.patch(url, json={}).json() == memo
    # A field whose type allows None can be cleared.
    cleared = client.patch(url, json={"note": None}).json()
    assert cleared == {**memo, "note": None}


def test_a_generated_field_with_a_default_allows_null_only_when_nullable(client):
    """In a view without a schema, a column with a default may be left out,
    but it allows ``null`` only when it is nullable. Before, its field was
    ``int | None``: POST stored the default for a ``null``, and PATCH got 409
    from the database."""

    class Counter(fr.IDMixin, SharedBase):
        count: Mapped[int] = mapped_column(default=3)
        label: Mapped[str] = mapped_column(server_default="x")
        note: Mapped[str | None] = mapped_column(default="n")

    @fr.include_view(client.app)
    class CounterView(fr.AsyncRestView):
        prefix = "/counters"
        model = Counter

    fields = CounterView.schema_create.model_fields
    assert fields["count"].annotation is int
    assert fields["label"].annotation is str
    assert type(None) in get_args(fields["note"].annotation)
    assert not any(field.is_required() for field in fields.values())

    create_tables()
    created = client.post("/counters/", json={}).json()
    assert (created["count"], created["label"], created["note"]) == (3, "x", "n")
    url = f"/counters/{created['id']}"
    for name in ("count", "label"):
        client.post("/counters/", json={name: None}, assert_status_code=422)
        client.patch(url, json={name: None}, assert_status_code=422)
    assert client.patch(url, json={"note": None}).json()["note"] is None


def test_the_update_body_has_no_defaults_and_no_required_fields(client):
    class Card(fr.IDMixin, SharedBase):
        title: Mapped[str]
        count: Mapped[int] = mapped_column(default=3)
        note: Mapped[str | None] = None
        secret: Mapped[str] = mapped_column(default="")

    class CardSchema(fr.IDSchema):
        title: str
        count: int = 3
        note: str | None = pydantic.Field(None, json_schema_extra={"x-note": True})
        secret: fr.WriteOnly[str] = ""

    @fr.include_view(client.app)
    class CardView(fr.AsyncRestView):
        prefix = "/cards"
        model = Card
        schema = CardSchema

    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    update = schemas["CardUpdate"]
    assert "required" not in update
    fields = update["properties"]
    assert fields["title"] == {"type": "string", "title": "Title"}
    assert fields["count"] == {"type": "integer", "title": "Count"}
    # The field's own extras stay.
    assert fields["note"] == {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "title": "Note",
        "x-note": True,
    }
    assert fields["secret"] == {"type": "string", "title": "Secret", "writeOnly": True}
    # The create body keeps the defaults of the schema.
    assert schemas["CardCreate"]["properties"]["count"]["default"] == 3


def test_a_v1_validator_with_always_does_not_validate_a_left_out_field(client):
    """Pydantic validates the default of a field with a v1
    ``@validator(..., always=True)``. In the update body, the ``None``
    default of ``title: str`` then failed, and each PATCH that left the title
    out returned 422."""

    class Note(fr.IDMixin, SharedBase):
        title: Mapped[str]
        body: Mapped[str]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pydantic.PydanticDeprecatedSince20)

        class NoteSchema(fr.IDSchema):
            title: str
            body: str

            @pydantic.validator("title", always=True)
            def strip_title(cls, value: str) -> str:
                return value.strip()

    @fr.include_view(client.app)
    class NoteView(fr.AsyncRestView):
        prefix = "/notes"
        model = Note
        schema = NoteSchema

    create_tables()
    note = client.post("/notes/", json={"title": " a ", "body": "b"}).json()
    assert note["title"] == "a"
    url = f"/notes/{note['id']}"
    assert client.patch(url, json={"body": "c"}).json()["title"] == "a"
    assert client.patch(url, json={"title": " d "}).json()["title"] == "d"
    client.patch(url, json={"title": None}, assert_status_code=422)
    # The create body still validates the default.
    assert NoteView.schema_create.model_fields["title"].validate_default
