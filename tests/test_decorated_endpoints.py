"""A decorator on a view method gets the same call as on a FastAPI function.

FastAPI calls an endpoint with keyword arguments only, ``self`` too. Restly's
copies of an inherited endpoint, and its guard of a list route, pass the
arguments on as they get them. So a decorator sees ``args == ()``, on a method
that the view defines and on one that it inherits.
"""

import functools
import inspect
from typing import Any

import fastapi
import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient

from ._models import SharedBase
from ._openapi_spec import spec
from .conftest import create_tables


def _wrap_both(func, check):
    """The decorator, for a sync and for an async method."""
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            check(args, kwargs)
            return await func(*args, **kwargs)

        return async_wrapper

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        check(args, kwargs)
        return func(*args, **kwargs)

    return sync_wrapper


def request_from_args_or_kwargs(func):
    """The logic of slowapi: it takes the position of ``request`` from the
    signature, and evaluates ``args[idx]`` when ``args`` is not empty. So
    ``self`` alone as a positional argument raises IndexError."""
    position = list(inspect.signature(func).parameters).index("request")

    def check(args, kwargs):
        request = kwargs.get("request", args[position] if args else None)
        assert isinstance(request, Request)

    return _wrap_both(func, check)


def request_from_kwargs(func):
    """The usual auth decorator of a FastAPI app."""

    def check(args, kwargs):
        if kwargs["request"].headers.get("x-token") != "yes":
            raise HTTPException(status_code=401)

    return _wrap_both(func, check)


def self_as_a_named_parameter(func):
    """A method decorator: ``self`` comes first, by its name."""
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(self, *args: Any, **kwargs: Any) -> Any:
            assert isinstance(self, fr.View)
            return await func(self, *args, **kwargs)

        return async_wrapper

    @functools.wraps(func)
    def sync_wrapper(self, *args: Any, **kwargs: Any) -> Any:
        assert isinstance(self, fr.View)
        return func(self, *args, **kwargs)

    return sync_wrapper


DECORATORS = [
    request_from_args_or_kwargs,
    request_from_kwargs,
    self_as_a_named_parameter,
]


def _view_with(decorator, *, is_async: bool, inherited: bool) -> type[fr.View]:
    if is_async:

        class AsyncBase(fr.View):
            @fr.get("/report")
            @decorator
            async def report(self, request: Request, q: int = 0):
                return {"path": request.url.path, "q": q}

        base: type[fr.View] = AsyncBase
    else:

        class SyncBase(fr.View):
            @fr.get("/report")
            @decorator
            def report(self, request: Request, q: int = 0):
                return {"path": request.url.path, "q": q}

        base = SyncBase
    if not inherited:
        base.prefix = "/views"
        return base

    class Child(base):
        prefix = "/views"

    return Child


@pytest.mark.parametrize("decorator", DECORATORS)
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("inherited", [False, True], ids=["own", "inherited"])
def test_a_decorator_on_a_custom_route(decorator, is_async, inherited):
    app = fastapi.FastAPI()
    fr.include_view(app, _view_with(decorator, is_async=is_async, inherited=inherited))
    client = TestClient(app)

    response = client.get("/views/report?q=3", headers={"x-token": "yes"})

    assert response.status_code == 200
    assert response.json() == {"path": "/views/report", "q": 3}
    # The decorator stays out of the spec: only the query parameter shows.
    parameters = spec(app)["paths"]["/views/report"]["get"]["parameters"]
    assert [parameter["name"] for parameter in parameters] == ["q"]


def test_a_request_goes_through_the_decorator_on_an_inherited_route():
    app = fastapi.FastAPI()
    fr.include_view(
        app, _view_with(request_from_kwargs, is_async=False, inherited=True)
    )
    client = TestClient(app)

    assert client.get("/views/report").status_code == 401


