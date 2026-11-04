"""What a route documents and answers when a view changes one HTTP detail."""

from collections.abc import Callable, Iterator
from typing import Any

import pydantic
import pytest
from fastapi import FastAPI
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient
from fastapi_restly.views import IDType, ResponseSchema

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
