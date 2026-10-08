"""The names of the classes that Restly generates for a view.

A generated class is named ``<Resource><Role>``. Resource is the class name
of the view's schema without a final ``Schema`` or ``Response``. The client
sees these names in OpenAPI, and never the view's schema itself. When two
different classes get the same name, Restly warns.
"""

import re
import warnings
from typing import Any

import fastapi
import pydantic
import pytest
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly.exc import RestlyMisuseWarning

from .conftest import create_tables

Spec = dict[str, Any]


def _spec(app_or_spec: fastapi.FastAPI | Spec) -> Spec:
    if isinstance(app_or_spec, dict):
        return app_or_spec
    with warnings.catch_warnings():
        warnings.simplefilter("error", RestlyMisuseWarning)
        return app_or_spec.openapi()


def _components(app_or_spec: fastapi.FastAPI | Spec) -> set[str]:
    return set(_spec(app_or_spec)["components"]["schemas"]) - {
        "HTTPValidationError",
        "ValidationError",
    }


def _ref(
    app_or_spec: fastapi.FastAPI | Spec, path: str, method: str, status: str = "200"
) -> str:
    operation = _spec(app_or_spec)["paths"][path][method]
    schema = operation["responses"][status]["content"]["application/json"]["schema"]
    return schema["$ref"].removeprefix("#/components/schemas/")


def _body_ref(app_or_spec: fastapi.FastAPI | Spec, path: str, method: str) -> str:
    operation = _spec(app_or_spec)["paths"][path][method]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    return schema["$ref"].removeprefix("#/components/schemas/")


def test_openapi_shows_only_the_generated_names():
    class User(fr.IDBase):
        name: Mapped[str]
        password_hash: Mapped[str] = ""

    class UserSchema(fr.IDSchema):
        name: str
        password: fr.WriteOnly[str]

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class UserView(fr.AsyncRestView):
        prefix = "/users"
        model = User
        schema = UserSchema

    assert _components(app) == {
        "UserResponse",
        "UserCreate",
        "UserUpdate",
        "UserListResponse",
    }
    assert _ref(app, "/users", "get") == "UserListResponse"
    assert _ref(app, "/users/{id}", "get") == "UserResponse"
    assert _ref(app, "/users", "post", "201") == "UserResponse"
    assert _ref(app, "/users/{id}", "patch") == "UserResponse"
    assert _body_ref(app, "/users", "post") == "UserCreate"
    assert _body_ref(app, "/users/{id}", "patch") == "UserUpdate"
    assert UserView.schema_list_params.__name__ == "UserListParams"


def test_a_view_without_a_schema_gets_a_generated_schema():
    class Thing(fr.IDBase):
        name: Mapped[str]

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class ThingView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing
        pagination = None

    assert ThingView.schema.__name__ == "ThingSchema"
    assert ThingView.schema_list_params.__name__ == "ThingListParams"
    assert _components(app) == {
        "ThingResponse",
        "ThingCreate",
        "ThingUpdate",
        "ThingListResponse",
    }


def test_a_final_response_is_dropped_from_the_resource_name():
    class Item(fr.IDBase):
        name: Mapped[str]

    class ItemResponse(fr.IDSchema):
        name: str

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemResponse

    assert _components(app) == {
        "ItemResponse",
        "ItemCreate",
        "ItemUpdate",
        "ItemListResponse",
    }


def test_the_response_class_is_built_without_write_only_fields_too():
    """The response class is a subclass of the view's schema, so a response
    is also an instance of the schema."""

    class Note(fr.IDBase):
        text: Mapped[str]

    class NoteSchema(fr.IDSchema):
        text: str

    class NoteView(fr.AsyncRestView):
        prefix = "/notes"
        model = Note
        schema = NoteSchema

    note = Note(text="hello")
    note.id = 1
    response = NoteView().to_single_response(note)

    assert type(response).__name__ == "NoteResponse"
    assert isinstance(response, NoteSchema)

    app = fastapi.FastAPI()
    fr.include_view(app, NoteView)
    assert _ref(app, "/notes/{id}", "get") == "NoteResponse"
    assert "NoteSchema" not in _components(app)


