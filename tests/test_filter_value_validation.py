"""Filter values are validated against one field, not the whole schema.

A query-string filter is one column. Validating it through the schema's
``validate_assignment`` on a ``model_construct()`` skeleton ran the model
validators too, so a cross-field rule (severity required when the type is a
bug) saw an empty skeleton and turned every legal filter into a 400. The value
now validates through the field's own core schema: field validators,
constraints and the model config still apply, model validators do not.
"""

from typing import Annotated, Any

import pydantic
import pytest
from pydantic import ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly.exc import BadQueryParam
from fastapi_restly.query._impl import _parse_value

from .conftest import create_tables


class TicketSchema(pydantic.BaseModel):
    """A cross-field rule that every filter used to trip over."""

    title: str
    kind: str = "bug"
    severity: int | None = None

    @model_validator(mode="after")
    def _bugs_need_severity(self):
        if self.kind == "bug" and self.severity is None:
            raise ValueError("a bug needs a severity")
        return self


def test_after_model_validator_does_not_reject_a_filter():
    assert _parse_value(TicketSchema, "title", "x") == "x"
    assert _parse_value(TicketSchema, "severity", "3") == 3


def test_before_model_validator_is_not_run():
    calls: list[object] = []

    class Schema(pydantic.BaseModel):
        name: str

        @model_validator(mode="before")
        @classmethod
        def _record(cls, data):
            calls.append(data)
            return data

    assert _parse_value(Schema, "name", "x") == "x"
    assert calls == []


def test_field_validator_still_applies():
    class Schema(pydantic.BaseModel):
        name: str

        @field_validator("name")
        @classmethod
        def _lower(cls, value: str) -> str:
            return value.strip().lower()

    assert _parse_value(Schema, "name", "  Foo ") == "foo"


def test_field_constraint_still_rejects():
    class Schema(pydantic.BaseModel):
        score: Annotated[int, Field(gt=0)]

    assert _parse_value(Schema, "score", "5") == 5
    with pytest.raises(BadQueryParam):
        _parse_value(Schema, "score", "0")


def test_bad_type_still_rejects():
    assert _parse_value(TicketSchema, "severity", "3") == 3
    with pytest.raises(BadQueryParam):
        _parse_value(TicketSchema, "severity", "three")


def test_model_config_still_applies():
    class Schema(pydantic.BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, str_to_lower=True)
        name: str

    assert _parse_value(Schema, "name", "  Foo ") == "foo"


def test_frozen_schema_filters():
    """Assignment on a frozen model raises; a field validator does not care."""

    class Schema(pydantic.BaseModel):
        model_config = ConfigDict(frozen=True)
        name: str

    assert _parse_value(Schema, "name", "x") == "x"


def test_definition_ref_field_validates():
    """A class the schema uses twice becomes a definition-ref in the core
    schema; the field validator carries the definitions along."""

    class Post(fr.IDBase):
        title: Mapped[str]

    class Schema(fr.IDSchema):
        post: fr.IDRef[Post]
        other: fr.IDRef[Post]

    core = Schema.__pydantic_core_schema__
    assert core["type"] == "definitions"
    node: Any = core
    while node["type"] != "model-fields":
        node = node["schema"]
    assert node["fields"]["post"]["schema"]["type"] == "definition-ref"
    assert _parse_value(Schema, "post", "5") == 5
    with pytest.raises(BadQueryParam):
        _parse_value(Schema, "post", "x")


def test_listing_filter_with_cross_field_validator_is_200(client):
    class Ticket(fr.IDBase):
        title: Mapped[str]
        kind: Mapped[str]
        severity: Mapped[int | None]

    class Schema(fr.IDSchema):
        title: str
        kind: str = "bug"
        severity: int | None = None

        @model_validator(mode="after")
        def _bugs_need_severity(self):
            if self.kind == "bug" and self.severity is None:
                raise ValueError("a bug needs a severity")
            return self

    @fr.include_view(client.app)
    class TicketView(fr.AsyncRestView):
        prefix = "/tickets"
        model = Ticket
        schema = Schema

    create_tables()
    client.post("/tickets", json={"title": "crash", "severity": 2})
    client.post("/tickets", json={"title": "idea", "kind": "feature"})
    client.post(
        "/tickets", json={"title": "vague", "kind": "bug"}, assert_status_code=422
    )

    rows = client.get("/tickets", params={"title": "crash"}).json()
    assert [r["title"] for r in rows["data"]] == ["crash"]
    rows = client.get("/tickets", params={"kind": "feature"}).json()
    assert [r["title"] for r in rows["data"]] == ["idea"]
    rows = client.get("/tickets", params={"severity__gte": "1"}).json()
    assert [r["title"] for r in rows["data"]] == ["crash"]
