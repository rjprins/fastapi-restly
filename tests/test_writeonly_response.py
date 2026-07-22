"""A WriteOnly field must never leak into a response.

Two layers, tested independently:

* (a) ``to_response_schema`` strips WriteOnly fields even when handed a *schema
  instance* (a custom verb, or a ``get_*`` override that returns one) -- it used
  to short-circuit and return the full-schema instance unfiltered. The custom
  route below has no ``response_model``, so it isolates this layer.
* (c) the generated ``response_model`` is the WriteOnly-omitting response
  schema, so FastAPI itself cannot emit a WriteOnly field even if a path
  bypasses ``to_response_schema`` -- and the OpenAPI response shape is correct.
  WriteOnly stays in the *request* schema (it is write-only, not no-write).
"""

from sqlalchemy.orm import Mapped

import fastapi_restly as fr

from .conftest import create_tables


def _build(client):
    class User(fr.IDBase):
        name: Mapped[str]
        password: Mapped[str]

    class UserSchema(fr.IDSchema):
        name: str
        password: fr.WriteOnly[str]

    @fr.include_view(client.app)
    class UserView(fr.AsyncRestView):
        prefix = "/users"
        model = User
        schema = UserSchema

        @fr.get("/echo/{id}")
        async def echo(self, id: int):
            # Return a FULL schema instance with the WriteOnly field populated --
            # the path that used to leak via the isinstance short-circuit. This
            # custom route has no response_model, so only layer (a) protects it.
            return self.to_response(
                UserSchema(id=id, name="bob", password="secret")
            )

    create_tables()
    return UserSchema


def test_writeonly_not_leaked_when_returning_a_schema_instance(client):
    _build(client)

    body = client.get("/users/echo/1").json()

    assert body["name"] == "bob"
    assert "password" not in body  # layer (a): to_response_schema stripped it


def test_writeonly_omitted_from_response_model_but_present_in_request(client):
    _build(client)

    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]

    # (c) the get-one response model omits the WriteOnly field.
    get_one = spec["paths"]["/users/{id}"]["get"]
    resp_ref = get_one["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].rsplit("/", 1)[-1]
    assert "password" not in schemas[resp_ref]["properties"]

    # ...but the create request body still accepts it (write-only is writable).
    post_op = next(item["post"] for item in spec["paths"].values() if "post" in item)
    req_ref = post_op["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ].rsplit("/", 1)[-1]
    assert "password" in schemas[req_ref]["properties"]


def test_standard_create_and_get_do_not_echo_writeonly(client):
    _build(client)

    created = client.post("/users/", json={"name": "bob", "password": "secret"}).json()
    assert "password" not in created

    fetched = client.get(f"/users/{created['id']}").json()
    assert "password" not in fetched


def test_schema_instance_path_does_not_rerun_validators(client):
    """A returned schema instance is serialized as-is (WriteOnly is stripped by
    the marker's ``exclude``), so an after-validator runs once at construction
    and is not re-applied on the way out."""
    from pydantic import model_validator

    class Acct(fr.IDBase):
        name: Mapped[str]
        token: Mapped[str]

    class AcctSchema(fr.IDSchema):
        name: str
        token: fr.WriteOnly[str]

        @model_validator(mode="after")
        def _bang(self):
            object.__setattr__(self, "name", self.name + "!")
            return self

    @fr.include_view(client.app)
    class AcctView(fr.AsyncRestView):
        prefix = "/accts"
        model = Acct
        schema = AcctSchema

        @fr.get("/echo")
        async def echo(self):
            return self.to_response(AcctSchema(id=1, name="x", token="secret"))

    create_tables()

    body = client.get("/accts/echo").json()
    assert body["name"] == "x!"  # validator applied once at construction, not "x!!"
    assert "token" not in body


def test_writeonly_stripped_in_nested_schema():
    """`exclude` on the marker recurses: a WriteOnly field on a NESTED response
    schema is stripped from serialization too. The top-level loop
    in to_response_schema never reached this -- the field-level exclude does."""

    class OrgNested(fr.IDSchema):
        name: str
        api_key: fr.WriteOnly[str]

    class MemberSchema(fr.IDSchema):
        name: str
        org: OrgNested

    member = MemberSchema(
        id=1, name="bob", org=OrgNested(id=1, name="acme", api_key="TOPSECRET")
    )

    dumped = member.model_dump()
    assert dumped["org"]["name"] == "acme"
    assert "api_key" not in dumped["org"]  # nested WriteOnly stripped
    assert "TOPSECRET" not in member.model_dump_json()


def test_writeonly_marker_excludes_from_dump_but_stays_writable():
    """Pin that `exclude` takes effect on the parameterized `WriteOnly[T]` alias
    form (guards against the cosmetic 'exclude has no effect' warning ever
    becoming a real dropped-exclude), while the field stays a writable input."""

    class S(fr.IDSchema):
        name: str
        secret: fr.WriteOnly[str]

    s = S(id=1, name="x", secret="z")
    assert "secret" not in s.model_dump()
    assert "secret" not in s.model_dump(by_alias=True)
    # exclude is serialization-only: the field is still accepted on input.
    assert S.model_validate({"id": 1, "name": "x", "secret": "z"}).secret == "z"


def test_writeonly_optional_recommended_form_is_stripped():
    """`WriteOnly[Optional[T]]` is the recommended way to make a WriteOnly field
    optional, and it is stripped. (Prefer it over `Optional[WriteOnly[T]]`, where
    the marker is only a union member so the exclude does not apply.)"""
    from typing import Optional

    class S(fr.IDSchema):
        name: str
        secret: fr.WriteOnly[Optional[str]] = None

    assert "secret" not in S(id=1, name="x", secret="z").model_dump()
    assert "secret" not in S(id=1, name="x").model_dump()
