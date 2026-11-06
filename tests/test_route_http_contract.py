"""What a route documents and answers when a view changes one HTTP detail."""

from collections.abc import Callable, Iterator
from typing import Annotated, Any

import pydantic
import pytest
from fastapi import Depends, FastAPI, Path
from fastapi.testclient import TestClient
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly._routes import iter_routes
from fastapi_restly.exc import RestlyConfigurationError
from fastapi_restly.testing import RestlyTestClient
from fastapi_restly.views import CreateSchema, IDType, ResponseSchema

from ._models import SharedBase
from .conftest import create_tables


class GadgetSchema(fr.IDSchema):
    name: str


@pytest.fixture
def Owner() -> type:
    class Owner(fr.IDMixin, SharedBase):
        name: Mapped[str]

    return Owner


@pytest.fixture
def Gadget(Owner: type) -> type:
    class Gadget(fr.IDMixin, SharedBase):
        name: Mapped[str]
        owner_id: Mapped[int | None] = mapped_column(ForeignKey(Owner.id), default=None)

    return Gadget


@pytest.fixture(params=[fr.RestView, fr.AsyncRestView], ids=["sync", "async"])
def view_base(request: pytest.FixtureRequest) -> Iterator[type]:
    if request.param is fr.RestView:
        request.getfixturevalue("sync_db")
    yield request.param


@pytest.fixture
def make_client(
    request: pytest.FixtureRequest, view_base: type, Gadget: type
) -> Callable[..., RestlyTestClient]:
    def make(*bases: type, **attrs: Any) -> RestlyTestClient:
        view = type(
            "GadgetView",
            (*bases, view_base),
            {"prefix": "/gadgets", "model": Gadget, "schema": GadgetSchema, **attrs},
        )
        app = FastAPI()
        fr.include_view(app, view)
        if view_base is fr.RestView:
            engine, _ = request.getfixturevalue("sync_db")
            SharedBase.metadata.create_all(engine)
        else:
            create_tables()
        return RestlyTestClient(app)

    return make


def _statuses(view: type) -> dict[tuple[str, str], set[str]]:
    app = FastAPI()
    fr.include_view(app, view)
    return {
        (path, method): set(operation["responses"])
        for path, methods in app.openapi()["paths"].items()
        for method, operation in methods.items()
    }


def _with_404(view: type) -> set[tuple[str, str]]:
    return {route for route, codes in _statuses(view).items() if "404" in codes}


def test_a_route_documents_404_where_a_row_can_be_missing(Gadget):
    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

        @fr.get("/stats")
        def stats(self) -> dict[str, int]:
            return {}

        @fr.post("/{id}/archive")
        def archive(self, id: IDType) -> dict[str, int]:
            return {}

    assert _with_404(GadgetView) == {
        ("/gadgets/{id}", "get"),
        ("/gadgets/{id}", "patch"),
        ("/gadgets/{id}", "delete"),
        ("/gadgets/{id}/archive", "post"),
    }


def test_a_parameter_in_the_prefix_documents_404_on_every_route(Gadget):
    class GadgetView(fr.RestView):
        prefix = "/owners/{owner_id}/gadgets"
        model = Gadget
        schema = GadgetSchema

    assert _with_404(GadgetView) == set(_statuses(GadgetView))


class _Tree(pydantic.BaseModel):
    name: str
    children: list["_Tree"] = []


def _line_of(model: type) -> type:
    class Line(pydantic.BaseModel):
        owner: fr.IDRef[model]  # type: ignore[valid-type]

    return Line


@pytest.mark.parametrize(
    "field_type",
    [
        lambda owner: fr.IDRef[owner],
        lambda owner: fr.IDRef[owner] | None,
        lambda owner: list[fr.IDRef[owner]],
        lambda owner: fr.IDSchema[owner],
        lambda owner: fr.MustExist[int, owner],
        lambda owner: fr.MustExist[int, owner] | None,
        lambda owner: list[fr.MustExist[int, owner]],
        _line_of,
        lambda owner: list[_line_of(owner)],
        lambda owner: dict[str, _line_of(owner)],
    ],
    ids=[
        "ref",
        "optional-ref",
        "ref-list",
        "id-schema",
        "must-exist",
        "optional-must-exist",
        "must-exist-list",
        "nested",
        "nested-list",
        "nested-dict",
    ],
)
def test_create_documents_404_when_its_body_holds_a_reference(
    field_type, Owner, Gadget
):
    class GadgetCreate(pydantic.BaseModel):
        name: str
        owner: field_type(Owner) = None  # type: ignore[valid-type]

    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        create_schema = GadgetCreate

    assert ("/gadgets", "post") in _with_404(GadgetView)
    assert ("/gadgets", "get") not in _with_404(GadgetView)


def test_a_schema_that_inherits_from_a_bound_id_schema_is_no_reference(Gadget):
    class BoundSchema(fr.IDSchema[Gadget]):
        name: str

    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = BoundSchema

    assert ("/gadgets", "post") not in _with_404(GadgetView)


def test_create_without_a_reference_documents_no_404_also_with_a_recursive_body(Gadget):
    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        create_schema = _Tree

    assert ("/gadgets", "post") not in _with_404(GadgetView)


