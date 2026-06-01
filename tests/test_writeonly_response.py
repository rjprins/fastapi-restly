"""A WriteOnly field must never leak into a response (ticket 477).

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
    """The schema-instance path uses ``model_construct``, so an after-validator
    runs exactly once: a non-idempotent one isn't double-applied (and one that
    reads a WriteOnly field can't crash on the omitting response schema)."""
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