def test_react_admin_put_returns_the_response_class():
    class Tag(fr.IDBase):
        label: Mapped[str]

    class TagSchema(fr.IDSchema):
        label: str

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class TagView(fr.AsyncReactAdminView):
        prefix = "/tags"
        model = Tag
        schema = TagSchema

    assert _ref(app, "/tags/{id}", "put") == "TagResponse"
    assert "TagSchema" not in _components(app)


# ---------------------------------------------------------------------------
# An override that returns an instance of the view's schema
# ---------------------------------------------------------------------------


class _Member(pydantic.BaseModel):
    """A plain schema, without ``from_attributes``."""

    id: int
    name: str
    password: fr.WriteOnly[str]


def _member_app(client, view_body):
    class Member(fr.IDBase):
        name: Mapped[str]
        password: Mapped[str]

    namespace = {"prefix": "/members", "model": Member, "schema": _Member}
    namespace.update(view_body)
    view = type("MemberView", (fr.AsyncRestView,), namespace)
    fr.include_view(client.app, view)
    create_tables()
    client.post("/members/", json={"name": "Ada", "password": "secret"})


def test_a_schema_instance_from_get_one_goes_out_as_the_response(client):
    async def get_one(self, id, *, scope=None):
        return _Member(id=id, name="Ada", password="secret")

    _member_app(client, {"get_one": get_one})

    assert client.get("/members/1").json() == {"id": 1, "name": "Ada"}


def test_schema_instances_from_to_single_response_fill_the_list(client):
    def to_single_response(self, obj):
        return _Member(id=obj.id, name=obj.name.upper(), password="secret")

    _member_app(client, {"to_single_response": to_single_response})

    payload = client.get("/members/").json()
    assert payload["data"] == [{"id": 1, "name": "ADA"}]
    assert client.get("/members/1").json() == {"id": 1, "name": "ADA"}


# ---------------------------------------------------------------------------
# Two classes with the same name
# ---------------------------------------------------------------------------


def test_two_views_with_one_schema_share_the_generated_classes():
    class Account(fr.IDBase):
        name: Mapped[str]

    class AccountSchema(fr.IDSchema):
        name: str

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class AccountView(fr.AsyncRestView):
        prefix = "/accounts"
        model = Account
        schema = AccountSchema

    @fr.include_view(app)
    class AdminAccountView(fr.AsyncRestView):
        prefix = "/admin/accounts"
        model = Account
        schema = AccountSchema

    assert _components(app) == {
        "AccountResponse",
        "AccountCreate",
        "AccountUpdate",
        "AccountListResponse",
    }


def _shop_views(app: fastapi.FastAPI, *, own_schema: bool = False) -> None:
    class Shop(fr.IDBase):
        name: Mapped[str]

    class ShopSchema(fr.IDSchema):
        name: str

    class AllShopsSchema(ShopSchema):
        pass

    @fr.include_view(app)
    class ShopView(fr.AsyncRestView):
        prefix = "/shops"
        model = Shop
        schema = ShopSchema

    @fr.include_view(app)
    class AllShopsView(fr.AsyncRestView):
        prefix = "/all-shops"
        model = Shop
        schema = AllShopsSchema if own_schema else ShopSchema
        pagination = None


def test_two_list_responses_with_one_name_warn_and_stay_in_openapi():
    """Two views share a schema, but only one is paginated. Both list
    responses are named ``ShopListResponse``. Pydantic gives them long names
    in OpenAPI, each route keeps its own, and Restly warns."""
    app = fastapi.FastAPI()
    _shop_views(app)

    with pytest.warns(RestlyMisuseWarning) as record:
        spec = app.openapi()

    paginated = _ref(spec, "/shops", "get")
    unpaginated = _ref(spec, "/all-shops", "get")
    schemas = spec["components"]["schemas"]
    assert paginated != unpaginated
    assert "ShopListResponse" in paginated
    assert "ShopListResponse" in unpaginated
    assert "total_count" in schemas[paginated]["properties"]
    assert "total_count" not in schemas[unpaginated]["properties"]

    [warning] = record
    message = str(warning.message)
    assert message.startswith("More than one class has the name ShopListResponse.")
    assert paginated in message and unpaginated in message
    assert "the list response that Restly generates for ShopView" in message
    assert "the list response that Restly generates for AllShopsView" in message
    assert "rename the view's schema" in message