def test_a_custom_route_with_a_reference_in_its_body_documents_404(Owner, Gadget):
    class Move(pydantic.BaseModel):
        owner_id: fr.MustExist[int, Owner]

    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

        @fr.post("/move")
        def move(self, data: Move) -> dict[str, int]:
            return {}

    assert ("/gadgets/move", "post") in _with_404(GadgetView)


def test_the_documented_404_is_what_create_answers(make_client, Owner):
    class GadgetCreate(fr.BaseSchema):
        name: str
        owner_id: fr.MustExist[int, Owner]

    client = make_client(create_schema=GadgetCreate)

    spec = client.get("/openapi.json").json()
    assert "404" in spec["paths"]["/gadgets"]["post"]["responses"]
    response = client.post(
        "/gadgets", json={"name": "a", "owner_id": 999}, assert_status_code=404
    )
    assert response.status_code == 404


def test_a_plain_view_documents_no_404_by_itself():
    class StatusView(fr.View):
        prefix = "/status"

        @fr.get("/{name}")
        def get_status(self, name: str) -> dict[str, str]:
            return {}

    assert _with_404(StatusView) == set()


def test_a_status_set_to_none_on_a_view_is_left_out(Gadget):
    class Quiet(fr.RestView):
        responses = {404: None, 402: {"description": "Payment required"}}

    class GadgetView(Quiet):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

    statuses = _statuses(GadgetView)
    assert _with_404(GadgetView) == set()
    assert all("402" in codes for codes in statuses.values())


def test_a_subclass_puts_back_what_a_base_left_out(Gadget):
    class Quiet(fr.RestView):
        responses = {404: None}

    class GadgetView(Quiet):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        responses = {404: {"description": "No such gadget"}}

    # What a view writes in `responses` is on every route.
    assert _with_404(GadgetView) == set(_statuses(GadgetView))


@pytest.mark.parametrize("view_base", [fr.RestView, fr.View], ids=["crud", "plain"])
def test_a_status_set_to_none_on_a_route_is_left_out(view_base, Gadget):
    class Gadgets(view_base):
        prefix = "/gadgets"
        responses = {402: {"description": "Payment required"}}

        @fr.get("/{id}/paid")
        def paid(self, id: int) -> dict[str, int]:
            return {}

        @fr.get("/{id}/free", responses={402: None, 404: None})
        def free(self, id: int) -> dict[str, int]:
            return {}

    if view_base is fr.RestView:
        Gadgets.model = Gadget
        Gadgets.schema = GadgetSchema
    statuses = _statuses(Gadgets)
    assert "402" in statuses["/gadgets/{id}/paid", "get"]
    assert "402" not in statuses["/gadgets/{id}/free", "get"]
    assert "404" not in statuses["/gadgets/{id}/free", "get"]


def test_a_route_response_can_hold_a_marker_next_to_a_removed_status(Gadget):
    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        responses = {409: {"model": ResponseSchema}, 404: None}

    app = FastAPI()
    fr.include_view(app, GadgetView)
    operation = app.openapi()["paths"]["/gadgets/{id}"]["get"]
    assert "404" not in operation["responses"]
    assert "application/json" in operation["responses"]["409"]["content"]


def test_the_default_delete_answers_204_with_no_body(make_client):
    client = make_client()
    created = client.post("/gadgets", json={"name": "a"}).json()

    response = client.delete(f"/gadgets/{created['id']}")

    assert response.status_code == 204
    assert response.content == b""


def test_delete_answers_the_status_of_its_route(make_client, view_base):
    class Answers200(view_base):
        if issubclass(view_base, fr.AsyncRestView):

            @fr.delete("/{id}", status_code=200)
            async def delete_endpoint(self, id: IDType) -> None:
                return await super().delete_endpoint(id)

        else:

            @fr.delete("/{id}", status_code=200)
            def delete_endpoint(self, id: IDType) -> None:
                return super().delete_endpoint(id)

    client = make_client(Answers200)
    created = client.post("/gadgets", json={"name": "a"}).json()

    spec = client.get("/openapi.json").json()
    assert "200" in spec["paths"]["/gadgets/{id}"]["delete"]["responses"]
    response = client.delete(f"/gadgets/{created['id']}")
    assert response.status_code == 200
    client.get(f"/gadgets/{created['id']}", assert_status_code=404)


def _recorder(log: list[str], name: str) -> Any:
    def record() -> None:
        log.append(name)

    return Depends(record)


def test_modify_route_changes_one_argument_and_keeps_the_rest(make_client):
    client = make_client(create_endpoint=fr.modify_route(status_code=200))

    operation = client.get("/openapi.json").json()["paths"]["/gadgets"]["post"]
    assert set(operation["responses"]) == {"200", "422"}
    assert operation["operationId"] == "gadgets_create"
    assert operation["summary"] == "Create"
    assert "description" not in operation
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    assert body["$ref"].endswith("/GadgetCreate")

    response = client.post("/gadgets", json={"name": "a"})
    assert response.status_code == 200
    assert response.json()["name"] == "a"


