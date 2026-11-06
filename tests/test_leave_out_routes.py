"""A view leaves out an inherited route when it sets the route method to
``None``, such as ``delete_endpoint = None``. The removed settings
``include_routes`` and ``exclude_routes`` raise and name this fix."""

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly._routes import iter_routes, original_route
from fastapi_restly.exc import RestlyConfigurationError

from ._models import SharedBase

ALL_CRUD = {
    "GET /tags",
    "POST /tags",
    "GET /tags/{id:int}",
    "PATCH /tags/{id:int}",
    "DELETE /tags/{id:int}",
}


def _served(view: type) -> set[str]:
    """The routes that the view serves on an app of its own, such as
    ``"GET /tags/{id:int}"``. The list and create routes answer with and
    without the final slash; this gives them once, without it."""
    app = FastAPI()
    fr.include_view(app, view)
    return {
        f"{method} {route.path.rstrip('/')}"
        for route in iter_routes(app.routes)
        if isinstance(original_route(route), APIRoute)
        for method in route.methods
    }


@pytest.fixture(params=[fr.RestView, fr.AsyncRestView], ids=["sync", "async"])
def view_base(request: pytest.FixtureRequest) -> type:
    return request.param


@pytest.fixture
def tag_view(view_base: type) -> type:
    """A view with the five CRUD routes and a custom write route."""

    class Tag(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class TagView(view_base):
        prefix = "/tags"
        model = Tag

        @fr.post("/merge")
        def merge(self) -> dict:
            return {}

    return TagView


def test_a_view_leaves_out_a_crud_route_set_to_none(tag_view):
    class NoDeleteTagView(tag_view):
        delete_endpoint = None

    assert _served(NoDeleteTagView) == ALL_CRUD - {"DELETE /tags/{id:int}"} | {
        "POST /tags/merge"
    }


def test_a_view_leaves_out_a_custom_route_set_to_none(tag_view):
    class PlainTagView(tag_view):
        merge = None

    assert _served(PlainTagView) == ALL_CRUD


def test_a_read_only_copy_leaves_out_the_write_routes_that_it_names(tag_view):
    class PublicTagView(tag_view):
        create_endpoint = None
        update_endpoint = None
        delete_endpoint = None

    assert _served(PublicTagView) == {
        "GET /tags",
        "GET /tags/{id:int}",
        "POST /tags/merge",
    }


@pytest.mark.parametrize("base_first", [False, True], ids=["alone", "base-first"])
def test_the_base_keeps_a_route_that_a_subclass_leaves_out(tag_view, base_first):
    class NoDeleteTagView(tag_view):
        delete_endpoint = None

    app = FastAPI()
    if base_first:
        fr.include_view(app, tag_view)
    fr.include_view(app, NoDeleteTagView)
    if base_first:
        assert "DELETE /tags/{id:int}" in _served(tag_view)
    assert "DELETE /tags/{id:int}" not in _served(NoDeleteTagView)


def test_modify_route_on_a_route_that_the_base_leaves_out_raises(tag_view):
    class NoDeleteTagView(tag_view):
        delete_endpoint = None

    with pytest.raises(RestlyConfigurationError, match="inherits no route method"):

        class SubView(NoDeleteTagView):
            delete_endpoint = fr.modify_route(status_code=200)


@pytest.mark.parametrize("setting", ["include_routes", "exclude_routes"])
def test_the_removed_settings_raise_and_name_the_fix(view_base, setting):
    class Tag(fr.IDMixin, SharedBase):
        name: Mapped[str]

    with pytest.raises(
        RestlyConfigurationError,
        match=rf"TagView sets {setting}, which was removed. To leave out a "
        r"route, set it to None in the class body, such as "
        r"delete_endpoint = None.",
    ):
        attrs = {"prefix": "/tags", "model": Tag, setting: ("delete_endpoint",)}
        type("TagView", (view_base,), attrs)


def test_a_composite_key_needs_no_item_routes_that_the_view_leaves_out(view_base):
    """An id from the path cannot name one row of a composite key, so a view
    with the default ``where_id`` fails when it serves an item route. A view
    that leaves out each item route registers."""

    class Seat(SharedBase):
        row: Mapped[int] = mapped_column(primary_key=True)
        number: Mapped[int] = mapped_column(primary_key=True)

    class SeatView(view_base):
        prefix = "/seats"
        model = Seat
        get_one_endpoint = None
        update_endpoint = None
        delete_endpoint = None

    assert _served(SeatView) == {"GET /seats", "POST /seats"}