def _thing_views(view_base, decorator):
    """A base view whose list route and create route carry the decorator, and
    a view that inherits them."""

    class DecoratedThing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    if issubclass(view_base, fr.AsyncRestView):

        class AsyncThingBase(view_base):
            model = DecoratedThing

            @fr.get("/")
            @decorator
            async def get_many_endpoint(
                self, request: Request, list_params: Any
            ) -> fr.views.ListResponseSchema:
                return await self.handle_get_many(list_params)

            @fr.post("/")
            @decorator
            async def create_endpoint(
                self, request: Request, data: fr.views.CreateSchema
            ) -> fr.views.ResponseSchema:
                return await self.handle_create(data)

        base: Any = AsyncThingBase
    else:

        class SyncThingBase(view_base):
            model = DecoratedThing

            @fr.get("/")
            @decorator
            def get_many_endpoint(
                self, request: Request, list_params: Any
            ) -> fr.views.ListResponseSchema:
                return self.handle_get_many(list_params)

            @fr.post("/")
            @decorator
            def create_endpoint(
                self, request: Request, data: fr.views.CreateSchema
            ) -> fr.views.ResponseSchema:
                return self.handle_create(data)

        base = SyncThingBase

    class ThingView(base):
        prefix = "/things"

    return ThingView


def _assert_the_spec_of_the_decorated_routes(app, view):
    """A decorator takes nothing away from the spec of an inherited route."""
    paths = spec(app)["paths"]["/things"]
    list_route = paths["get"]
    # the list params, and no parameter for ``request`` or ``list_params``
    names = {parameter["name"] for parameter in list_route["parameters"]}
    assert {"name", "sort", "page", "page_size"} <= names
    assert not {"request", "list_params", "self"} & names
    assert list_route["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": f"#/components/schemas/{view.list_response_schema.__name__}"
    }
    create_route = paths["post"]
    body = create_route["requestBody"]["content"]["application/json"]["schema"]
    assert body == {"$ref": f"#/components/schemas/{view.create_schema.__name__}"}


@pytest.mark.parametrize("decorator", DECORATORS)
def test_a_decorator_on_an_inherited_crud_route_async(client, decorator):
    view = _thing_views(fr.AsyncRestView, decorator)
    fr.include_view(client.app, view)
    create_tables()
    headers = {"x-token": "yes"}

    created = client.post("/things", json={"name": "a"}, headers=headers)
    assert created.json()["name"] == "a"
    listed = client.get("/things", headers=headers)
    assert [row["name"] for row in listed.json()["data"]] == ["a"]
    # the guard of a list route still runs: an unknown key is a 422
    client.get("/things?nope=1", headers=headers, assert_status_code=422)
    _assert_the_spec_of_the_decorated_routes(client.app, view)


@pytest.mark.parametrize("decorator", DECORATORS)
def test_a_decorator_on_an_inherited_crud_route_sync(sync_db, decorator):
    engine, _ = sync_db
    view = _thing_views(fr.RestView, decorator)
    app = fastapi.FastAPI()
    fr.include_view(app, view)
    SharedBase.metadata.create_all(engine)
    client = RestlyTestClient(app)
    headers = {"x-token": "yes"}

    created = client.post("/things", json={"name": "a"}, headers=headers)
    assert created.json()["name"] == "a"
    listed = client.get("/things", headers=headers)
    assert [row["name"] for row in listed.json()["data"]] == ["a"]
    client.get("/things?nope=1", headers=headers, assert_status_code=422)
    _assert_the_spec_of_the_decorated_routes(app, view)


@pytest.mark.parametrize("decorator", DECORATORS)
def test_a_decorator_under_route_options_async(client, decorator):
    class OptionsThingView(_thing_views(fr.AsyncRestView, decorator)):
        create_endpoint = fr.route_options(status_code=200)

    fr.include_view(client.app, OptionsThingView)
    create_tables()

    created = client.post(
        "/things",
        json={"name": "a"},
        headers={"x-token": "yes"},
        assert_status_code=200,
    )
    assert created.json()["name"] == "a"
    assert "200" in spec(client.app)["paths"]["/things"]["post"]["responses"]