def test_modify_route_with_a_status_without_a_body_drops_the_response_model(
    make_client,
):
    client = make_client(update_endpoint=fr.modify_route(status_code=204))
    created = client.post("/gadgets", json={"name": "a"}).json()

    responses = client.get("/openapi.json").json()["paths"]["/gadgets/{id}"]["patch"][
        "responses"
    ]
    assert "content" not in responses["204"]
    response = client.patch(f"/gadgets/{created['id']}", json={"name": "b"})
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/gadgets/{created['id']}").json()["name"] == "b"


def test_modify_route_with_a_status_with_a_body_brings_the_response_model_back(
    make_client, view_base
):
    class NoBody(view_base):
        update_endpoint = fr.modify_route(status_code=204)

    client = make_client(NoBody, update_endpoint=fr.modify_route(status_code=200))
    created = client.post("/gadgets", json={"name": "a"}).json()

    responses = client.get("/openapi.json").json()["paths"]["/gadgets/{id}"]["patch"][
        "responses"
    ]
    assert "$ref" in responses["200"]["content"]["application/json"]["schema"]
    response = client.patch(f"/gadgets/{created['id']}", json={"name": "b"})
    assert response.status_code == 200
    assert response.json()["name"] == "b"


def test_modify_route_keeps_a_status_without_a_body_down_the_classes(
    make_client, view_base
):
    class NoBody(view_base):
        update_endpoint = fr.modify_route(status_code=204)

    class Plain(NoBody):
        pass

    client = make_client(Plain, update_endpoint=fr.modify_route(summary="Change it"))

    operation = client.get("/openapi.json").json()["paths"]["/gadgets/{id}"]["patch"]
    assert operation["summary"] == "Change it"
    assert "content" not in operation["responses"]["204"]


def test_modify_route_changes_the_method(make_client):
    client = make_client(update_endpoint=fr.modify_route(methods=["PUT"]))
    created = client.post("/gadgets", json={"name": "a"}).json()

    path = f"/gadgets/{created['id']}"
    assert client.put(path, json={"name": "b"}).json()["name"] == "b"
    client.patch(path, json={"name": "c"}, assert_status_code=405)


def test_modify_route_on_delete_answers_the_new_status(make_client):
    client = make_client(delete_endpoint=fr.modify_route(status_code=200))
    created = client.post("/gadgets", json={"name": "a"}).json()

    spec = client.get("/openapi.json").json()
    assert "200" in spec["paths"]["/gadgets/{id}"]["delete"]["responses"]
    assert client.delete(f"/gadgets/{created['id']}").status_code == 200


def test_modify_route_keeps_the_hooks_of_the_view(make_client, view_base):
    class Upper(view_base):
        if issubclass(view_base, fr.AsyncRestView):

            async def create(self, schema_obj: Any) -> Any:
                obj = await super().create(schema_obj)
                obj.name = obj.name.upper()
                return obj

        else:

            def create(self, schema_obj: Any) -> Any:
                obj = super().create(schema_obj)
                obj.name = obj.name.upper()
                return obj

    client = make_client(Upper, create_endpoint=fr.modify_route(status_code=200))

    assert client.post("/gadgets", json={"name": "a"}).json()["name"] == "A"


def test_modify_route_adds_dependencies_after_those_of_the_base(make_client, view_base):
    log: list[str] = []

    class Base(view_base):
        create_endpoint = fr.modify_route(dependencies=[_recorder(log, "base")])

    client = make_client(
        Base,
        dependencies=[_recorder(log, "view")],
        create_endpoint=fr.modify_route(dependencies=[_recorder(log, "leaf")]),
    )
    client.post("/gadgets", json={"name": "a"})

    assert log == ["view", "base", "leaf"]


def test_modify_route_that_lists_a_base_dependency_again_runs_it_there(
    make_client, view_base
):
    log: list[str] = []
    guard = _recorder(log, "guard")

    class Base(view_base):
        create_endpoint = fr.modify_route(dependencies=[guard])

    client = make_client(
        Base,
        create_endpoint=fr.modify_route(dependencies=[_recorder(log, "first"), guard]),
    )
    client.post("/gadgets", json={"name": "a"})

    assert log == ["first", "guard"]


def test_modify_route_adds_responses_per_status_code(Gadget):
    class Base(fr.RestView):
        create_endpoint = fr.modify_route(
            responses={409: {"description": "Taken"}, 418: {"description": "Teapot"}}
        )

    class GadgetView(Base):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        create_endpoint = fr.modify_route(
            responses={402: {"description": "Pay"}, 418: None}
        )
        get_one_endpoint = fr.modify_route(responses={404: None})

    statuses = _statuses(GadgetView)
    assert statuses["/gadgets", "post"] == {"201", "402", "409", "422"}
    assert "404" not in statuses["/gadgets/{id}", "get"]
    assert "404" in statuses["/gadgets/{id}", "patch"]


