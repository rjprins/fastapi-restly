"""The names of the classes that Restly generates for a view.

A generated class is named ``<Resource><Role>``. Resource is the class name
of the view's schema without a final ``Schema`` or ``Response``. The client
sees these names in OpenAPI. It sees the view's schema only when another
schema nests it or a custom route names it. When two different classes get
the same name, Restly warns.
"""

import re
import warnings
from typing import Any

import fastapi
import pydantic
import pytest
from pydantic.alias_generators import to_camel
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly.exc import RestlyDuplicateSchemaNameWarning

from .conftest import create_tables

Spec = dict[str, Any]


def _spec(app_or_spec: fastapi.FastAPI | Spec) -> Spec:
    if isinstance(app_or_spec, dict):
        return app_or_spec
    with warnings.catch_warnings():
        warnings.simplefilter("error", RestlyDuplicateSchemaNameWarning)
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


class _Priced(pydantic.BaseModel):
    """A schema with camelCase aliases and a validator that changes a value."""

    model_config = pydantic.ConfigDict(alias_generator=to_camel)

    id: int
    full_name: str
    price: int

    @pydantic.field_validator("price")
    @classmethod
    def _in_cents(cls, value: int) -> int:
        return value * 100


def test_a_schema_instance_goes_out_without_a_second_validation(client):
    """The response class copies an instance of the view's schema. It does
    not read the instance again by its aliases, and it does not run the
    validators again."""

    class Priced(fr.IDBase):
        full_name: Mapped[str]
        price: Mapped[int]

    @fr.include_view(client.app)
    class PricedView(fr.AsyncRestView):
        prefix = "/priced"
        model = Priced
        schema = _Priced

        def to_single_response(self, obj):
            return _Priced(id=obj.id, fullName=obj.full_name, price=3)

    create_tables()
    created = client.post("/priced/", json={"fullName": "Ada", "price": 1})

    expected = {"id": 1, "fullName": "Ada", "price": 300}
    assert created.status_code == 201
    assert created.json() == expected
    assert client.get("/priced/1").json() == expected
    assert client.get("/priced/").json()["data"] == [expected]


# ---------------------------------------------------------------------------
# A forward reference that the module defines later
# ---------------------------------------------------------------------------


def test_a_forward_reference_can_be_defined_after_the_response_class():
    """Restly builds the response class when it includes the view. A name that
    the schema's module defines later does not stop it, also when the class
    has WriteOnly fields to remove."""
    from . import _forward_ref_schemas as schemas

    author = schemas.AuthorResponse.model_validate(
        {"id": 1, "name": "Ada", "note": {"text": "hi"}}
    )
    login = schemas.LoginResponse.model_validate(
        {"id": 1, "name": "Ada", "note": {"text": "hi"}}
    )

    assert author.model_dump() == {"id": 1, "name": "Ada", "note": {"text": "hi"}}
    assert login.model_dump() == {"id": 1, "name": "Ada", "note": {"text": "hi"}}
    assert "password" not in schemas.LoginResponse.model_fields


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

    with pytest.warns(RestlyDuplicateSchemaNameWarning) as record:
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
    generated = "the list response that Restly generates from {}.ShopSchema for {}"
    schema_path = "tests.test_generated_names._shop_views.<locals>"
    assert generated.format(schema_path, "ShopView") in message
    assert generated.format(schema_path, "AllShopsView") in message
    assert "rename the view's schema" in message


def test_views_on_a_router_are_checked_through_configure():
    """``fr.configure(app)`` lets Restly check an app whose views are all on
    an ``APIRouter``. A view without a schema gets its classes from its
    model, and the warning says so."""

    class Account(fr.IDBase):
        name: Mapped[str]

    router = fastapi.APIRouter()

    @fr.include_view(router)
    class AccountView(fr.AsyncRestView):
        prefix = "/accounts"
        model = Account

    @fr.include_view(router)
    class AllAccountsView(fr.AsyncRestView):
        prefix = "/all-accounts"
        model = Account
        pagination = None

    app = fastapi.FastAPI()
    app.include_router(router)
    fr.configure(app)

    with pytest.warns(RestlyDuplicateSchemaNameWarning) as record:
        app.openapi()

    [warning] = record
    message = str(warning.message)
    model = f"{Account.__module__}.{Account.__qualname__}"
    generated = "the list response that Restly generates from the model {} for {}"
    assert generated.format(model, "AccountView") in message
    assert generated.format(model, "AllAccountsView") in message
    assert "give a view without a schema one of its own" in message


