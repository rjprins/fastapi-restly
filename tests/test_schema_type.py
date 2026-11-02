"""``SchemaType``: a type that depends on the view.

A setting that takes a schema class also takes a function of the view, and a
route takes one in an annotation. Restly calls the function once for each view,
when something first reads the type, and not when Python creates the class. So a
view can be read before it registers, and setups that need the class definition
to be over keep working.
"""

import threading
from typing import Annotated, Any, Generic, Optional, TypeVar

import pydantic
import pytest
import sqlalchemy
from fastapi import FastAPI
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError
from fastapi_restly.testing._client import RestlyTestClient
from fastapi_restly.views import SchemaType, ViewID, ViewResponse

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
        schema_response = SchemaType(lambda view: _with_note(view.schema))

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
        schema_create = SchemaType(
            lambda view: pydantic.create_model(
                "LampIn", name=(str, ...), __base__=pydantic.BaseModel
            )
        )
        schema_update = SchemaType(
            lambda view: fr.schemas.derive_schema_update(view.schema_create)
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
        schema_list_params = SchemaType(lambda view: LampParams)

    fr.include_view(client.app, LampView)
    assert LampView.schema_list_params is LampParams


def test_the_checks_of_list_params_apply_to_what_a_function_returns(client, Lamp):
    class NoPages(pydantic.BaseModel):
        name: str | None = None

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        schema_list_params = SchemaType(lambda view: NoPages)

    with pytest.raises(RestlyConfigurationError, match="no 'page' and no 'page_size'"):
        fr.include_view(client.app, LampView)


def test_a_setting_as_a_decorator(client, Lamp):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

        @SchemaType
        def schema_response(view):
            return _with_note(view.schema)

    fr.include_view(client.app, LampView)
    create_tables()
    assert client.post("/lamps/", json={"name": "a"}).json()["note"] == "noted"


def test_an_instance_reads_the_same_class(Lamp):
    class LampView(fr.AsyncRestView):
        model = Lamp
        schema = LampSchema
        schema_response = SchemaType(lambda view: _with_note(view.schema))

    assert LampView().schema_response is LampView.schema_response


# --- the route form ----------------------------------------------------------


def test_a_function_in_a_route_gets_the_view(client, Lamp):
    class Bulb(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class BulbSchema(fr.IDSchema):
        name: str

    Single = Annotated[Any, SchemaType(lambda view: Data[view.schema_response])]

    class ApiView(fr.AsyncRestView):
        @fr.get("/{id}/wrapped")
        async def wrapped(self, id: ViewID) -> Single:
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
    Names = Annotated[Any, SchemaType(lambda view: list[view.schema_response])]

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
    Own = Annotated[Any, SchemaType(lambda view: view.schema_response)]

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

        @fr.get("/{id}/own")
        async def own(self, id: ViewID) -> Own:
            return await self.handle_get_one(id)

    fr.include_view(client.app, LampView)
    create_tables()
    created = client.post("/lamps/", json={"name": "a"}).json()
    assert client.get(f"/lamps/{created['id']}/own").json() == created


def test_a_function_on_a_plain_view_raises():
    Own = Annotated[Any, SchemaType(lambda view: view.schema_response)]

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
        def schema_response(view):
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
        schema_response = SchemaType(noted)

    class LampView(ApiView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

    class SubView(LampView):
        prefix = "/sub-lamps"

    first = LampView.schema_response
    assert LampView.schema_response is first
    fr.include_view(client.app, LampView)
    fr.include_view(client.app, SubView)
    assert LampView.schema_response is first
    assert SubView.schema_response is not first
    assert calls == ["LampView", "SubView"]


def test_two_objects_around_one_function_give_one_class(client, Lamp):
    def noted(view):
        return _with_note(view.schema)

    Noted = Annotated[Any, SchemaType(noted)]

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        schema_response = SchemaType(noted)

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
        def schema_update(view):
            return fr.schemas.derive_schema_update(view.schema_create)

        @SchemaType
        def schema_create(view):
            return _with_note(view.schema_response)

    assert issubclass(LampView.schema_update, LampView.schema_create)
    assert issubclass(LampView.schema_create, LampView.schema_response)
    assert issubclass(LampView.schema_response, LampView.schema)


def test_a_function_that_reads_itself_raises(Lamp):
    class LampView(fr.AsyncRestView):
        model = Lamp

        @SchemaType
        def schema_response(view):
            return view.schema_create

        @SchemaType
        def schema_create(view):
            return view.schema_response

    with pytest.raises(RestlyConfigurationError, match="reads its own result"):
        LampView.schema_response
    # the failed read leaves nothing behind
    with pytest.raises(RestlyConfigurationError, match="reads its own result"):
        LampView.schema_create


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
        schema_response = SchemaType(built)

    results, errors = [], []

    def read():
        try:
            results.append(LampView.schema_response)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=read) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert results[0] is results[1]
    assert LampView.schema_response is results[0]


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
            "schema_response",
            "schema_create",
            "schema_update",
            "schema_list_params",
        )
    }
    assert sorted(before["schema_create"].model_fields) == ["name"]

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

    LampView.schema_create
    LampView.schema_response
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

    assert "other" in SubView.schema_response.model_fields
    assert "other" in SubView.schema_create.model_fields
    assert "other" not in LampView.schema_response.model_fields


