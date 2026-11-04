"""What a route documents and answers when a view changes one HTTP detail."""

from collections.abc import Callable, Iterator
from typing import Any

import pydantic
import pytest
from fastapi import Depends, FastAPI
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


def test_route_options_change_one_argument_and_keep_the_rest(make_client):
    client = make_client(create_endpoint=fr.route_options(status_code=200))

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


def test_route_options_with_a_status_without_a_body_drop_the_response_model(
    make_client,
):
    client = make_client(update_endpoint=fr.route_options(status_code=204))
    created = client.post("/gadgets", json={"name": "a"}).json()

    responses = client.get("/openapi.json").json()["paths"]["/gadgets/{id}"]["patch"][
        "responses"
    ]
    assert "content" not in responses["204"]
    response = client.patch(f"/gadgets/{created['id']}", json={"name": "b"})
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/gadgets/{created['id']}").json()["name"] == "b"


def test_route_options_with_a_status_with_a_body_bring_the_response_model_back(
    make_client, view_base
):
    class NoBody(view_base):
        update_endpoint = fr.route_options(status_code=204)

    client = make_client(NoBody, update_endpoint=fr.route_options(status_code=200))
    created = client.post("/gadgets", json={"name": "a"}).json()

    responses = client.get("/openapi.json").json()["paths"]["/gadgets/{id}"]["patch"][
        "responses"
    ]
    assert "$ref" in responses["200"]["content"]["application/json"]["schema"]
    response = client.patch(f"/gadgets/{created['id']}", json={"name": "b"})
    assert response.status_code == 200
    assert response.json()["name"] == "b"


def test_route_options_keep_a_status_without_a_body_down_the_classes(
    make_client, view_base
):
    class NoBody(view_base):
        update_endpoint = fr.route_options(status_code=204)

    class Plain(NoBody):
        pass

    client = make_client(Plain, update_endpoint=fr.route_options(summary="Change it"))

    operation = client.get("/openapi.json").json()["paths"]["/gadgets/{id}"]["patch"]
    assert operation["summary"] == "Change it"
    assert "content" not in operation["responses"]["204"]


def test_route_options_keep_a_response_model_that_they_name_on_a_status_without_a_body(
    view_base, Gadget
):
    class GadgetView(view_base):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        update_endpoint = fr.route_options(status_code=204, response_model=GadgetSchema)

    with pytest.raises(AssertionError, match="must not have a response body"):
        fr.include_view(FastAPI(), GadgetView)


def test_route_options_change_the_method(make_client):
    client = make_client(update_endpoint=fr.route_options(methods=["PUT"]))
    created = client.post("/gadgets", json={"name": "a"}).json()

    path = f"/gadgets/{created['id']}"
    assert client.put(path, json={"name": "b"}).json()["name"] == "b"
    client.patch(path, json={"name": "c"}, assert_status_code=405)


def test_route_options_on_delete_answer_the_new_status(make_client):
    client = make_client(delete_endpoint=fr.route_options(status_code=200))
    created = client.post("/gadgets", json={"name": "a"}).json()

    spec = client.get("/openapi.json").json()
    assert "200" in spec["paths"]["/gadgets/{id}"]["delete"]["responses"]
    assert client.delete(f"/gadgets/{created['id']}").status_code == 200


def test_route_options_keep_the_hooks_of_the_view(make_client, view_base):
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

    client = make_client(Upper, create_endpoint=fr.route_options(status_code=200))

    assert client.post("/gadgets", json={"name": "a"}).json()["name"] == "A"


def test_route_options_add_dependencies_after_those_of_the_base(make_client, view_base):
    log: list[str] = []

    class Base(view_base):
        create_endpoint = fr.route_options(dependencies=[_recorder(log, "base")])

    client = make_client(
        Base,
        dependencies=[_recorder(log, "view")],
        create_endpoint=fr.route_options(dependencies=[_recorder(log, "leaf")]),
    )
    client.post("/gadgets", json={"name": "a"})

    assert log == ["view", "base", "leaf"]


def test_route_options_that_list_a_base_dependency_again_run_it_there(
    make_client, view_base
):
    log: list[str] = []
    guard = _recorder(log, "guard")

    class Base(view_base):
        create_endpoint = fr.route_options(dependencies=[guard])

    client = make_client(
        Base,
        create_endpoint=fr.route_options(dependencies=[_recorder(log, "first"), guard]),
    )
    client.post("/gadgets", json={"name": "a"})

    assert log == ["first", "guard"]


def test_route_options_add_responses_per_status_code(Gadget):
    class Base(fr.RestView):
        create_endpoint = fr.route_options(
            responses={409: {"description": "Taken"}, 418: {"description": "Teapot"}}
        )

    class GadgetView(Base):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        create_endpoint = fr.route_options(
            responses={402: {"description": "Pay"}, 418: None}
        )
        get_one_endpoint = fr.route_options(responses={404: None})

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
def test_route_options_pass_an_argument_to_fastapi(options, key, value, Gadget):
    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        create_endpoint = fr.route_options(**options)

    app = FastAPI()
    fr.include_view(app, GadgetView)

    assert app.openapi()["paths"]["/gadgets"]["post"][key] == value


def test_route_options_with_a_name_give_fastapi_the_id_and_the_summary(Gadget):
    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        create_endpoint = fr.route_options(name="create_gadget")

    app = FastAPI()
    fr.include_view(app, GadgetView)

    operation = app.openapi()["paths"]["/gadgets"]["post"]
    assert operation["operationId"] == "create_gadget_gadgets_post"
    assert operation["summary"] == "Create Gadget"
    assert str(app.url_path_for("create_gadget")) == "/gadgets"


