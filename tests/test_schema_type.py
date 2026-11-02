"""``SchemaType``: a type that depends on the view.

A setting that takes a schema class also takes a function of the view, and a
route takes one in an annotation. Restly calls the function once for each view,
when something first reads the type, and not when Python creates the class. So a
view can be read before it registers, and setups that need the class definition
to be over keep working.
"""

import copy
import dataclasses
import functools
import sys
import threading
from typing import (
    Annotated,
    Any,
    Generic,
    NamedTuple,
    Optional,
    TypedDict,
    TypeVar,
    get_args,
)

import pydantic
import pytest
import sqlalchemy
from fastapi import FastAPI
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing_extensions import TypeAliasType

import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError
from fastapi_restly.testing._client import RestlyTestClient
from fastapi_restly.views import (
    CreateSchema,
    IDType,
    ListResponseSchema,
    PaginatedEnvelope,
    ResponseSchema,
    SchemaType,
)

from ._models import SharedBase
from .conftest import create_tables

T = TypeVar("T")


class Data(pydantic.BaseModel, Generic[T]):
    data: T


def _ref(schema):
    return schema["$ref"].rsplit("/", 1)[-1]


def _response_ref(operation, status="200"):
    return _ref(operation["responses"][status]["content"]["application/json"]["schema"])


def _body_ref(operation):
    return _ref(operation["requestBody"]["content"]["application/json"]["schema"])


def _with_note(schema):
    """A class per schema, as a function on a base view would build it."""
    return pydantic.create_model(
        f"{schema.__name__}Noted", __base__=schema, note=(fr.ReadOnly[str], "noted")
    )


@pytest.fixture
def Lamp():
    class Lamp(fr.IDMixin, SharedBase):
        name: Mapped[str]

    return Lamp


class LampSchema(fr.IDSchema):
    name: str


# --- the setting form --------------------------------------------------------


def test_a_function_as_the_schema(client, Lamp):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = SchemaType(lambda view: LampSchema)

    fr.include_view(client.app, LampView)
    create_tables()
    assert LampView.schema is LampSchema
    assert client.post("/lamps/", json={"name": "a"}).json()["name"] == "a"


def test_a_function_as_the_response_class(client, Lamp):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        response_schema = SchemaType(lambda view: _with_note(view.schema))

    fr.include_view(client.app, LampView)
    create_tables()
    created = client.post("/lamps/", json={"name": "a"}).json()
    assert created["note"] == "noted"
    assert client.get(f"/lamps/{created['id']}").json()["note"] == "noted"
    paths = client.app.openapi()["paths"]
    assert _response_ref(paths["/lamps/{id}"]["get"]) == "LampSchemaNoted"


def test_a_function_as_the_create_body_and_the_update_body(client, Lamp):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        create_schema = SchemaType(
            lambda view: pydantic.create_model(
                "LampIn", name=(str, ...), __base__=pydantic.BaseModel
            )
        )
        update_schema = SchemaType(
            lambda view: fr.schemas.derive_update_schema(view.create_schema, view.model)
        )

    fr.include_view(client.app, LampView)
    create_tables()
    paths = client.app.openapi()["paths"]
    assert _body_ref(paths["/lamps"]["post"]) == "LampIn"
    assert _body_ref(paths["/lamps/{id}"]["patch"]) == "LampInUpdate"
    created = client.post("/lamps/", json={"name": "a"}).json()
    patched = client.patch(f"/lamps/{created['id']}", json={"name": "b"}).json()
    assert patched["name"] == "b"


def test_a_function_as_the_list_params(client, Lamp):
    class LampParams(pydantic.BaseModel):
        name: str | None = None
        page: int = 1
        page_size: int = 10

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        list_params_schema = SchemaType(lambda view: LampParams)

    fr.include_view(client.app, LampView)
    assert LampView.list_params_schema is LampParams


def test_the_checks_of_list_params_apply_to_what_a_function_returns(client, Lamp):
    class NoPages(pydantic.BaseModel):
        name: str | None = None

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        list_params_schema = SchemaType(lambda view: NoPages)

    with pytest.raises(RestlyConfigurationError, match="no 'page' and no 'page_size'"):
        fr.include_view(client.app, LampView)


def test_a_setting_as_a_decorator(client, Lamp):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

        @SchemaType
        def response_schema(view):
            return _with_note(view.schema)

    fr.include_view(client.app, LampView)
    create_tables()
    assert client.post("/lamps/", json={"name": "a"}).json()["note"] == "noted"


def test_an_instance_reads_the_same_class(Lamp):
    class LampView(fr.AsyncRestView):
        model = Lamp
        schema = LampSchema
        response_schema = SchemaType(lambda view: _with_note(view.schema))

    assert LampView().response_schema is LampView.response_schema


# --- the route form ----------------------------------------------------------


