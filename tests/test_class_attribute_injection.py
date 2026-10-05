"""Which view class attributes FastAPI injects, as docs/class_based_views.md
states the contract.

A Depends (or Security) marker or a bare-injectable special type is wired. A
request-parameter marker (Path, Query, Header, Cookie, Body, Form, File) fails
registration, since FastAPI would never set the attribute. Every other
annotation is only a type hint, and a plain annotation on a mixin or subclass
keeps the base's wiring.
"""

from collections.abc import Iterator
from typing import Annotated, Any

import pytest
from fastapi import (
    Body,
    Cookie,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    Path,
    Query,
    Request,
    Security,
)
from fastapi.testclient import TestClient

import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError


class User:
    def __init__(self, name: str) -> None:
        self.name = name


def get_current_user() -> User:
    return User("ann")


def get_scope_name() -> str:
    return "read"


class ExpectsUser:
    current_user: User  # a mixin's hint for type checkers


def _report(view: Any) -> dict[str, Any]:
    return {
        "current_user": view.current_user.name,
        "scope_name": view.scope_name,
        "request": type(view.request).__name__,
        "has_reviewer": hasattr(view, "reviewer"),
        "authorized_as": view.authorized_as,
    }


@pytest.fixture(params=["sync", "async"])
def client(request: pytest.FixtureRequest) -> Iterator[TestClient]:
    sync = request.param == "sync"
    app = FastAPI()

    class UserView(ExpectsUser, fr.View):
        prefix = "/users"
        current_user: Annotated[User, Depends(get_current_user)]
        scope_name: Annotated[str, Security(get_scope_name)]
        request: Request
        reviewer: User
        authorized_as: str = ""

        def authorize(self) -> None:
            self.authorized_as = self.current_user.name

        if sync:

            @fr.get("/probe")
            def probe(self) -> dict[str, Any]:
                self.authorize()
                return _report(self)

        else:

            @fr.get("/probe")
            async def probe(self) -> dict[str, Any]:  # type: ignore[misc]
                self.authorize()
                return _report(self)

    class ReviewView(UserView):
        prefix = "/reviews"
        current_user: User  # plain re-declaration on a subclass

    fr.include_view(app, UserView)
    fr.include_view(app, ReviewView)
    yield TestClient(app)


def test_depends_security_and_special_types_are_wired(client):
    report = client.get("/users/probe").json()

    assert report["current_user"] == "ann"
    assert report["scope_name"] == "read"
    assert report["request"] == "Request"


def test_wired_attributes_reach_every_method(client):
    assert client.get("/users/probe").json()["authorized_as"] == "ann"


def test_a_bare_annotation_is_not_set(client):
    assert client.get("/users/probe").json()["has_reviewer"] is False


def test_a_plain_annotation_keeps_the_wiring(client):
    assert client.get("/users/reviews/probe").json()["current_user"] == "ann"


def get_roles() -> list[str]:
    return ["reader"]


def test_a_dependency_of_a_generic_type_is_wired():
    app = FastAPI()

    @fr.include_view(app)
    class RoleView(fr.View):
        prefix = "/roles"
        roles: Annotated[list[str], Depends(get_roles)]

        @fr.get("")
        def list_roles(self) -> list[str]:
            return self.roles

    assert TestClient(app).get("/roles").json() == ["reader"]


def test_generic_annotations_are_only_hints():
    app = FastAPI()

    @fr.include_view(app)
    class ItemView(fr.View):
        prefix = "/items"
        seen: list[str]
        limits: dict[str, int]
        kind: type[User]
        notes: Annotated[list[str], "free text"]

        @fr.get("")
        def probe(self) -> list[bool]:
            names = ("seen", "limits", "kind", "notes")
            return [hasattr(self, name) for name in names]

    assert TestClient(app).get("/items").json() == [False, False, False, False]


@pytest.mark.parametrize(
    "marker",
    [Path(), Query(), Header(), Cookie(), Body(), Form(), File()],
    ids=lambda marker: type(marker).__name__,
)
def test_a_request_parameter_marker_fails_registration(marker):
    class ItemView(fr.View):
        prefix = "/items"
        dry_run: Annotated[bool, marker] = False

    kind = type(marker).__name__
    with pytest.raises(
        RestlyConfigurationError, match=rf"ItemView\.dry_run has a {kind}\(\) marker"
    ):
        fr.include_view(FastAPI(), ItemView)


def test_a_marker_on_a_mixin_fails_registration():
    class Localized:
        accept_language: Annotated[str, Header()] = "en"

    class ItemView(Localized, fr.View):
        prefix = "/items"

    with pytest.raises(
        RestlyConfigurationError,
        match=r"ItemView\.accept_language has a Header\(\) marker",
    ):
        fr.include_view(FastAPI(), ItemView)


def test_the_error_names_the_alternatives():
    class ItemView(fr.View):
        prefix = "/items"
        dry_run: Annotated[bool, Query()] = False

    with pytest.raises(RestlyConfigurationError) as error:
        fr.include_view(FastAPI(), ItemView)

    assert "on the endpoint method that reads it" in str(error.value)
    assert "Annotated[..., Depends(...)]" in str(error.value)