@pytest.mark.parametrize("decorator", DECORATORS)
def test_a_decorator_under_route_options_sync(sync_db, decorator):
    engine, _ = sync_db

    class OptionsThingView(_thing_views(fr.RestView, decorator)):
        create_endpoint = fr.route_options(status_code=200)

    app = fastapi.FastAPI()
    fr.include_view(app, OptionsThingView)
    SharedBase.metadata.create_all(engine)

    created = RestlyTestClient(app).post(
        "/things",
        json={"name": "a"},
        headers={"x-token": "yes"},
        assert_status_code=200,
    )
    assert created.json()["name"] == "a"
    assert "200" in spec(app)["paths"]["/things"]["post"]["responses"]


def test_a_python_call_of_an_inherited_route_stays_a_python_call(client):
    """A subclass can call the method of its base, with a positional argument."""

    class CalledThing(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class CalledBase(fr.AsyncRestView):
        model = CalledThing

        @fr.get("/{id}/name")
        @self_as_a_named_parameter
        async def name_of(self, id: int) -> dict[str, str]:
            return {"name": (await self.get_one(id)).name}

    class CalledMiddle(CalledBase):
        pass

    class CalledView(CalledMiddle):
        prefix = "/called"

        @fr.get("/{id}/shout")
        async def shout(self, id: int) -> dict[str, str]:
            name = (await self.name_of(id))["name"]
            return {"name": (await super().name_of(id))["name"].upper() + name}

    fr.include_view(client.app, CalledView)
    create_tables()

    created = client.post("/called", json={"name": "ann"}).json()

    assert client.get(f"/called/{created['id']}/name").json() == {"name": "ann"}
    assert client.get(f"/called/{created['id']}/shout").json() == {"name": "ANNann"}


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_a_first_parameter_with_another_name_works_on_an_inherited_route(is_async):
    """A copy does not name the first parameter, so the name can be any."""
    if is_async:

        class AsyncNamed(fr.View):
            @fr.get("/one")
            async def one(this):
                return {"class": type(this).__name__}

        base: type[fr.View] = AsyncNamed
    else:

        class SyncNamed(fr.View):
            @fr.get("/one")
            def one(this):
                return {"class": type(this).__name__}

        base = SyncNamed

    class NamedChild(base):
        prefix = "/named"

    app = fastapi.FastAPI()
    fr.include_view(app, NamedChild)

    assert TestClient(app).get("/named/one").json() == {"class": "NamedChild"}


def test_a_list_route_with_another_first_parameter_name_async(client):
    """The guard of a list route finds the view by the name of that parameter."""

    class AsyncNamedRow(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class AsyncNamedListView(fr.AsyncRestView):
        prefix = "/named-list"
        model = AsyncNamedRow

        @fr.get("/")
        async def get_many_endpoint(
            this, list_params: Any
        ) -> fr.views.ListResponseSchema:
            return await this.handle_get_many(list_params)

    fr.include_view(client.app, AsyncNamedListView)
    create_tables()

    assert client.get("/named-list").json()["data"] == []
    client.get("/named-list?nope=1", assert_status_code=422)


def test_a_list_route_with_another_first_parameter_name_sync(sync_db):
    engine, _ = sync_db

    class SyncNamedRow(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class SyncNamedListView(fr.RestView):
        prefix = "/named-list"
        model = SyncNamedRow

        @fr.get("/")
        def get_many_endpoint(this, list_params: Any) -> fr.views.ListResponseSchema:
            return this.handle_get_many(list_params)

    app = fastapi.FastAPI()
    fr.include_view(app, SyncNamedListView)
    SharedBase.metadata.create_all(engine)
    client = RestlyTestClient(app)

    assert client.get("/named-list").json()["data"] == []
    client.get("/named-list?nope=1", assert_status_code=422)
