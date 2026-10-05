"""Each class in a view hierarchy adds ``dependencies`` and ``responses``.

A subclass that set its own ``dependencies`` used to replace the base's list,
so a base guard was dropped without any error, and its own ``responses``
dropped the 404 that ``BaseRestView`` documents. They now add up base first,
as ``prefix`` does and as FastAPI's nested routers do.
"""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient

from .conftest import create_tables


@pytest.fixture(params=[fr.RestView, fr.AsyncRestView], ids=["sync", "async"])
def view_base(request: pytest.FixtureRequest) -> Iterator[type]:
    if request.param is fr.RestView:
        request.getfixturevalue("sync_db")
    yield request.param


@pytest.fixture
def make_client(
    request: pytest.FixtureRequest, view_base: type
) -> Callable[..., RestlyTestClient]:
    class Gadget(fr.IDBase):
        name: Mapped[str]

    class GadgetRead(fr.IDSchema):
        name: str

    def make(*bases: type, **attrs: Any) -> RestlyTestClient:
        view = type(
            "GadgetView",
            (*bases, view_base),
            {"prefix": "/gadgets", "model": Gadget, "schema": GadgetRead, **attrs},
        )
        app = FastAPI()
        fr.include_view(app, view)
        if view_base is fr.RestView:
            engine, _ = request.getfixturevalue("sync_db")
            fr.DataclassBase.metadata.create_all(engine)
        else:
            create_tables()
        return RestlyTestClient(app)

    return make


def _recorder(log: list[str], name: str) -> Callable[[], None]:
    def dependency() -> None:
        log.append(name)

    return dependency


def test_subclass_dependencies_keep_the_base_guard(make_client):
    def deny() -> None:
        raise fr.exc.Forbidden("no")

    class Guarded:
        dependencies = [Depends(deny)]

    client = make_client(Guarded, dependencies=[Depends(lambda: None)])

    client.get("/gadgets/", assert_status_code=403)
    client.post("/gadgets/", json={"name": "x"}, assert_status_code=403)


def test_dependencies_run_base_first(make_client):
    log: list[str] = []

    class Outer:
        dependencies = [Depends(_recorder(log, "outer"))]

    class Inner(Outer):
        dependencies = [Depends(_recorder(log, "inner"))]

    client = make_client(Inner, dependencies=[Depends(_recorder(log, "view"))])
    client.get("/gadgets/")

    assert log == ["outer", "inner", "view"]


def test_dependencies_from_sibling_mixins_follow_the_mro(make_client):
    log: list[str] = []

    class First:
        dependencies = [Depends(_recorder(log, "first"))]

    class Second:
        dependencies = [Depends(_recorder(log, "second"))]

    make_client(First, Second).get("/gadgets/")

    # Base first: the reversed MRO puts the later-listed mixin first.
    assert log == ["second", "first"]


def _list_route_dependencies(client: RestlyTestClient) -> list[Any]:
    route = next(
        route
        for route in client.app.routes
        if isinstance(route, APIRoute)
        and route.path.rstrip("/") == "/gadgets"
        and "GET" in route.methods
    )
    return route.dependencies


def test_a_spelled_out_base_list_lists_each_dependency_once(make_client):
    base = Depends(lambda: None)
    view = Depends(lambda: None)

    class Base:
        dependencies = [base]

    client = make_client(Base, dependencies=[*Base.dependencies, view])

    # FastAPI's request cache would hide a duplicate at run time, so check
    # the route itself.
    dependencies = _list_route_dependencies(client)
    assert len(dependencies) == 2
    assert dependencies[0] is base
    assert dependencies[1] is view


def test_a_subclass_can_run_its_own_dependency_before_the_base(make_client):
    log: list[str] = []

    class Base:
        dependencies = [Depends(_recorder(log, "base"))]

    client = make_client(
        Base, dependencies=[Depends(_recorder(log, "own")), *Base.dependencies]
    )
    client.get("/gadgets/")

    assert log == ["own", "base"]


@pytest.mark.parametrize("own", [None, []], ids=["none", "empty"])
def test_a_subclass_cannot_clear_the_base_dependencies(make_client, own):
    def deny() -> None:
        raise fr.exc.Forbidden("no")

    class Guarded:
        dependencies = [Depends(deny)]

    make_client(Guarded, dependencies=own).get("/gadgets/", assert_status_code=403)


def test_an_inherited_list_is_not_duplicated(make_client):
    log: list[str] = []

    class Base:
        dependencies = [Depends(_recorder(log, "base"))]

    class Middle(Base):
        prefix = "/v1"

    make_client(Middle).get("/v1/gadgets/")

    assert log == ["base"]


def test_dependencies_accept_a_tuple(make_client):
    log: list[str] = []

    client = make_client(dependencies=(Depends(_recorder(log, "view")),))
    client.get("/gadgets/")

    assert log == ["view"]


def test_responses_merge_down_the_hierarchy(make_client):
    class Billing:
        responses = {402: {"description": "Payment required"}}

    client = make_client(Billing, responses={418: {"description": "Teapot"}})
    spec = client.get("/openapi.json").json()

    for path in ("/gadgets", "/gadgets/{id}"):
        for operation in spec["paths"][path].values():
            assert {"402", "404", "418"} <= set(operation["responses"])
    detail = spec["paths"]["/gadgets/{id}"]["get"]["responses"]
    assert detail["404"]["description"] == "Not found"


def test_subclass_response_wins_for_the_same_status(make_client):
    client = make_client(responses={404: {"description": "No such gadget"}})
    spec = client.get("/openapi.json").json()

    detail = spec["paths"]["/gadgets/{id}"]["get"]["responses"]
    assert detail["404"]["description"] == "No such gadget"