def test_only_the_routes_in_the_app_name_a_view():
    """A view that excludes its list route, and a react-admin view, whose list
    route returns a plain response, do not use the list response."""

    class Tag(fr.IDBase):
        name: Mapped[str]

    class TagSchema(fr.IDSchema):
        name: str

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class TagView(fr.AsyncRestView):
        prefix = "/tags"
        model = Tag
        schema = TagSchema

    @fr.include_view(app)
    class AllTagsView(fr.AsyncRestView):
        prefix = "/all-tags"
        model = Tag
        schema = TagSchema
        pagination = None

    @fr.include_view(app)
    class TagLookupView(fr.AsyncRestView):
        prefix = "/tag-lookup"
        model = Tag
        schema = TagSchema
        exclude_routes = ("get_many_endpoint",)

    @fr.include_view(app)
    class TagAdminView(fr.AsyncReactAdminView):
        prefix = "/admin/tags"
        model = Tag
        schema = TagSchema

    with pytest.warns(RestlyDuplicateSchemaNameWarning) as record:
        app.openapi()

    [warning] = record
    message = str(warning.message)
    assert re.search(r"for TagView$", message, re.MULTILINE)
    assert re.search(r"for AllTagsView$", message, re.MULTILINE)
    assert "TagLookupView" not in message
    assert "TagAdminView" not in message


def test_a_create_body_that_a_subclass_sets_is_its_own():
    """A subclass sets its own ``schema_create`` and generates nothing else.
    The warning does not call that class generated."""

    class Item(fr.IDBase):
        name: Mapped[str]
        note: Mapped[str] = ""

    class ItemSchema(fr.IDSchema):
        name: str
        note: str = ""

    class ItemCreate(pydantic.BaseModel):
        name: str

    class ItemUpdate(pydantic.BaseModel):
        name: str | None = None

    app = fastapi.FastAPI()

    @fr.include_view(app)
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        schema_update = ItemUpdate
        schema_list_params = fr.query.derive_schema_list_params(ItemSchema, Item)

    @fr.include_view(app)
    class QuickItemView(ItemView):
        prefix = "/quick-items"
        schema_create = ItemCreate

    with pytest.warns(RestlyDuplicateSchemaNameWarning) as record:
        app.openapi()

    [warning] = record
    message = str(warning.message)
    assert re.search(
        r"<locals>\.ItemCreate: the create body of QuickItemView$",
        message,
        re.MULTILINE,
    )
    assert re.search(
        r"the create body that Restly generates from \S+ItemSchema for ItemView$",
        message,
        re.MULTILINE,
    )


def test_a_class_that_only_a_webhook_uses_is_checked():
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

    @app.webhooks.post("order-created")
    def order_created(body: OrderCreate) -> None:
        """Restly sends this when an order is created."""

    with pytest.warns(RestlyDuplicateSchemaNameWarning) as record:
        app.openapi()

    [warning] = record
    message = str(warning.message)
    assert message.startswith("More than one class has the name OrderCreate.")
    assert re.search(r"<locals>\.OrderCreate$", message, re.MULTILINE)


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

    with pytest.warns(RestlyDuplicateSchemaNameWarning) as record:
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
    assert (
        "the create body that Restly generates from "
        "tests.test_generated_names._order_app.<locals>.OrderSchema for OrderView"
    ) in message
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
        with pytest.warns(RestlyDuplicateSchemaNameWarning, match="name OrderCreate"):
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

    with pytest.warns(RestlyDuplicateSchemaNameWarning) as record:
        app.openapi()

    names = sorted(str(w.message).split(".", 1)[0] for w in record)
    assert names == [
        "More than one class has the name Envelope_ItemSchema_",
        "More than one class has the name ItemSchema",
    ]