@pytest.mark.parametrize(
    "options, key, value",
    [
        ({"summary": "Add a gadget"}, "summary", "Add a gadget"),
        ({"description": "Adds one."}, "description", "Adds one."),
        ({"operation_id": "add_gadget"}, "operationId", "add_gadget"),
        ({"tags": ["shop"]}, "tags", ["Gadgets", "shop"]),
        ({"deprecated": True}, "deprecated", True),
        ({"openapi_extra": {"x-owner": "me"}}, "x-owner", "me"),
    ],
    ids=["summary", "description", "operation_id", "tags", "deprecated", "extra"],
)
def test_modify_route_passes_an_argument_to_fastapi(options, key, value, Gadget):
    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        create_endpoint = fr.modify_route(**options)

    app = FastAPI()
    fr.include_view(app, GadgetView)

    assert app.openapi()["paths"]["/gadgets"]["post"][key] == value


def test_modify_route_with_a_name_gives_fastapi_the_id_and_the_summary(Gadget):
    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        create_endpoint = fr.modify_route(name="create_gadget")

    app = FastAPI()
    fr.include_view(app, GadgetView)

    operation = app.openapi()["paths"]["/gadgets"]["post"]
    assert operation["operationId"] == "create_gadget_gadgets_post"
    assert operation["summary"] == "Create Gadget"
    assert str(app.url_path_for("create_gadget")) == "/gadgets"


def test_modify_route_hides_a_route_from_the_spec_only(make_client):
    client = make_client(create_endpoint=fr.modify_route(include_in_schema=False))

    assert "post" not in client.get("/openapi.json").json()["paths"]["/gadgets"]
    assert client.post("/gadgets", json={"name": "a"}).status_code == 201


def test_modify_route_changes_the_path(make_client):
    client = make_client(
        create_endpoint=fr.modify_route("/new"),
        get_one_endpoint=fr.modify_route(path="/{id}/detail"),
    )

    created = client.post("/gadgets/new", json={"name": "a"}).json()
    client.post("/gadgets", json={"name": "b"}, assert_status_code=405)
    assert client.get(f"/gadgets/{created['id']}/detail").json()["name"] == "a"
    # the id keeps the converter of the view
    client.get("/gadgets/abc/detail", assert_status_code=404)


def test_modify_route_with_an_empty_path_leaves_out_the_trailing_slash_route(Gadget):
    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        create_endpoint = fr.modify_route(path="")

    app = FastAPI()
    fr.include_view(app, GadgetView)

    posts = [
        route.path
        for route in iter_routes(app.routes)
        if "POST" in getattr(route, "methods", ())
    ]
    assert posts == ["/gadgets"]


def test_modify_route_keeps_the_list_params(make_client):
    client = make_client(get_many_endpoint=fr.modify_route(summary="All gadgets"))
    client.post("/gadgets", json={"name": "a"})
    client.post("/gadgets", json={"name": "b"})

    response = client.get("/gadgets", params={"page_size": 1})
    assert len(response.json()["data"]) == 1
    client.get("/gadgets", params={"nope": 1}, assert_status_code=422)

    operation = client.get("/openapi.json").json()["paths"]["/gadgets"]["get"]
    assert operation["summary"] == "All gadgets"
    assert "page_size" in {parameter["name"] for parameter in operation["parameters"]}


@pytest.mark.parametrize(
    "argument",
    ["response_model", "response_model_exclude", "response_model_exclude_none"],
)
def test_modify_route_refuses_the_response_model_arguments(argument):
    # another response model would skip to_single_response, and the fields
    # that it hides would go out
    with pytest.raises(
        RestlyConfigurationError,
        match=rf"fr.modify_route cannot set {argument}: it changes how a route "
        r"is served, not what it sends. .*set response_schema or "
        r"list_response_schema.*write the route method",
    ):
        fr.modify_route(**{argument: None})


def test_modify_route_leaves_the_base_and_other_views_alone(Gadget):
    class ChangedView(fr.RestView):
        prefix = "/changed"
        model = Gadget
        schema = GadgetSchema
        create_endpoint = fr.modify_route(status_code=200)

    class OtherView(fr.RestView):
        prefix = "/other"
        model = Gadget
        schema = GadgetSchema

    assert "200" in _statuses(ChangedView)["/changed", "post"]
    assert "201" in _statuses(OtherView)["/other", "post"]
    assert fr.RestView.create_endpoint._api_route_args[1]["status_code"] == 201


def test_a_subclass_inherits_modify_route_and_changes_the_route_further(Gadget):
    class Base(fr.RestView):
        model = Gadget
        schema = GadgetSchema
        create_endpoint = fr.modify_route(status_code=200, summary="Base")

    class Child(Base):
        prefix = "/child"

    class Grandchild(Child):
        prefix = "/grand"
        create_endpoint = fr.modify_route(summary="Grandchild")

    app = FastAPI()
    fr.include_view(app, Child)
    operation = app.openapi()["paths"]["/child"]["post"]
    assert operation["summary"] == "Base"
    assert "200" in operation["responses"]

    # also after its parent registered, and with the prefix of the parent
    app = FastAPI()
    fr.include_view(app, Grandchild)
    operation = app.openapi()["paths"]["/child/grand"]["post"]
    assert operation["summary"] == "Grandchild"
    assert "200" in operation["responses"]


def test_a_plain_mixin_holds_modify_route_for_a_view(make_client):
    class Quiet:
        create_endpoint = fr.modify_route(status_code=200, summary="Mixin")

    class Named:
        create_endpoint = fr.modify_route(summary="Front")

    client = make_client(Named, Quiet)

    operation = client.get("/openapi.json").json()["paths"]["/gadgets"]["post"]
    assert operation["summary"] == "Front"
    assert client.post("/gadgets", json={"name": "a"}).status_code == 200


