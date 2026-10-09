"""Find the routes of an app on every FastAPI version that Restly supports.

FastAPI 0.137 changed how an app holds the routes of a router that it
includes. Before, ``include_router`` copied those routes into ``app.routes``.
Now ``app.routes`` holds one entry for the router, and FastAPI builds the
routes from it when it needs them.
"""

from collections.abc import Iterable, Iterator
from typing import Any

from starlette.routing import BaseRoute
from starlette.types import Scope


def iter_routes(routes: Iterable[BaseRoute]) -> Iterator[Any]:
    """Each route in ``routes``, and each route of the routers they include.

    On FastAPI 0.137 and later, a route of an included router comes as
    FastAPI's context of the route. Its ``path`` has the prefix of the
    router, and its ``dependant`` has the dependencies of the app and the
    router. :func:`original_route` gives the route object itself.
    """
    for route in routes:
        expand = getattr(route, "effective_route_contexts", None)
        if expand is None:
            yield route
        else:
            yield from expand()


def original_route(route: Any) -> Any:
    """The route object of a route that :func:`iter_routes` yields."""
    return getattr(route, "original_route", route)


def matched_route(scope: Scope) -> Any:
    """The route that a request matched, with the dependencies of the app and
    of its routers.

    On FastAPI 0.137 and later, ``scope["route"]`` is the route object
    without those dependencies, so this reads FastAPI's context of the route.
    """
    fastapi_scope = scope.get("fastapi")
    if isinstance(fastapi_scope, dict):
        context = fastapi_scope.get("effective_route_context")
        if context is not None:
            return context
    return scope.get("route")
