"""``include_routes`` names the routes that a view serves.

A route is served when ``include_routes`` has it and ``exclude_routes`` does
not. So a read-only copy of a view with custom routes leaves out the custom
write routes of its base, also a route that the base gets later.
``exclude_routes`` leaves out only the routes that it names.
"""

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly._routes import iter_routes, original_route

from ._models import SharedBase

READS = (fr.ViewRoute.GET_MANY, fr.ViewRoute.GET_ONE)
WRITES = (fr.ViewRoute.CREATE, fr.ViewRoute.UPDATE, fr.ViewRoute.DELETE)
SERVED_READS = {"GET /tags", "GET /tags/{id:int}"}


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
    """A view with the five CRUD routes, a custom read route and a custom
    write route."""

    class Tag(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class TagView(view_base):
        prefix = "/tags"
        model = Tag

        @fr.get("/stats")
        def stats(self) -> dict:
            return {}

        @fr.post("/merge")
        def merge(self) -> dict:
            return {}

    return TagView


def test_a_view_serves_only_the_crud_routes_that_it_includes(tag_view):
    class PublicTagView(tag_view):
        include_routes = READS

    assert _served(PublicTagView) == SERVED_READS


def test_a_view_serves_only_the_custom_routes_that_it_includes(tag_view):
    class StatsTagView(tag_view):
        include_routes = (fr.ViewRoute.GET_MANY, "stats")

    assert _served(StatsTagView) == {"GET /tags", "GET /tags/stats"}


def test_exclude_routes_leaves_out_a_route_that_include_routes_has(tag_view):
    """Also an ``exclude_routes`` that the view inherits."""

    class ReadTagView(tag_view):
        include_routes = (*READS, "stats")
        exclude_routes = ("stats",)

    class NoDeleteTagView(tag_view):
        exclude_routes = (fr.ViewRoute.DELETE,)

    class ItemTagView(NoDeleteTagView):
        include_routes = (fr.ViewRoute.GET_ONE, fr.ViewRoute.DELETE)

    assert _served(ReadTagView) == SERVED_READS
    assert _served(ItemTagView) == {"GET /tags/{id:int}"}


@pytest.mark.parametrize("base_first", [False, True], ids=["alone", "base-first"])
def test_a_subclass_inherits_include_routes_or_replaces_it(tag_view, base_first):
    """A subclass with its own value serves the routes that its base leaves
    out, also when the base registered first."""

    class PublicTagView(tag_view):
        include_routes = READS

    class InheritingTagView(PublicTagView):
        pass

    class FullTagView(PublicTagView):
        include_routes = ("*",)

    views = [PublicTagView, InheritingTagView, FullTagView]
    served = {view: _served(view) for view in (views if base_first else views[::-1])}

    assert served[PublicTagView] == SERVED_READS
    assert served[InheritingTagView] == SERVED_READS
    assert served[FullTagView] == {
        *SERVED_READS,
        "POST /tags",
        "PATCH /tags/{id:int}",
        "DELETE /tags/{id:int}",
        "GET /tags/stats",
        "POST /tags/merge",
    }


def test_a_route_that_the_base_gets_later_is_left_out(tag_view):
    """The reason for ``include_routes``: a read-only copy that names its
    routes does not serve a write route that the base gets later. A copy
    with ``exclude_routes`` serves it."""

    class PublicTagView(tag_view):
        include_routes = READS

    class ExcludingTagView(tag_view):
        exclude_routes = (*WRITES, "merge")

    # Later, the base gets a new write route. The two copies do not change.
    def archive(self) -> dict:
        return {}

    tag_view.archive = fr.post("/archive")(archive)

    assert "POST /tags/archive" in _served(tag_view)
    assert _served(PublicTagView) == SERVED_READS
    assert _served(ExcludingTagView) == {
        *SERVED_READS,
        "GET /tags/stats",
        "POST /tags/archive",
    }


@pytest.mark.parametrize("entry", ["merg", "get_one"], ids=["typo", "business"])
def test_an_entry_that_names_no_route_fails_at_registration(tag_view, entry):
    """As in ``exclude_routes``: a typo, or the name of the business method
    instead of the route method, raises when the view registers."""

    class PublicTagView(tag_view):
        include_routes = (fr.ViewRoute.GET_MANY, entry)

    with pytest.raises(
        AttributeError,
        match=f"'{entry}' in include_routes is not a route on PublicTagView",
    ):
        fr.include_view(FastAPI(), PublicTagView)


def test_a_composite_key_needs_no_item_routes_that_the_view_leaves_out(view_base):
    """An id from the path cannot name one row of a composite key, so a view
    with the default ``where_id`` fails when it serves an item route. A view
    whose ``include_routes`` has no item route registers."""

    class Seat(SharedBase):
        row: Mapped[int] = mapped_column(primary_key=True)
        number: Mapped[int] = mapped_column(primary_key=True)

    class SeatView(view_base):
        prefix = "/seats"
        model = Seat
        include_routes = (fr.ViewRoute.GET_MANY, fr.ViewRoute.CREATE)

    assert _served(SeatView) == {"GET /seats", "POST /seats"}