def test_a_written_route_replaces_modify_route_and_reaches_it_with_super(
    make_client, view_base
):
    class Base(view_base):
        create_endpoint = fr.modify_route(status_code=200, summary="Base")

    class Written(Base):
        if issubclass(view_base, fr.AsyncRestView):

            @fr.post("/")
            async def create_endpoint(self, data: CreateSchema) -> ResponseSchema:
                return await super().create_endpoint(data)

        else:

            @fr.post("/")
            def create_endpoint(self, data: CreateSchema) -> ResponseSchema:
                return super().create_endpoint(data)

    client = make_client(Written)

    operation = client.get("/openapi.json").json()["paths"]["/gadgets"]["post"]
    assert operation["summary"] == "Create"
    assert client.post("/gadgets", json={"name": "a"}).status_code == 201


def test_modify_route_on_a_custom_route_of_a_plain_view():
    class Reports(fr.View):
        prefix = "/reports"

        @fr.get("/daily", summary="Daily")
        def daily(self) -> dict[str, int]:
            return {}

    class Quiet(Reports):
        daily = fr.modify_route(deprecated=True)

    app = FastAPI()
    fr.include_view(app, Quiet)

    operation = app.openapi()["paths"]["/reports/daily"]["get"]
    assert operation["summary"] == "Daily"
    assert operation["deprecated"] is True


@pytest.mark.parametrize("name", ["create", "creat_endpoint", "prefix"])
def test_modify_route_that_names_no_route_raises(name):
    with pytest.raises(
        RestlyConfigurationError,
        match=f"BadView.{name} uses fr.modify_route, but the class inherits no",
    ):
        type("BadView", (fr.RestView,), {name: fr.modify_route(status_code=200)})


def test_modify_route_over_a_route_that_a_base_removed_raises():
    class NoCreate(fr.RestView):
        create_endpoint = None

    with pytest.raises(
        RestlyConfigurationError,
        match="NoCreate sets create_endpoint to None, so there is no route to change",
    ):

        class BadView(NoCreate):
            create_endpoint = fr.modify_route(status_code=200)


def test_modify_route_with_an_unknown_argument_fails_as_a_decorator_does(Gadget):
    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        create_endpoint = fr.modify_route(stauts_code=200)

    with pytest.raises(TypeError, match="stauts_code"):
        fr.include_view(FastAPI(), GadgetView)


def test_modify_route_set_after_the_class_body_raises(Gadget):
    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

    GadgetView.create_endpoint = fr.modify_route(status_code=200)

    with pytest.raises(RestlyConfigurationError, match="Use it in the class body"):
        fr.include_view(FastAPI(), GadgetView)