def test_route_options_hide_a_route_from_the_spec_only(make_client):
    client = make_client(create_endpoint=fr.route_options(include_in_schema=False))

    assert "post" not in client.get("/openapi.json").json()["paths"]["/gadgets"]
    assert client.post("/gadgets", json={"name": "a"}).status_code == 201


def test_route_options_change_the_path(make_client):
    client = make_client(
        create_endpoint=fr.route_options("/new"),
        get_one_endpoint=fr.route_options(path="/{id}/detail"),
    )

    created = client.post("/gadgets/new", json={"name": "a"}).json()
    client.post("/gadgets", json={"name": "b"}, assert_status_code=405)
    assert client.get(f"/gadgets/{created['id']}/detail").json()["name"] == "a"
    # the id keeps the converter of the view
    client.get("/gadgets/abc/detail", assert_status_code=404)


def test_route_options_with_an_empty_path_leave_out_the_trailing_slash_route(Gadget):
    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        create_endpoint = fr.route_options(path="")

    app = FastAPI()
    fr.include_view(app, GadgetView)

    posts = [
        route.path
        for route in iter_routes(app.routes)
        if "POST" in getattr(route, "methods", ())
    ]
    assert posts == ["/gadgets"]


def test_route_options_keep_the_list_params(make_client):
    client = make_client(get_many_endpoint=fr.route_options(summary="All gadgets"))
    client.post("/gadgets", json={"name": "a"})
    client.post("/gadgets", json={"name": "b"})

    response = client.get("/gadgets", params={"page_size": 1})
    assert len(response.json()["data"]) == 1
    client.get("/gadgets", params={"nope": 1}, assert_status_code=422)

    operation = client.get("/openapi.json").json()["paths"]["/gadgets"]["get"]
    assert operation["summary"] == "All gadgets"
    assert "page_size" in {parameter["name"] for parameter in operation["parameters"]}


def test_route_options_change_the_response_model(make_client):
    class Name(fr.BaseSchema):
        name: str

    client = make_client(get_one_endpoint=fr.route_options(response_model=Name))
    created = client.post("/gadgets", json={"name": "a"}).json()

    assert client.get(f"/gadgets/{created['id']}").json() == {"name": "a"}


def test_route_options_leave_the_base_and_other_views_alone(Gadget):
    class ChangedView(fr.RestView):
        prefix = "/changed"
        model = Gadget
        schema = GadgetSchema
        create_endpoint = fr.route_options(status_code=200)

    class OtherView(fr.RestView):
        prefix = "/other"
        model = Gadget
        schema = GadgetSchema

    assert "200" in _statuses(ChangedView)["/changed", "post"]
    assert "201" in _statuses(OtherView)["/other", "post"]
    assert fr.RestView.create_endpoint._api_route_args[1]["status_code"] == 201


def test_a_subclass_inherits_route_options_and_changes_them_further(Gadget):
    class Base(fr.RestView):
        model = Gadget
        schema = GadgetSchema
        create_endpoint = fr.route_options(status_code=200, summary="Base")

    class Child(Base):
        prefix = "/child"

    class Grandchild(Child):
        prefix = "/grand"
        create_endpoint = fr.route_options(summary="Grandchild")

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


def test_a_plain_mixin_holds_route_options_for_a_view(make_client):
    class Quiet:
        create_endpoint = fr.route_options(status_code=200, summary="Mixin")

    class Named:
        create_endpoint = fr.route_options(summary="Front")

    client = make_client(Named, Quiet)

    operation = client.get("/openapi.json").json()["paths"]["/gadgets"]["post"]
    assert operation["summary"] == "Front"
    assert client.post("/gadgets", json={"name": "a"}).status_code == 200


def test_a_written_route_replaces_all_route_options_and_reaches_them_with_super(
    make_client, view_base
):
    class Base(view_base):
        create_endpoint = fr.route_options(status_code=200, summary="Base")

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


def test_route_options_on_a_custom_route_of_a_plain_view():
    class Reports(fr.View):
        prefix = "/reports"

        @fr.get("/daily", summary="Daily")
        def daily(self) -> dict[str, int]:
            return {}

    class Quiet(Reports):
        daily = fr.route_options(deprecated=True)

    app = FastAPI()
    fr.include_view(app, Quiet)

    operation = app.openapi()["paths"]["/reports/daily"]["get"]
    assert operation["summary"] == "Daily"
    assert operation["deprecated"] is True


@pytest.mark.parametrize("name", ["create", "creat_endpoint", "prefix"])
def test_route_options_that_name_no_route_raise(name):
    with pytest.raises(
        RestlyConfigurationError,
        match=f"BadView.{name} is a fr.route_options, but the class inherits no",
    ):
        type("BadView", (fr.RestView,), {name: fr.route_options(status_code=200)})


def test_route_options_over_a_route_that_a_base_removed_raise():
    class NoCreate(fr.RestView):
        create_endpoint = None

    with pytest.raises(RestlyConfigurationError, match="inherits no route method"):

        class BadView(NoCreate):
            create_endpoint = fr.route_options(status_code=200)


def test_route_options_with_an_unknown_argument_fail_as_a_decorator_does(Gadget):
    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema
        create_endpoint = fr.route_options(stauts_code=200)

    with pytest.raises(TypeError, match="stauts_code"):
        fr.include_view(FastAPI(), GadgetView)


def test_route_options_set_after_the_class_body_raise(Gadget):
    class GadgetView(fr.RestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

    GadgetView.create_endpoint = fr.route_options(status_code=200)

    with pytest.raises(RestlyConfigurationError, match="Set it in the class body"):
        fr.include_view(FastAPI(), GadgetView)
