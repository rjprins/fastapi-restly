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


@pytest.mark.parametrize("mode", ["before", "after", "plain", "wrap"])
@pytest.mark.parametrize("recursive", [False, True])
def test_filter_validator_receives_field_name_and_empty_data(mode, recursive):
    seen = []

    def prefix(value, info):
        seen.append((info.field_name, None if info.data is None else info.data.copy()))
        return f"{info.field_name}:{value}"

    def wrap(value, handler, info):
        return handler(prefix(value, info))

    class Schema(pydantic.BaseModel):
        model_config = ConfigDict(validate_by_alias=True, validate_by_name=False)
        first_name: str = Field(alias="firstName")
        last_name: str
        other_required_field: int

        _prefix = field_validator("first_name", "last_name", mode=mode)(
            wrap if mode == "wrap" else prefix
        )

    if recursive:

        class RecursiveSchema(Schema):
            children: list["RecursiveSchema"] = Field(default_factory=list)

        schema_cls = RecursiveSchema
    else:
        schema_cls = Schema

    assert _parse_value(schema_cls, "firstName", "Alice") == "first_name:Alice"
    assert _parse_value(schema_cls, "last_name", "Smith") == "last_name:Smith"
    assert seen == [("first_name", {}), ("last_name", {})]


def test_listing_filter_with_field_name_dependent_validator(client):
    class Item(fr.IDBase):
        name: Mapped[str]
        code: Mapped[str]

    class ItemSchema(fr.IDSchema):
        name: str = Field(alias="displayName")
        code: str

        @field_validator("name", "code")
        @classmethod
        def _check_length(cls, value, info):
            limits = {"name": 20, "code": 4}
            if len(value) > limits[info.field_name]:
                raise ValueError("value is too long")
            return value

    @fr.include_view(client.app)
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

    create_tables()
    client.post("/items", json={"displayName": "Alice", "code": "A1"})
    client.post("/items", json={"displayName": "Bob", "code": "B1"})

    rows = client.get("/items", params={"displayName": "Alice", "code": "A1"}).json()
    assert [row["displayName"] for row in rows["data"]] == ["Alice"]

    response = client.get("/items", params={"code": "ABCDE"}, assert_status_code=400)
    assert response.json() == {"detail": "Invalid attribute in URL query: code"}


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


def test_recursive_schema_scalar_filters():
    class Node(pydantic.BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True)
        name: str = Field(alias="nodeName", min_length=1)
        children: list["Node"] = Field(default_factory=list)

        @field_validator("name")
        @classmethod
        def _lower(cls, value: str) -> str:
            return value.lower()

    assert _parse_value(Node, "nodeName", "  Alice ") == "alice"
    with pytest.raises(BadQueryParam):
        _parse_value(Node, "nodeName", " ")


def test_recursive_schema_model_validators_are_not_run():
    class Node(pydantic.BaseModel):
        name: str
        children: list["Node"] = Field(default_factory=list)

        @model_validator(mode="before")
        @classmethod
        def _before(cls, data):
            raise AssertionError("model before-validator ran for a filter")

        @model_validator(mode="wrap")
        @classmethod
        def _wrap(cls, data, handler):
            raise AssertionError("model wrap-validator ran for a filter")

        @model_validator(mode="after")
        def _after(self):
            raise AssertionError("model after-validator ran for a filter")

    assert _parse_value(Node, "name", "Alice") == "Alice"


def test_mutually_recursive_schema_scalar_filters():
    class Parent(pydantic.BaseModel):
        name: str
        children: list["Child"] = Field(default_factory=list)

    class Child(pydantic.BaseModel):
        age: int
        parent: Parent

    Parent.model_rebuild()

    assert _parse_value(Parent, "name", "Alice") == "Alice"
    assert _parse_value(Child, "age", "5") == 5
    assert _parse_value(Child, "parent.name", "Alice") == "Alice"


def test_listing_scalar_filter_with_recursive_schema(client):
    class Node(fr.IDBase):
        name: Mapped[str]

    class NodeSchema(fr.IDSchema):
        name: str
        children: fr.ReadOnly[list["NodeSchema"]] = Field(default_factory=list)

    @fr.include_view(client.app)
    class NodeView(fr.AsyncRestView):
        prefix = "/nodes"
        model = Node
        schema = NodeSchema

    create_tables()
    client.post("/nodes", json={"name": "Alice"})
    client.post("/nodes", json={"name": "Bob"})

    rows = client.get("/nodes", params={"name": "Alice"}).json()
    assert [row["name"] for row in rows["data"]] == ["Alice"]


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