def test_a_subclass_with_a_new_schema_gets_the_default_back(Lamp):
    class Own(pydantic.BaseModel):
        name: str

    class OtherSchema(fr.IDSchema):
        name: str
        other: str = "x"

    class LampView(fr.AsyncRestView):
        model = Lamp
        schema = LampSchema
        schema_create = Own

    class SubView(LampView):
        schema = OtherSchema

    class SameView(LampView):
        pass

    assert SameView.schema_create is Own
    assert "other" in SubView.schema_create.model_fields


def test_a_subclass_with_a_new_schema_keeps_a_function(Lamp):
    class OtherSchema(fr.IDSchema):
        name: str

    class LampView(fr.AsyncRestView):
        model = Lamp
        schema = LampSchema
        schema_response = SchemaType(lambda view: _with_note(view.schema))

    class SubView(LampView):
        schema = OtherSchema

    assert issubclass(SubView.schema_response, OtherSchema)
    assert issubclass(LampView.schema_response, LampSchema)


def test_a_class_in_a_subclass_wins_over_a_function(Lamp):
    class Own(pydantic.BaseModel):
        name: str

    class ApiView(fr.AsyncRestView):
        schema_response = SchemaType(lambda view: _with_note(view.schema))

    class LampView(ApiView):
        model = Lamp
        schema = LampSchema
        schema_response = Own

    assert LampView.schema_response is Own


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
    assert "name" in WriterView.schema_response.model_fields


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
        schema_response = LampSchema

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
        def schema_response(view):
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
    assert LampView.schema_response.__name__ == "LampSchemaKeyed"
    assert not hasattr(BaseLampView, "schema_response")


def test_a_view_without_a_model_has_no_generated_classes():
    class ApiView(fr.AsyncRestView):
        pass

    assert not hasattr(ApiView, "schema")
    assert not hasattr(ApiView, "schema_response")
    assert not hasattr(ApiView, "schema_create")


# --- wrong values ------------------------------------------------------------


class NotAModel:
    pass


@pytest.mark.parametrize(
    "value",
    ["oops", NotAModel, sqlalchemy.types.SchemaType(), None],
    ids=["text", "plain-class", "sqlalchemy-schematype", "none"],
)
@pytest.mark.parametrize(
    "setting", ["schema_response", "schema_create", "schema_update", "schema"]
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


@pytest.mark.parametrize(
    "result", [None, list[LampSchema], NotAModel], ids=["none", "list", "plain-class"]
)
def test_a_function_that_gives_something_else_than_a_class_raises(Lamp, result):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        schema_response = SchemaType(lambda view: result)

    with pytest.raises(
        RestlyConfigurationError,
        match=r"SchemaType function of LampView\.schema_response must return",
    ):
        fr.include_view(FastAPI(), LampView)


def test_a_view_without_a_model_raises_at_registration():
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"

    with pytest.raises(ValueError, match=r"'LampView\.model' must be specified"):
        fr.include_view(FastAPI(), LampView)


# --- a marker in a field of a class ------------------------------------------


class _Wrapped(pydantic.BaseModel):
    thing: ViewResponse


class _WrappedList(pydantic.BaseModel):
    things: list[ViewResponse]


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


def test_derive_create_and_update_leave_out_the_read_only_fields():
    class Schema(fr.BaseSchema):
        id: fr.ReadOnly[int]
        name: str

    create = fr.schemas.derive_schema_create(Schema)
    update = fr.schemas.derive_schema_update(Schema)
    assert list(create.model_fields) == ["name"]
    assert list(update.model_fields) == ["name"]
    assert update().model_dump() == {"name": None}
    assert create.__name__ == "SchemaCreate"
    assert update.__name__ == "SchemaUpdate"


def test_derive_create_and_update_with_a_model_leave_out_a_generated_key(Lamp):
    class Plain(fr.BaseSchema):
        id: int
        name: str

    assert list(fr.schemas.derive_schema_create(Plain).model_fields) == ["id", "name"]
    assert list(fr.schemas.derive_schema_create(Plain, Lamp).model_fields) == ["name"]
    assert list(fr.schemas.derive_schema_update(Plain, Lamp).model_fields) == ["name"]


def test_derive_gives_the_class_of_the_view(client, Lamp):
    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema

    assert fr.schemas.derive_schema_create(LampSchema, Lamp) is LampView.schema_create
    assert fr.schemas.derive_schema_update(LampSchema, Lamp) is LampView.schema_update


def test_derive_update_from_a_class_of_your_own(client, Lamp):
    class LampIn(fr.BaseSchema):
        name: str

    class LampView(fr.AsyncRestView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        schema_create = LampIn
        schema_update = fr.schemas.derive_schema_update(LampIn)

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

    assert SubView.schema_list_params is fr.views.ReactAdminListParams


def test_a_react_admin_view_takes_a_function_for_its_list_params(client, Lamp):
    class LampParams(fr.views.ReactAdminListParams):
        pass

    class LampView(fr.views.AsyncReactAdminView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        schema_list_params = SchemaType(lambda view: LampParams)

    fr.include_view(client.app, LampView)
    assert LampView.schema_list_params is LampParams


def test_a_react_admin_view_checks_what_a_function_gives_at_registration(Lamp):
    class Wrong(pydantic.BaseModel):
        pass

    class LampView(fr.views.AsyncReactAdminView):
        prefix = "/lamps"
        model = Lamp
        schema = LampSchema
        schema_list_params = SchemaType(lambda view: Wrong)

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
        schema_response = SchemaType(lambda view: _with_note(view.schema))

    app = FastAPI()
    fr.include_view(app, LampView)
    SharedBase.metadata.create_all(engine)
    client = RestlyTestClient(app)
    assert client.post("/lamps/", json={"name": "a"}).json()["note"] == "noted"