def test_a_view_with_its_own_schema_name_does_not_clash():
    app = fastapi.FastAPI()
    _shop_views(app, own_schema=True)

    assert _ref(app, "/shops", "get") == "ShopListResponse"
    assert _ref(app, "/all-shops", "get") == "AllShopsListResponse"


def _order_app(*, set_on_view: bool) -> fastapi.FastAPI:
    class Order(fr.IDBase):
        number: Mapped[str]
        note: Mapped[str] = ""

    class OrderSchema(fr.IDSchema):
        number: str
        note: str = ""

    class OrderCreate(pydantic.BaseModel):
        number: str

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class OrderView(fr.AsyncRestView):
        prefix = "/orders"
        model = Order
        schema = OrderSchema
        if set_on_view:
            schema_create = OrderCreate

        @fr.post("/quick")
        async def quick(self, body: OrderCreate) -> dict[str, str]:
            return {"number": body.number}

    return app


def test_a_hand_written_class_with_a_generated_name_warns_and_stays_in_openapi():
    """A hand-written ``OrderCreate`` that differs from the generated one:
    both are in OpenAPI under long names, each route keeps its own, and
    Restly warns."""
    app = _order_app(set_on_view=False)

    with pytest.warns(RestlyMisuseWarning) as record:
        spec = app.openapi()

    generated = _body_ref(spec, "/orders", "post")
    hand_written = _body_ref(spec, "/orders/quick", "post")
    schemas = spec["components"]["schemas"]
    assert generated != hand_written
    assert "OrderCreate" in generated
    assert "OrderCreate" in hand_written
    assert "note" in schemas[generated]["properties"]
    assert "note" not in schemas[hand_written]["properties"]

    [warning] = record
    message = str(warning.message)
    assert message.startswith("More than one class has the name OrderCreate.")
    assert "the create body that Restly generates for OrderView" in message
    assert re.search(r"<locals>\.OrderCreate$", message, re.MULTILINE)
    assert "goes on the view, for example as schema_create" in message


def test_a_hand_written_class_set_on_the_view_does_not_clash():
    app = _order_app(set_on_view=True)

    assert _body_ref(app, "/orders", "post") == "OrderCreate"
    assert _body_ref(app, "/orders/quick", "post") == "OrderCreate"


def test_the_warning_comes_each_time_the_spec_is_read():
    """A test that reads the spec sees the warning, also when an earlier
    test built the spec first."""
    app = _order_app(set_on_view=False)

    for _ in range(2):
        with pytest.warns(RestlyMisuseWarning, match="name OrderCreate"):
            app.openapi()


def test_two_modes_of_one_class_are_not_a_clash():
    """FastAPI shows a class as ``Name-Input`` and ``Name-Output`` when the
    two differ. That is one class, so Restly does not warn."""

    class Label(fr.IDBase):
        text: Mapped[str]

    class Draft(pydantic.BaseModel):
        text: str

        @pydantic.computed_field  # type: ignore[prop-decorator]
        @property
        def length(self) -> int:
            return len(self.text)

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class LabelView(fr.AsyncRestView):
        prefix = "/labels"
        model = Label

        @fr.post("/draft")
        async def draft(self, body: Draft) -> Draft:
            return body

    assert {"Draft-Input", "Draft-Output"} <= _components(app)


def test_two_generic_classes_with_one_name_warn():
    """``Envelope[ItemSchema]`` for two different ``ItemSchema`` classes:
    both the envelopes and the item classes clash."""

    class Item(fr.IDBase):
        name: Mapped[str]

    def item_schema(name_type: type) -> type[pydantic.BaseModel]:
        class ItemSchema(fr.IDSchema):
            name: name_type  # type: ignore[valid-type]

        return ItemSchema

    first, second = item_schema(str), item_schema(int)
    app = fastapi.FastAPI()

    @fr.include_view(app)
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item

        @fr.get("/first")
        async def get_first(self) -> fr.views.Envelope[first]:  # type: ignore[valid-type]
            return fr.views.Envelope(data=[])

        @fr.get("/second")
        async def get_second(self) -> fr.views.Envelope[second]:  # type: ignore[valid-type]
            return fr.views.Envelope(data=[])

    with pytest.warns(RestlyMisuseWarning) as record:
        app.openapi()

    names = sorted(str(w.message).split(".", 1)[0] for w in record)
    assert names == [
        "More than one class has the name Envelope_ItemSchema_",
        "More than one class has the name ItemSchema",
    ]
