"""A field of the update body keeps its type.

The client may leave out each field of ``PATCH``, but ``null`` is valid only
where the type allows ``None``, as for ``POST``. Before, the update body made
each type ``Optional``. Then ``{"title": null}`` passed validation, and the
database rejected the ``NULL``: 409 instead of 422.
"""

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