def test_modify_route_set_on_a_base_after_its_class_body_raises(Gadget):
    class Base(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

    class GadgetView(Base):
        pass

    Base.create_endpoint = fr.modify_route(status_code=200)

    with pytest.raises(
        RestlyConfigurationError, match="Base.create_endpoint got fr.modify_route after"
    ):
        fr.include_view(FastAPI(), GadgetView)


def test_modify_route_on_a_mixin_after_the_view_class_raises():
    class Quiet:
        create_endpoint = fr.modify_route(status_code=200)

    with pytest.raises(
        RestlyConfigurationError,
        match="the fr.modify_route of Quiet changes nothing, because Quiet comes after",
    ):

        class GadgetView(fr.RestView, Quiet):
            pass


def test_a_written_route_in_front_of_a_mixin_with_options_is_allowed(view_base):
    class Quiet:
        create_endpoint = fr.modify_route(status_code=200)

    class GadgetView(Quiet, view_base):
        @fr.post("/")
        def create_endpoint(self) -> dict[str, Any]:
            return {}

    assert GadgetView.create_endpoint._api_route_args[0] == "/"


def _route_paths_of(client: RestlyTestClient) -> set[str]:
    return {route.path for route in iter_routes(client.app.routes)}


def _path_parameter_of(client: RestlyTestClient, path: str, method: str) -> Any:
    operation = client.get("/openapi.json").json()["paths"][path][method]
    (parameter,) = [p for p in operation["parameters"] if p["in"] == "path"]
    return parameter


def test_an_id_type_with_metadata_that_cannot_be_hashed_works(make_client):
    client = make_client(id_type=Annotated[int, {"unit": "row"}])

    created = client.post("/gadgets", json={"name": "lamp"}).json()
    assert client.get(f"/gadgets/{created['id']}").json() == created
    assert _path_parameter_of(client, "/gadgets/{id}", "get")["name"] == "id"


def test_a_custom_route_with_id_type_gets_the_converter(make_client):
    class Routes:
        @fr.post("/{id}/publish")
        def publish(self, id: IDType) -> dict[str, Any]:
            return {"id": id}

        @fr.post("/{id:str}/own-converter")
        def own_converter(self, id: IDType) -> dict[str, Any]:
            return {"id": id}

        @fr.post("/{id}/merge/{other_id}")
        def merge(self, id: IDType, other_id: IDType) -> dict[str, Any]:
            return {"id": id, "other_id": other_id}

        @fr.post("/{id}/as-written")
        def as_written(self, id: int) -> dict[str, Any]:
            return {"id": id}

    client = make_client(Routes)

    assert {
        "/gadgets/{id:int}/publish",
        "/gadgets/{id:str}/own-converter",
        "/gadgets/{id:int}/merge/{other_id:int}",
        "/gadgets/{id}/as-written",
    } <= _route_paths_of(client)
    assert client.post("/gadgets/7/publish").json() == {"id": 7}
    assert client.post("/gadgets/7/merge/8").json() == {"id": 7, "other_id": 8}
    assert client.post("/gadgets/7/as-written").json() == {"id": 7}
    client.post("/gadgets/abc/publish", assert_status_code=404)
    client.post("/gadgets/abc/as-written", assert_status_code=422)


def _get_one_on_id(annotation: Any = int) -> Any:
    @fr.get("/{id}")
    def get_one_endpoint(self, id: annotation) -> dict[str, Any]:  # type: ignore[valid-type]
        return {"id": id}

    return get_one_endpoint


def _get_one_on_gadget_id(**route_kwargs: Any) -> Any:
    @fr.get("/{gadget_id}", **route_kwargs)
    def get_one_endpoint(self, gadget_id: int) -> dict[str, Any]:
        return {"id": gadget_id}

    return get_one_endpoint


def test_a_written_name_next_to_the_generated_id_is_refused(make_client):
    with pytest.raises(RestlyConfigurationError) as caught:
        make_client(get_one_endpoint=_get_one_on_gadget_id())

    message = str(caught.value)
    assert "differ only in the names of their parameters" in message
    assert "/gadgets/{gadget_id} (GET get_one)" in message
    assert "/gadgets/{id} (PATCH update)" in message


@pytest.mark.parametrize("include_in_schema", [True, False])
def test_a_method_of_your_own_on_the_path_and_method_of_a_crud_route_is_refused(
    make_client, include_in_schema
):
    class Routes:
        @fr.get("/{id}", include_in_schema=include_in_schema)
        def read_gadget(self, id: IDType) -> dict[str, Any]:
            return {"id": id}

    with pytest.raises(RestlyConfigurationError) as caught:
        make_client(Routes)

    message = str(caught.value)
    assert "have the same path and HTTP method" in message
    assert "/gadgets/{id} (GET read_gadget)" in message
    assert "/gadgets/{id} (GET get_one)" in message


def test_put_on_update_of_a_react_admin_view_is_refused(Gadget):
    class GadgetView(fr.ReactAdminView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        update_endpoint = fr.modify_route(methods=["PUT"])

    with pytest.raises(RestlyConfigurationError, match=r"\(PUT put\) and"):
        fr.include_view(FastAPI(), GadgetView)


def test_two_written_names_are_refused_as_openapi_says(make_client):
    class Routes:
        @fr.get("/{first}/report")
        def read_report(self, first: int) -> dict[str, Any]:
            return {}

        @fr.post("/{second}/report")
        def write_report(self, second: int) -> dict[str, Any]:
            return {}

    with pytest.raises(RestlyConfigurationError, match="differ only in the names"):
        make_client(Routes)


@pytest.mark.parametrize(
    "attrs",
    [
        lambda: {"get_one_endpoint": _get_one_on_id(IDType)},
        lambda: {"get_one_endpoint": _get_one_on_id()},
        lambda: {
            "get_one_endpoint": _get_one_on_gadget_id(),
            "update_endpoint": None,
            "delete_endpoint": None,
        },
    ],
    ids=["id-type", "id-as-written", "no-other-route"],
)
def test_one_name_for_an_item_path_is_allowed(make_client, attrs):
    client = make_client(**attrs())

    item_paths = [path for path in client.app.openapi()["paths"] if "{" in path]
    assert len(item_paths) == 1


def test_a_route_that_the_spec_leaves_out_may_have_another_name(make_client):
    client = make_client(
        get_one_endpoint=_get_one_on_gadget_id(include_in_schema=False)
    )

    assert set(client.app.openapi()["paths"]) == {"/gadgets", "/gadgets/{id}"}
    assert client.get("/gadgets/7").json() == {"id": 7}


def test_a_plain_view_may_have_two_names(Gadget):
    class Routes(fr.View):
        prefix = "/plain"

        @fr.get("/{first}")
        def read(self, first: int) -> dict[str, Any]:
            return {}

        @fr.post("/{second}")
        def write(self, second: int) -> dict[str, Any]:
            return {}

    app = FastAPI()
    fr.include_view(app, Routes)

    assert set(app.openapi()["paths"]) == {"/plain/{first}", "/plain/{second}"}


def test_modify_route_with_a_path_keeps_the_converter(make_client):
    client = make_client(get_one_endpoint=fr.modify_route(path="/by-id/{id}"))

    assert "/gadgets/by-id/{id:int}" in _route_paths_of(client)
    created = client.post("/gadgets", json={"name": "a"}).json()
    assert client.get(f"/gadgets/by-id/{created['id']}").json() == created


def _spec_parameter(client, path: str, method: str, name: str) -> dict[str, Any]:
    operation = client.app.openapi()["paths"][path][method]
    return next(p for p in operation["parameters"] if p["name"] == name)


def test_an_id_type_in_a_type_alias_inside_annotated_gets_the_converter(
    view_base, Gadget
):
    from typing_extensions import TypeAliasType

    MyId = TypeAliasType("MyId", IDType)

    class GadgetView(view_base):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

        @fr.get("/{id}/aliased")
        def aliased(self, id: Annotated[MyId, Path(ge=1)]) -> dict[str, int]:
            return {"id": id}

    app = FastAPI()
    fr.include_view(app, GadgetView)
    client = RestlyTestClient(app)

    assert "/gadgets/{id:int}/aliased" in _route_paths_of(client)
    assert client.get("/gadgets/4/aliased").json() == {"id": 4}


def test_a_path_on_the_id_keeps_its_arguments(make_client):
    class Routes:
        @fr.get("/{id}/annotated")
        def annotated(
            self, id: Annotated[IDType, Path(ge=5, description="The id")]
        ) -> dict[str, Any]:
            return {"id": id}

        @fr.get("/{id}/default")
        def default(
            self, id: IDType = Path(ge=5, description="The id")
        ) -> dict[str, Any]:
            return {"id": id}

    client = make_client(Routes)

    for route in ("annotated", "default"):
        path = f"/gadgets/{{id}}/{route}"
        parameter = _path_parameter_of(client, path, "get")
        assert parameter["name"] == "id"
        assert parameter["description"] == "The id"
        assert parameter["schema"]["minimum"] == 5
        assert client.get(f"/gadgets/7/{route}").json() == {"id": 7}
        response = client.get(f"/gadgets/1/{route}", assert_status_code=422)
        assert response.json()["detail"][0]["loc"] == ["path", "id"]


def test_a_field_on_the_id_keeps_its_constraint(view_base, Gadget):
    class GadgetView(view_base):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

        @fr.get("/{id}/field")
        def field(self, id: Annotated[IDType, pydantic.Field(gt=3)]) -> dict[str, int]:
            return {"id": id}

    app = FastAPI()
    fr.include_view(app, GadgetView)
    client = TestClient(app)

    parameter = _spec_parameter(client, "/gadgets/{id}/field", "get", "id")
    assert parameter["schema"]["exclusiveMinimum"] == 3
    assert client.get("/gadgets/3/field").status_code == 422
    assert client.get("/gadgets/4/field").json() == {"id": 4}


def test_a_constrained_id_type_keeps_its_constraint(view_base, Gadget):
    class GadgetView(view_base):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        id_type = pydantic.PositiveInt

    app = FastAPI()
    fr.include_view(app, GadgetView)
    client = TestClient(app)

    parameter = _spec_parameter(client, "/gadgets/{id}", "get", "id")
    assert parameter["schema"]["exclusiveMinimum"] == 0
    assert client.get("/gadgets/0").status_code == 422


@pytest.mark.parametrize("form", ["default", "annotated"])
def test_an_id_from_a_dependency_is_no_query_parameter(view_base, Gadget, form):
    def doubled(id: int) -> int:
        return id * 2

    class GadgetView(view_base):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

        if form == "default":

            @fr.get("/{id}/doubled")
            def doubled_route(self, id: IDType = Depends(doubled)) -> dict[str, int]:
                return {"id": id}

        else:

            @fr.get("/{id}/doubled")
            def doubled_route(
                self, id: Annotated[IDType, Depends(doubled)]
            ) -> dict[str, int]:
                return {"id": id}

    app = FastAPI()
    fr.include_view(app, GadgetView)
    client = RestlyTestClient(app)

    assert "/gadgets/{id}/doubled" in app.openapi()["paths"]
    assert client.get("/gadgets/4/doubled").json() == {"id": 8}


def test_a_path_without_the_id_parameter_is_the_same_for_any_parent(view_base, Gadget):
    class Parent(view_base):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

    app = FastAPI()
    fr.include_view(app, Parent)

    class Child(Parent):
        prefix = "/child"
        delete_endpoint = fr.modify_route(path="/one")

    fr.include_view(app, Child)

    parameter = _spec_parameter(
        RestlyTestClient(app), "/gadgets/child/one", "delete", "id"
    )
    assert parameter["in"] == "query"


@pytest.mark.parametrize("registered_parent", [False, True])
def test_a_new_path_with_an_unread_parameter_is_refused(
    view_base, Gadget, registered_parent
):
    class Parent(view_base):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

    app = FastAPI()
    if registered_parent:
        fr.include_view(app, Parent)

    class Child(Parent):
        prefix = "/child"
        get_one_endpoint = fr.modify_route("/{role_id}")

    with pytest.raises(
        RestlyConfigurationError,
        match=(
            r"Child\.get_one_endpoint: nothing reads the parameter role_id of the "
            r"path /\{role_id\}\. The parameter id reads the query\. Write \{id\} in "
            r"the path, or write the route method with a parameter role_id\."
        ),
    ):
        fr.include_view(app, Child)


def test_a_query_id_next_to_a_path_parameter_that_a_dependency_reads_is_kept(
    make_client,
):
    def owner(owner_name: str) -> None: ...

    client = make_client(
        get_one_endpoint=fr.modify_route(
            "/of/{owner_name}", dependencies=[Depends(owner)]
        )
    )

    parameter = _spec_parameter(client, "/gadgets/of/{owner_name}", "get", "id")
    assert parameter["in"] == "query"


def _order_of(view: type) -> list[str]:
    app = FastAPI()
    fr.include_view(app, view)
    return [
        f"{sorted(route.methods)[0]} {route.path}"
        for route in iter_routes(app.routes)
        if route.path.startswith("/gadgets")
    ]


def test_an_option_above_a_custom_route_leaves_the_custom_route_first(
    view_base, Gadget
):
    class GadgetView(view_base):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        id_type = str

        get_one_endpoint = fr.modify_route(summary="One")

        @fr.get("/stats")
        def stats(self) -> dict[str, int]:
            return {"n": 1}

    order = _order_of(GadgetView)
    assert order.index("GET /gadgets/stats") < order.index("GET /gadgets/{id}")


@pytest.mark.parametrize("registered_parent", [False, True])
def test_an_option_in_a_child_leaves_the_custom_route_first(
    view_base, Gadget, registered_parent
):
    class Parent(view_base):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        id_type = str

        @fr.get("/stats")
        def stats(self) -> dict[str, int]:
            return {"n": 1}

    if registered_parent:
        fr.include_view(FastAPI(), Parent)

    class Child(Parent):
        prefix = "/child"
        get_one_endpoint = fr.modify_route(summary="One")

    order = _order_of(Child)
    assert order.index("GET /gadgets/child/stats") < order.index(
        "GET /gadgets/child/{id}"
    )


def test_an_option_keeps_the_order_of_the_routes(view_base, Gadget):
    class Plain(view_base):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

        @fr.get("/stats")
        def stats(self) -> dict[str, int]:
            return {"n": 1}

    class WithOption(view_base):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

        update_endpoint = fr.modify_route(summary="Change")

        @fr.get("/stats")
        def stats(self) -> dict[str, int]:
            return {"n": 1}

    assert _order_of(WithOption) == _order_of(Plain)


def _stats_base(view_base: type, Gadget: type) -> type:
    class StatsBase(view_base):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        id_type = str

        @fr.get("/stats")
        def stats(self) -> dict[str, int]:
            return {"n": 1}

    return StatsBase


def test_an_option_from_a_mixin_keeps_the_order_of_the_routes(view_base, Gadget):
    StatsBase = _stats_base(view_base, Gadget)

    class Quiet:
        get_one_endpoint = fr.modify_route(summary="One")

    class Plain(StatsBase):
        pass

    class WithOption(Quiet, StatsBase):
        pass

    assert _order_of(WithOption) == _order_of(Plain)


@pytest.mark.parametrize("registered_middle", [False, True])
def test_an_option_in_a_middle_class_keeps_the_order_of_the_routes(
    view_base, Gadget, registered_middle
):
    StatsBase = _stats_base(view_base, Gadget)

    class Middle(StatsBase):
        get_one_endpoint = fr.modify_route(summary="One")

    if registered_middle:
        fr.include_view(FastAPI(), Middle)

    class Plain(StatsBase):
        pass

    class Leaf(Middle):
        pass

    assert _order_of(Leaf) == _order_of(Plain)


def test_a_status_without_a_body_also_drops_the_model_of_a_decorator(view_base, Gadget):
    class Parent(view_base):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

        @fr.post("/{id}/archive", response_model=GadgetSchema)
        def archive(self, id: IDType) -> None:
            return None

    class Quiet(Parent):
        prefix = "/quiet"
        archive = fr.modify_route(status_code=204)

    class Loud(Quiet):
        prefix = "/loud"
        archive = fr.modify_route(status_code=200)

    app = FastAPI()
    fr.include_view(app, Quiet)
    fr.include_view(app, Loud)
    paths = app.openapi()["paths"]

    quiet = paths["/gadgets/quiet/{id}/archive"]["post"]["responses"]
    assert "content" not in quiet["204"]
    loud = paths["/gadgets/quiet/loud/{id}/archive"]["post"]["responses"]
    assert "GadgetSchema" in str(loud["200"]["content"])


@pytest.mark.parametrize("status", [404, "404"])
def test_a_status_set_to_none_is_left_out_as_int_and_as_text(make_client, status):
    client = make_client(get_one_endpoint=fr.modify_route(responses={status: None}))

    responses = client.app.openapi()["paths"]["/gadgets/{id}"]["get"]["responses"]
    assert "404" not in responses


def test_modify_route_over_a_written_base_gives_no_misuse_warning(
    view_base, Gadget, recwarn
):
    class Base(view_base):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

        @fr.post("/")
        def create_endpoint(self, data: CreateSchema) -> ResponseSchema: ...

    class Child(Base):
        prefix = "/child"
        create_endpoint = fr.modify_route(status_code=200)

    fr.configure(warn_on_misuse=True)
    try:
        fr.include_view(FastAPI(), Child)
    finally:
        fr.configure(warn_on_misuse=False)
    assert not [w for w in recwarn if "overrides the endpoint method" in str(w.message)]