def test_a_function_in_a_route_gets_the_view(client, Lamp):
    class Bulb(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class BulbSchema(fr.IDSchema):
        name: str

    Single = Annotated[Any, SchemaType(lambda view: Data[view.response_schema])]

    class ApiView(fr.AsyncRestView):
        @fr.get("/{id}/wrapped")
        async def wrapped(self, id: IDType) -> Single:
            row = await self.handle_get_one(id)
            return {"data": self.to_single_response(row)}

    class LampView(ApiView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

    class BulbView(ApiView):
        prefix = "/bulbs"
        model = Bulb
        schema = BulbSchema

    fr.include_view(client.app, LampView)
    fr.include_view(client.app, BulbView)
    create_tables()
    paths = client.app.openapi()["paths"]
    assert _response_ref(paths["/lamps/{id}/wrapped"]["get"]) == "Data_LampResponse_"
    assert _response_ref(paths["/bulbs/{id}/wrapped"]["get"]) == "Data_BulbResponse_"
    created = client.post("/lamps/", json={"name": "a"}).json()
    assert client.get(f"/lamps/{created['id']}/wrapped").json() == {"data": created}


def test_a_function_in_a_route_can_give_any_type(client, Lamp):
    Names = Annotated[Any, SchemaType(lambda view: list[view.response_schema])]

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

        @fr.get("/all")
        async def all_lamps(self) -> Names:
            return []

    fr.include_view(client.app, LampView)
    schema = client.app.openapi()["paths"]["/lamps/all"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert schema["type"] == "array"
    assert _ref(schema["items"]) == "LampResponse"


def test_a_function_that_gives_the_response_class_goes_through_the_view(client, Lamp):
    Own = Annotated[Any, SchemaType(lambda view: view.response_schema)]

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

        @fr.get("/{id}/own")
        async def own(self, id: IDType) -> Own:
            return await self.handle_get_one(id)

    fr.include_view(client.app, LampView)
    create_tables()
    created = client.post("/lamps/", json={"name": "a"}).json()
    assert client.get(f"/lamps/{created['id']}/own").json() == created


def test_a_function_on_a_plain_view_raises():
    Own = Annotated[Any, SchemaType(lambda view: view.response_schema)]

    class PingView(fr.View):
        prefix = "/ping"

        @fr.get("/")
        def ping(self) -> Own:
            return {}

    with pytest.raises(
        RestlyConfigurationError, match=r"PingView\.ping uses SchemaType"
    ):
        fr.include_view(FastAPI(), PingView)


# --- when and how often the function runs ------------------------------------


def test_the_class_definition_and_a_subclass_do_not_call_the_function(Lamp):
    calls = []

    class ApiView(fr.AsyncRestView):
        @SchemaType
        def response_schema(view):
            calls.append(view)
            return _with_note(view.schema)

    class LampView(ApiView):
        model = Lamp
        schema = LampSchema

    class SubView(LampView):
        pass

    assert calls == []


def test_the_function_runs_once_for_each_view(client, Lamp):
    calls = []

    def noted(view):
        calls.append(view.__name__)
        return _with_note(view.schema)

    class ApiView(fr.AsyncRestView):
        response_schema = SchemaType(noted)

    class LampView(ApiView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

    class SubView(LampView):
        prefix = "/sub-lamps"

    first = LampView.response_schema
    assert LampView.response_schema is first
    fr.include_view(client.app, LampView)
    fr.include_view(client.app, SubView)
    assert LampView.response_schema is first
    assert SubView.response_schema is not first
    assert calls == ["LampView", "SubView"]


def test_two_objects_around_one_function_give_one_class(client, Lamp):
    def noted(view):
        return _with_note(view.schema)

    Noted = Annotated[Any, SchemaType(noted)]

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        response_schema = SchemaType(noted)

        @fr.get("/one")
        def one(self) -> Noted:
            return {"id": 1, "name": "a", "note": "x"}

    fr.include_view(client.app, LampView)
    paths = client.app.openapi()["paths"]
    assert _response_ref(paths["/lamps/one"]["get"]) == "LampSchemaNoted"
    assert _response_ref(paths["/lamps/{id}"]["get"]) == "LampSchemaNoted"


def test_a_function_reads_any_other_setting_in_any_order(Lamp):
    class LampView(fr.AsyncRestView):
        model = Lamp

        @SchemaType
        def update_schema(view):
            return fr.schemas.derive_update_schema(view.create_schema, view.model)

        @SchemaType
        def create_schema(view):
            return _with_note(view.response_schema)

    assert issubclass(LampView.update_schema, LampView.create_schema)
    assert issubclass(LampView.create_schema, LampView.response_schema)
    assert issubclass(LampView.response_schema, LampView.schema)


def test_a_function_that_reads_itself_raises(Lamp):
    class LampView(fr.AsyncRestView):
        model = Lamp

        @SchemaType
        def response_schema(view):
            return view.create_schema

        @SchemaType
        def create_schema(view):
            return view.response_schema

    with pytest.raises(RestlyConfigurationError, match="reads its own result"):
        LampView.response_schema
    # the failed read leaves nothing behind
    with pytest.raises(RestlyConfigurationError, match="reads its own result"):
        LampView.create_schema


def test_a_function_that_fails_runs_again_on_the_next_read(Lamp):
    attempts = []

    def flaky(view):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("not yet")
        return LampSchema

    class LampView(fr.AsyncRestView):
        model = Lamp
        schema = SchemaType(flaky)

    with pytest.raises(RuntimeError):
        LampView.schema
    assert LampView.schema is LampSchema


def test_two_threads_that_read_first_get_one_class_and_no_false_cycle(Lamp):
    both_inside = threading.Barrier(2, timeout=5)

    def built(view):
        both_inside.wait()
        return _with_note(LampSchema)

    class LampView(fr.AsyncRestView):
        model = Lamp
        schema = LampSchema
        response_schema = SchemaType(built)

    results, errors = [], []

    def read():
        try:
            results.append(LampView.response_schema)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=read) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert results[0] is results[1]
    assert LampView.response_schema is results[0]


# --- a read before registration ----------------------------------------------


def test_a_read_before_registration_gives_the_class_of_the_registered_view(
    client, Lamp
):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

    before = {
        name: getattr(LampView, name)
        for name in (
            "schema",
            "response_schema",
            "create_schema",
            "update_schema",
            "list_params_schema",
        )
    }
    assert sorted(before["create_schema"].model_fields) == ["name"]

    routes = len(client.app.routes)
    assert len(client.app.routes) == routes
    fr.include_view(client.app, LampView)
    assert before == {name: getattr(LampView, name) for name in before}
    assert len(client.app.routes) > routes
    paths = client.app.openapi()["paths"]
    schemas = client.app.openapi()["components"]["schemas"]
    assert _body_ref(paths["/lamps"]["post"]) == "LampCreate"
    assert list(schemas["LampCreate"]["properties"]) == ["name"]


def test_a_read_before_registration_does_not_mount_or_prepare_the_view(Lamp):
    prepared = []

    class LampView(fr.AsyncRestView):
        model = Lamp
        schema = LampSchema

        @classmethod
        def before_include_view(cls):
            prepared.append(cls)
            super().before_include_view()

    LampView.create_schema
    LampView.response_schema
    assert prepared == []
    assert "_fr_initialised" not in vars(LampView)


def test_a_subclass_of_a_registered_view_has_its_own_classes(client, Lamp):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp

    fr.include_view(client.app, LampView)

    class OtherSchema(fr.IDSchema):
        name: str
        other: str = "x"

    class SubView(LampView):
        schema = OtherSchema

    assert "other" in SubView.response_schema.model_fields
    assert "other" in SubView.create_schema.model_fields
    assert "other" not in LampView.response_schema.model_fields


def test_a_subclass_with_a_new_schema_gets_the_default_back(Lamp):
    class Own(pydantic.BaseModel):
        name: str

    class OtherSchema(fr.IDSchema):
        name: str
        other: str = "x"

    class LampView(fr.AsyncRestView):
        model = Lamp
        schema = LampSchema
        create_schema = Own

    class SubView(LampView):
        schema = OtherSchema

    class SameView(LampView):
        pass

    assert SameView.create_schema is Own
    assert "other" in SubView.create_schema.model_fields


def test_a_subclass_with_a_new_schema_keeps_a_function(Lamp):
    class OtherSchema(fr.IDSchema):
        name: str

    class LampView(fr.AsyncRestView):
        model = Lamp
        schema = LampSchema
        response_schema = SchemaType(lambda view: _with_note(view.schema))

    class SubView(LampView):
        schema = OtherSchema

    assert issubclass(SubView.response_schema, OtherSchema)
    assert issubclass(LampView.response_schema, LampSchema)


def test_a_class_in_a_subclass_wins_over_a_function(Lamp):
    class Own(pydantic.BaseModel):
        name: str

    class ApiView(fr.AsyncRestView):
        response_schema = SchemaType(lambda view: _with_note(view.schema))

    class LampView(ApiView):
        model = Lamp
        schema = LampSchema
        response_schema = Own

    assert LampView.response_schema is Own


# --- setups that a read at class definition would break ----------------------


def test_a_model_whose_related_class_comes_later(client):
    class Writer(fr.IDMixin, SharedBase):
        name: Mapped[str]
        novels: Mapped[list["Novel"]] = relationship(
            back_populates="writer", default_factory=list
        )

    class WriterView(fr.AsyncRestView):
        prefix = "/writers"
        model = Writer

    class Novel(fr.IDMixin, SharedBase):
        name: Mapped[str]
        writer_id: Mapped[int] = mapped_column(ForeignKey(Writer.id))
        writer: Mapped[Writer] = relationship(back_populates="novels", init=False)

    fr.include_view(client.app, WriterView)
    assert "name" in WriterView.response_schema.model_fields


def test_a_schema_with_a_forward_ref_to_a_later_class(client, Lamp):
    class ShelfSchema(fr.IDSchema):
        name: str
        label: Optional["ShelfLabel"] = None

    class ShelfView(fr.AsyncRestView):
        prefix = "/shelves"
        model = Lamp
        schema = ShelfSchema

    class ShelfLabel(pydantic.BaseModel):
        text: str

    ShelfSchema.model_rebuild(_types_namespace={"ShelfLabel": ShelfLabel})
    fr.include_view(client.app, ShelfView)
    schemas = client.app.openapi()["components"]["schemas"]
    assert "label" in schemas["ShelfResponse"]["properties"]


def test_a_base_view_that_changes_the_schema_in_init_subclass(client, Lamp):
    class ApiView(fr.AsyncRestView):
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__(**kwargs)
            if "schema" in vars(cls):
                cls.schema = _with_note(cls.schema)

    class LampView(ApiView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

    fr.include_view(client.app, LampView)
    create_tables()
    assert client.post("/lamps/", json={"name": "a"}).json()["note"] == "noted"


def test_a_class_decorator_that_sets_the_schema(client, Lamp):
    class Other(fr.IDSchema):
        name: str
        note: fr.ReadOnly[str] = "noted"

    def with_other_schema(cls):
        cls.schema = Other
        return cls

    class ApiView(fr.AsyncRestView):
        schema = LampSchema
        response_schema = LampSchema

    @fr.include_view(client.app)
    @with_other_schema
    class LampView(ApiView):
        prefix = "/lamps"
        model = Lamp

    create_tables()
    # the response class of the base view was built for the old schema
    assert client.post("/lamps/", json={"name": "a"}).json()["note"] == "noted"


def test_a_base_view_with_a_model_that_lacks_a_setting_its_function_reads(client, Lamp):
    class ApiView(fr.AsyncRestView):
        @SchemaType
        def response_schema(view):
            return pydantic.create_model(
                f"{view.schema.__name__}{view.item_key}", __base__=view.schema
            )

    class BaseLampView(ApiView):
        model = Lamp
        schema = LampSchema

    class LampView(BaseLampView):
        prefix = "/lamps"
        item_key = "Keyed"

    fr.include_view(client.app, LampView)
    assert LampView.response_schema.__name__ == "LampSchemaKeyed"
    assert not hasattr(BaseLampView, "response_schema")


def test_a_view_without_a_model_has_no_generated_classes():
    class ApiView(fr.AsyncRestView):
        pass

    assert not hasattr(ApiView, "schema")
    assert not hasattr(ApiView, "response_schema")
    assert not hasattr(ApiView, "create_schema")


# --- wrong values ------------------------------------------------------------


class NotAModel:
    pass


@pytest.mark.parametrize(
    "value",
    ["oops", NotAModel, sqlalchemy.types.SchemaType(), None],
    ids=["text", "plain-class", "sqlalchemy-schematype", "none"],
)
@pytest.mark.parametrize(
    "setting", ["response_schema", "create_schema", "update_schema", "schema"]
)
def test_a_wrong_value_for_a_setting_raises_at_registration(Lamp, setting, value):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp

    setattr(LampView, setting, value)
    if setting != "schema":
        LampView.schema = LampSchema
    with pytest.raises(
        RestlyConfigurationError,
        match=rf"LampView\.{setting} must be a Pydantic class or a",
    ):
        fr.include_view(FastAPI(), LampView)


def test_a_function_that_gives_nothing_raises(Lamp):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        response_schema = SchemaType(lambda view: None)

    with pytest.raises(RestlyConfigurationError, match="of LampView returned None"):
        fr.include_view(FastAPI(), LampView)


@pytest.mark.parametrize(
    "result", [list[LampSchema], NotAModel], ids=["list", "plain-class"]
)
def test_a_function_that_gives_something_else_than_a_class_raises(Lamp, result):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        response_schema = SchemaType(lambda view: result)

    with pytest.raises(
        RestlyConfigurationError,
        match=r"SchemaType function of LampView\.response_schema must return",
    ):
        fr.include_view(FastAPI(), LampView)


def test_a_view_without_a_model_raises_at_registration():
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"

    with pytest.raises(ValueError, match=r"'LampView\.model' must be specified"):
        fr.include_view(FastAPI(), LampView)


# --- a marker in a field of a class ------------------------------------------


class _Wrapped(pydantic.BaseModel):
    thing: ResponseSchema


class _WrappedList(pydantic.BaseModel):
    things: list[ResponseSchema]


class _WrappedFunction(pydantic.BaseModel):
    thing: Annotated[Any, SchemaType(lambda view: view.schema)]


class _Outer(pydantic.BaseModel):
    inner: _Wrapped | None = None


@pytest.mark.parametrize(
    ("declared", "model", "field"),
    [
        (_Wrapped, "_Wrapped", "thing"),
        (_WrappedList, "_WrappedList", "things"),
        (_WrappedFunction, "_WrappedFunction", "thing"),
        (_Outer, "_Wrapped", "thing"),
        (list[_Wrapped], "_Wrapped", "thing"),
        (_Wrapped | None, "_Wrapped", "thing"),
        (Data[_Wrapped], "_Wrapped", "thing"),
    ],
    ids=["field", "in-list", "function", "nested", "route-list", "optional", "generic"],
)
def test_a_marker_in_a_field_of_a_class_that_a_route_names_raises(
    Lamp, declared, model, field
):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

        @fr.get("/x")
        def x(self) -> declared:
            return {}

    with pytest.raises(
        RestlyConfigurationError,
        match=rf"LampView\.x names the class {model}, and its field {field} uses",
    ):
        fr.include_view(FastAPI(), LampView)


def test_a_marker_in_a_field_of_a_body_or_a_response_model_raises(Lamp):
    class BodyView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp

        @fr.post("/x")
        def x(self, body: _Wrapped) -> dict[str, int]:
            return {}

    with pytest.raises(RestlyConfigurationError, match="field thing"):
        fr.include_view(FastAPI(), BodyView)

    class ModelView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp

        @fr.get("/x", response_model=_Wrapped)
        def x(self):
            return {}

    with pytest.raises(RestlyConfigurationError, match="field thing"):
        fr.include_view(FastAPI(), ModelView)


def test_a_marker_in_a_field_on_a_plain_view_raises():
    class PingView(fr.View):
        prefix = "/ping"

        @fr.get("/")
        def ping(self) -> _Wrapped:
            return {}

    with pytest.raises(RestlyConfigurationError, match="field thing"):
        fr.include_view(FastAPI(), PingView)


def test_a_recursive_class_ends_the_search(Lamp):
    class Node(pydantic.BaseModel):
        children: list["Node"] = []

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp

        @fr.get("/tree")
        def tree(self) -> Node:
            return Node()

    fr.include_view(FastAPI(), LampView)


# --- the public derive functions ---------------------------------------------


def test_derive_create_and_update_leave_out_the_read_only_fields(Lamp):
    class Schema(fr.BaseSchema):
        id: fr.ReadOnly[int]
        name: str

    create = fr.schemas.derive_create_schema(Schema, Lamp)
    update = fr.schemas.derive_update_schema(Schema, Lamp)
    assert list(create.model_fields) == ["name"]
    assert list(update.model_fields) == ["name"]
    assert update().model_dump() == {"name": None}
    assert create.__name__ == "SchemaCreate"
    assert update.__name__ == "SchemaUpdate"


def test_derive_create_and_update_leave_out_a_generated_key(Lamp):
    class Plain(fr.BaseSchema):
        id: int
        name: str

    assert list(fr.schemas.derive_create_schema(Plain, Lamp).model_fields) == ["name"]
    assert list(fr.schemas.derive_update_schema(Plain, Lamp).model_fields) == ["name"]


def test_derive_gives_the_class_of_the_view(client, Lamp):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

    assert fr.schemas.derive_create_schema(LampSchema, Lamp) is LampView.create_schema
    assert fr.schemas.derive_update_schema(LampSchema, Lamp) is LampView.update_schema


def test_derive_update_from_a_class_of_your_own(client, Lamp):
    class LampIn(fr.BaseSchema):
        name: str

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        create_schema = LampIn
        update_schema = fr.schemas.derive_update_schema(LampIn, Lamp)

    fr.include_view(client.app, LampView)
    create_tables()
    created = client.post("/lamps/", json={"name": "a"}).json()
    patched = client.patch(f"/lamps/{created['id']}", json={}).json()
    assert patched["name"] == "a"


# --- react-admin -------------------------------------------------------------


def test_a_react_admin_view_keeps_its_list_params_with_a_new_schema(Lamp):
    class LampView(fr.views.AsyncReactAdminView):
        model = Lamp

    class SubView(LampView):
        schema = LampSchema

    assert SubView.list_params_schema is fr.views.ReactAdminListParams


def test_a_react_admin_view_takes_a_function_for_its_list_params(client, Lamp):
    class LampParams(fr.views.ReactAdminListParams):
        pass

    class LampView(fr.views.AsyncReactAdminView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        list_params_schema = SchemaType(lambda view: LampParams)

    fr.include_view(client.app, LampView)
    assert LampView.list_params_schema is LampParams


def test_a_react_admin_view_checks_what_a_function_gives_at_registration(Lamp):
    class Wrong(pydantic.BaseModel):
        pass

    class LampView(fr.views.AsyncReactAdminView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        list_params_schema = SchemaType(lambda view: Wrong)

    with pytest.raises(RestlyConfigurationError, match="ReactAdminListParams"):
        fr.include_view(FastAPI(), LampView)


def test_a_sync_view_takes_a_function(sync_db):
    engine, _ = sync_db

    class SyncLamp(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class LampView(fr.RestView):
        prefix = "/lamps"
        model = SyncLamp
        schema = LampSchema
        response_schema = SchemaType(lambda view: _with_note(view.schema))

    app = FastAPI()
    fr.include_view(app, LampView)
    SharedBase.metadata.create_all(engine)
    client = RestlyTestClient(app)
    assert client.post("/lamps/", json={"name": "a"}).json()["note"] == "noted"


# --- recipes in the docs ------------------------------------------------------


def test_endpoints_replaced_on_a_base_view_give_each_view_its_types(client, Lamp):
    class Bulb(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class BulbSchema(fr.IDSchema):
        name: str

    class ApiView(fr.AsyncRestView):
        @fr.get("/{id}")
        async def get_one_endpoint(self, id: IDType) -> Data[ResponseSchema]:
            obj = await self.handle_get_one(id)
            return {"data": self.to_single_response(obj)}

        @fr.post("/")
        async def create_endpoint(self, data: CreateSchema) -> Data[ResponseSchema]:
            obj = await self.handle_create(data)
            return {"data": self.to_single_response(obj)}

    class LampView(ApiView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

    class BulbView(ApiView):
        prefix = "/bulbs"
        model = Bulb
        schema = BulbSchema

    fr.include_view(client.app, LampView)
    fr.include_view(client.app, BulbView)
    create_tables()

    paths = client.get("/openapi.json").json()["paths"]
    assert _response_ref(paths["/lamps/{id}"]["get"]) == "Data_LampResponse_"
    assert _response_ref(paths["/bulbs/{id}"]["get"]) == "Data_BulbResponse_"
    assert _response_ref(paths["/lamps"]["post"], "201") == "Data_LampResponse_"
    assert _body_ref(paths["/bulbs"]["post"]) == "BulbCreate"

    created = client.post("/lamps", json={"name": "desk"}).json()
    assert created == {"data": {"id": 1, "name": "desk"}}
    assert client.get("/lamps/1").json() == created


def test_a_delete_that_sends_the_row_shows_it_in_the_spec(client, Lamp):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

        @fr.delete("/{id}", status_code=200)
        async def delete_endpoint(self, id: int) -> ResponseSchema:
            obj = await self.get_one(id)
            response = self.to_single_response(obj)
            await self.handle_delete(id)
            return response

    fr.include_view(client.app, LampView)
    create_tables()
    client.post("/lamps", json={"name": "desk"})

    paths = client.get("/openapi.json").json()["paths"]
    assert _response_ref(paths["/lamps/{id}"]["delete"]) == "LampResponse"
    assert client.delete("/lamps/1").json() == {"id": 1, "name": "desk"}
    client.get("/lamps/1", assert_status_code=404)


def _keyed_list(view):
    key = view.list_key
    return pydantic.create_model(
        f"{key.title()}List",
        data=(list[view.response_schema], pydantic.Field(alias=key)),
        total_count=(int, pydantic.Field(alias=f"{key}Count")),
    )


def _keyed_single(view):
    key = view.single_key
    return pydantic.create_model(
        f"{key.title()}Single", data=(view.response_schema, pydantic.Field(alias=key))
    )


def test_each_view_gets_its_own_keys(client, Lamp):
    Single = Annotated[Any, SchemaType(_keyed_single)]

    class Bulb(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class BulbSchema(fr.IDSchema):
        name: str

    class ApiView(fr.AsyncRestView):
        list_response_schema = SchemaType(_keyed_list)

        @fr.get("/{id}")
        async def get_one_endpoint(self, id: IDType) -> Single:
            obj = await self.handle_get_one(id)
            return {self.single_key: self.to_single_response(obj)}

    class LampView(ApiView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        list_key = "lamps"
        single_key = "lamp"

    class BulbView(ApiView):
        prefix = "/bulbs"
        model = Bulb
        schema = BulbSchema
        list_key = "bulbs"
        single_key = "bulb"

    fr.include_view(client.app, LampView)
    fr.include_view(client.app, BulbView)
    create_tables()
    client.post("/lamps", json={"name": "desk"})

    spec = client.get("/openapi.json").json()
    assert _response_ref(spec["paths"]["/lamps"]["get"]) == "LampsList"
    assert _response_ref(spec["paths"]["/bulbs"]["get"]) == "BulbsList"
    assert _response_ref(spec["paths"]["/lamps/{id}"]["get"]) == "LampSingle"
    schemas = spec["components"]["schemas"]
    assert set(schemas["LampSingle"]["properties"]) == {"lamp"}
    assert set(schemas["BulbsList"]["properties"]) == {"bulbs", "bulbsCount"}

    row = {"id": 1, "name": "desk"}
    assert client.get("/lamps").json() == {"lamps": [row], "lampsCount": 1}
    assert client.get("/lamps/1").json() == {"lamp": row}


# --- what a function gives ----------------------------------------------------


def test_a_route_function_that_returns_nothing_raises(client, Lamp):
    def forgot_the_return(view):
        Data[view.response_schema]

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

        @fr.get("/{id}/wrapped")
        async def wrapped(
            self, id: IDType
        ) -> Annotated[Any, SchemaType(forgot_the_return)]:
            return {}

    with pytest.raises(RestlyConfigurationError, match="forgot_the_return.*None"):
        fr.include_view(client.app, LampView)


@pytest.mark.parametrize(
    "fn",
    [
        lambda view: ResponseSchema,
        lambda view: Optional[ResponseSchema],
        lambda view: Data[ResponseSchema],
    ],
    ids=["bare", "optional", "generic"],
)
def test_a_function_can_return_a_marker(client, Lamp, fn):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

        @fr.get("/{id}/made")
        async def made(self, id: IDType) -> Annotated[Any, SchemaType(fn)]:
            return None

    fr.include_view(client.app, LampView)
    assert "LampResponse" in str(client.get("/openapi.json").json()["paths"])


def test_a_function_that_allows_none_uses_the_response_step(client, Lamp):
    calls = []

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

        def to_single_response(self, obj):
            calls.append(obj)
            return super().to_single_response(obj)

        @fr.get("/{id}/maybe")
        async def maybe(
            self, id: IDType
        ) -> Annotated[Any, SchemaType(lambda view: Optional[view.response_schema])]:
            return await self.handle_get_one(id)

    fr.include_view(client.app, LampView)
    create_tables()
    client.post("/lamps", json={"name": "desk"})
    calls.clear()

    assert client.get("/lamps/1/maybe").json() == {"id": 1, "name": "desk"}
    assert len(calls) == 1


# --- a marker in a setting ----------------------------------------------------


@pytest.mark.parametrize(
    "setting, value",
    [
        ("list_response_schema", list[ResponseSchema]),
        ("list_response_schema", PaginatedEnvelope[ResponseSchema]),
        ("response_schema", Data[ResponseSchema]),
    ],
)
def test_a_setting_that_holds_a_marker_raises(client, Lamp, setting, value):
    LampView = type(
        "LampView",
        (fr.AsyncRestView,),
        {"prefix": "/lamps", "model": Lamp, "schema": LampSchema, setting: value},
    )

    with pytest.raises(RestlyConfigurationError, match=f"{setting}.*SchemaType"):
        fr.include_view(client.app, LampView)


def test_a_function_on_a_setting_can_return_a_marker(client, Lamp):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        list_response_schema = SchemaType(lambda view: list[ResponseSchema])

    assert LampView.list_response_schema == list[LampView.response_schema]


# --- type aliases -------------------------------------------------------------


def test_a_type_alias_of_a_marker(client, Lamp):
    Resp = TypeAliasType("Resp", ResponseSchema)
    Key = TypeAliasType("Key", IDType)
    calls = []

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

        def to_single_response(self, obj):
            calls.append(obj)
            return super().to_single_response(obj)

        @fr.get("/{id}/aliased")
        async def aliased(self, id: Key) -> Resp:
            return await self.handle_get_one(id)

    fr.include_view(client.app, LampView)
    create_tables()
    client.post("/lamps", json={"name": "desk"})
    calls.clear()

    spec = client.get("/openapi.json").json()
    operation = spec["paths"]["/lamps/{id}/aliased"]["get"]
    assert _response_ref(operation) == "LampResponse"
    assert operation["parameters"][0]["schema"]["type"] == "integer"
    assert client.get("/lamps/1/aliased").json() == {"id": 1, "name": "desk"}
    assert len(calls) == 1
    client.get("/lamps/abc/aliased", assert_status_code=404)


def test_a_recursive_type_alias_is_left_alone(client, Lamp):
    Tree = TypeAliasType("Tree", "list[Tree] | str")

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

        @fr.get("/{id}/tree", response_model=None)
        async def tree(self, id: IDType) -> Tree:
            return "leaf"

    fr.include_view(client.app, LampView)
    create_tables()
    assert client.get("/lamps/1/tree").json() == "leaf"


# --- responses= ---------------------------------------------------------------


def _model_of(operation, status):
    return operation["responses"][status]["content"]["application/json"]["schema"]


@pytest.mark.parametrize(
    "model, name",
    [(ResponseSchema, "LampResponse"), (Data[ResponseSchema], "Data_LampResponse_")],
    ids=["bare", "generic"],
)
def test_a_marker_in_responses_of_a_route(client, Lamp, model, name):
    class ApiView(fr.AsyncRestView):
        @fr.post("/{id}/claim", responses={409: {"model": model, "description": "x"}})
        async def claim(self, id: IDType) -> ResponseSchema:
            return await self.handle_get_one(id)

    class LampView(ApiView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

    fr.include_view(client.app, LampView)

    spec = client.get("/openapi.json").json()
    operation = spec["paths"]["/lamps/{id}/claim"]["post"]
    assert _ref(_model_of(operation, "409")) == name
    assert operation["responses"]["409"]["description"] == "x"
    assert spec["components"]["schemas"]["LampResponse"]["properties"]


def test_a_marker_in_responses_of_a_view(client, Lamp):
    class ApiView(fr.AsyncRestView):
        responses = {409: {"model": ResponseSchema}}

    class LampView(ApiView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

    fr.include_view(client.app, LampView)

    operation = client.get("/openapi.json").json()["paths"]["/lamps"]["get"]
    assert _ref(_model_of(operation, "409")) == "LampResponse"


def test_a_marker_in_responses_of_a_plain_view_raises(client):
    class PlainView(fr.View):
        prefix = "/plain"

        @fr.get("/", responses={409: {"model": ResponseSchema}})
        def index(self):
            return {}

    with pytest.raises(RestlyConfigurationError, match="ResponseSchema"):
        fr.include_view(client.app, PlainView)


def test_a_marker_can_be_copied():
    assert copy.copy(ResponseSchema) == ResponseSchema
    marker = get_args(ResponseSchema)[1]
    assert copy.deepcopy(marker) is marker


# --- a marker in a field ------------------------------------------------------


@dataclasses.dataclass
class InADataclass:
    item: ResponseSchema


class InATypedDict(TypedDict):
    item: ResponseSchema


class InANamedTuple(NamedTuple):
    item: ResponseSchema


@pytest.mark.parametrize("wrapper", [InADataclass, InATypedDict, InANamedTuple])
def test_a_marker_in_a_field_of_another_kind_of_class_raises(client, Lamp, wrapper):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

        @fr.get("/{id}/wrapped")
        async def wrapped(self, id: IDType) -> wrapper:
            return None

    with pytest.raises(RestlyConfigurationError, match="field item"):
        fr.include_view(client.app, LampView)


# --- a function on a subclass counts as new -----------------------------------


class _OtherSchema(fr.IDSchema):
    name: str
    note: str = "n"


@pytest.mark.parametrize("form", ["schema", "decorator"])
def test_a_new_schema_as_a_function_replaces_the_declared_classes(Lamp, form):
    class Create(LampSchema):
        pass

    class Base(fr.AsyncRestView):
        model = Lamp
        schema = LampSchema
        create_schema = Create

    if form == "schema":

        class Sub(Base):
            schema = SchemaType(lambda view: _OtherSchema)

    else:

        class Sub(Base):
            @SchemaType
            def schema(view):
                return _OtherSchema

    assert Base.create_schema is Create
    assert Sub.create_schema is not Create
    assert "note" in Sub.create_schema.model_fields
    assert "note" in Sub.response_schema.model_fields


def test_a_new_response_schema_as_a_function_replaces_the_declared_list_classes(Lamp):
    class OnlyTheId(fr.IDSchema):
        pass

    class Base(fr.AsyncRestView):
        model = Lamp
        schema = LampSchema
        list_params_schema = fr.query.derive_list_params_schema(LampSchema, Lamp)

    class Sub(Base):
        response_schema = SchemaType(lambda view: OnlyTheId)

    assert "name" in Base.list_params_schema.model_fields
    assert "name" not in Sub.list_params_schema.model_fields


def test_a_function_on_the_same_class_keeps_a_declared_class(Lamp):
    class Create(LampSchema):
        pass

    class Base(fr.AsyncRestView):
        model = Lamp
        schema = SchemaType(lambda view: LampSchema)
        create_schema = Create

    class Sub(Base):
        pass

    assert Base.create_schema is Create
    assert Sub.create_schema is Create


# --- what the response step takes ---------------------------------------------


def test_a_decorator_without_async_around_an_async_endpoint(client, Lamp):
    def logged(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

        def to_single_response(self, obj):
            response = super().to_single_response(obj)
            response.name = response.name.upper()
            return response

        @fr.get("/{id}")
        @logged
        async def get_one_endpoint(self, id: IDType) -> ResponseSchema:
            return await self.handle_get_one(id)

        @fr.get("/")
        @logged
        async def get_many_endpoint(self, list_params) -> ListResponseSchema:
            return await self.handle_get_many(list_params)

    fr.include_view(client.app, LampView)
    create_tables()
    client.post("/lamps", json={"name": "desk"})

    assert client.get("/lamps/1").json()["name"] == "DESK"
    assert client.get("/lamps").json()["data"][0]["name"] == "DESK"


def test_a_row_of_another_model_uses_the_response_step(client, Lamp):
    class Shade(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

        def to_single_response(self, obj):
            response = super().to_single_response(obj)
            response.name = response.name.upper()
            return response

        async def create(self, data):
            return Shade(name=data.name)

    fr.include_view(client.app, LampView)
    create_tables()

    assert client.post("/lamps", json={"name": "desk"}).json()["name"] == "DESK"


def test_a_list_of_rows_uses_the_response_class_of_an_override(client, Lamp):
    class Aliased(fr.IDSchema):
        name: str = pydantic.Field(alias="lampName")

    class Wide(fr.IDSchema):
        model_config = pydantic.ConfigDict(from_attributes=True)
        name: str

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        response_schema = Aliased
        schema = Aliased

        def to_single_response(self, obj):
            return Wide(id=obj.id, name=obj.name)

        @fr.get("/{id}/one")
        async def one(self, id: IDType) -> ResponseSchema:
            return await self.handle_get_one(id)

        @fr.get("/{id}/many")
        async def many(self, id: IDType) -> list[ResponseSchema]:
            return [await self.handle_get_one(id)]

    fr.include_view(client.app, LampView)
    create_tables()
    client.post("/lamps", json={"lampName": "desk"})

    assert client.get("/lamps/1/one").json() == {"id": 1, "lampName": "desk"}
    assert client.get("/lamps/1/many").json() == [{"id": 1, "lampName": "desk"}]


# --- first reads --------------------------------------------------------------


def test_two_threads_that_read_a_new_view_share_one_result(Lamp):
    fn_calls = []

    def build(view):
        fn_calls.append(view)
        return pydantic.create_model("Built", name=(str, ...))

    original = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for _ in range(300):

            class V(fr.AsyncRestView):
                model = Lamp
                schema = LampSchema
                built = SchemaType(build)

            barrier = threading.Barrier(4)
            seen = []

            def read():
                barrier.wait()
                seen.append(V.built)

            threads = [threading.Thread(target=read) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            assert len({id(result) for result in seen}) == 1
    finally:
        sys.setswitchinterval(original)


# --- two derived parents ------------------------------------------------------


def test_a_class_with_two_derived_parents_keeps_out_what_each_dropped(Lamp):
    class One(fr.IDSchema):
        name: str
        pin: fr.WriteOnly[str]

    class Two(fr.IDSchema):
        name: str
        salary: fr.ReadOnly[int] = 0

    class Both(
        fr.schemas.derive_response_schema(One),
        fr.schemas.derive_create_schema(Two, Lamp),
    ):
        pass

    assert "pin" not in Both.model_fields
    assert "salary" not in Both.model_fields
