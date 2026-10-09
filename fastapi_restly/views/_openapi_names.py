"""Warn when two classes in the OpenAPI spec have the same name.

OpenAPI keeps all classes in one list, by name. When two different classes
have the same name, pydantic shows both under long names built from the
module path, such as ``app__users__views__UserCreate``. When the module path
is the same too, it numbers them. Generated clients use these names as type
names, and they change when a module moves or when the views are registered
in another order. Restly warns when the spec is built, and names the classes.
"""

import inspect
import re
import warnings
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import fastapi
from fastapi.openapi.utils import get_fields_from_routes
from fastapi.routing import APIRoute
from pydantic.json_schema import GenerateJsonSchema
from starlette.routing import BaseRoute

from .._routes import iter_routes, original_route
from ..exc import RestlyDuplicateSchemaNameWarning
from ..schemas import derive_schema_list_response

_CLASHES_ATTR = "_fr_openapi_name_clashes"
_normalize = GenerateJsonSchema().normalize_name
# What pydantic adds to a long name: the mode, then the number.
_LONG_NAME_SUFFIX = re.compile(r"(?:-Input|-Output)?(?:__\d+)?$")

# A role, and what Restly generates the class from (None: you wrote it).
_Role = tuple[str, str | None]
# For each class, its roles and the views that use it in that role.
_Roles = dict[type, dict[_Role, list[str]]]


def _warn_on_name_clashes(app: fastapi.FastAPI, spec: dict[str, Any]) -> None:
    """Warn once for each name that more than one class in ``spec`` has.

    The check runs once per spec. The warnings come on every call, so a test
    that builds the spec sees them even when an earlier test built it first.
    """
    cached = app.__dict__.get(_CLASHES_ATTR)
    if cached is None or cached[0] is not spec:
        # The same routes that FastAPI builds the spec from.
        routes = [*app.routes, *app.webhooks.routes]
        cached = (spec, _clash_messages(routes, spec))
        setattr(app, _CLASHES_ATTR, cached)
    for message in cached[1]:
        warnings.warn(message, RestlyDuplicateSchemaNameWarning, stacklevel=3)


def _clash_messages(routes: Sequence[BaseRoute], spec: dict[str, Any]) -> list[str]:
    keys_by_long_name: dict[str, set[str]] = defaultdict(set)
    for key in spec.get("components", {}).get("schemas", {}):
        keys_by_long_name[_LONG_NAME_SUFFIX.sub("", key)].add(key)

    # A long name is only used when another class has the same short name.
    classes: dict[str, dict[type, None]] = defaultdict(dict)
    keys: dict[str, set[str]] = defaultdict(set)
    for ref, cls in _component_classes(routes).items():
        short_name, long_name = _component_names(ref)
        if long_name in keys_by_long_name:
            classes[short_name][cls] = None
            keys[short_name] |= keys_by_long_name[long_name]
    if not classes:
        return []

    roles = _view_roles(routes)
    return [
        _clash_message(name, list(classes[name]), sorted(keys[name]), roles)
        for name in sorted(classes)
    ]


def _component_classes(routes: Sequence[BaseRoute]) -> dict[str, type]:
    """The classes that FastAPI can put in the spec, by pydantic's core ref."""
    found: dict[str, type] = {}
    seen: set[int] = set()
    for field in get_fields_from_routes(routes):
        # FastAPI builds the spec from this same core schema.
        _collect_classes(field._type_adapter.core_schema, found, seen)
    return found


def _collect_classes(node: Any, found: dict[str, type], seen: set[int]) -> None:
    if isinstance(node, dict):
        if id(node) in seen:
            return
        seen.add(id(node))
        ref, cls = node.get("ref"), node.get("cls")
        if isinstance(ref, str) and isinstance(cls, type):
            found[ref] = cls
        for value in node.values():
            _collect_classes(value, found, seen)
    elif isinstance(node, list):
        for item in node:
            _collect_classes(item, found, seen)


def _component_names(core_ref: str) -> tuple[str, str]:
    """The short and the long name that pydantic can give a class in the spec.

    This follows ``GenerateJsonSchema.get_defs_ref``: drop the ids from the
    core ref, then the long name keeps the module paths and the short name
    drops them.
    """
    parts = [part.rsplit(":", 1)[0] for part in re.split(r"([\][,])", core_ref)]
    short = "".join(re.sub(r"(?:[^.[\]]+\.)+((?:[^.[\]]+))", r"\1", p) for p in parts)
    return _normalize(short), _normalize("".join(parts))


def _view_roles(routes: Sequence[BaseRoute]) -> _Roles:
    """For each class that a view's route uses in a role, the role and the
    views. Only the routes that are in the app count, so a route that a view
    excludes, or a list route that returns a plain response, adds no role."""
    roles: _Roles = defaultdict(lambda: defaultdict(list))
    for route in map(original_route, iter_routes(routes)):
        if not isinstance(route, APIRoute):
            continue
        view = _route_view(route)
        if view is None:
            continue
        used = [route.response_model]
        if route.body_field is not None:
            used.append(route.body_field.field_info.annotation)
        view_roles = _roles_of(view)
        for cls in used:
            for role in view_roles.get(cls, ()):
                if view.__name__ not in roles[cls][role]:
                    roles[cls][role].append(view.__name__)
    return roles


def _route_view(route: APIRoute) -> type | None:
    """The Restly view of a route: the class that FastAPI builds as the
    route's ``self``."""
    from ._base import BaseRestView

    for dependency in route.dependant.dependencies:
        call = dependency.call
        if inspect.isclass(call) and issubclass(call, BaseRestView):
            return call
    return None


def _roles_of(view: Any) -> dict[type, list[_Role]]:
    """The classes of a view by role. For a class that Restly generates, the
    role also says what Restly generates it from."""
    generated = view.__dict__.get("_fr_generated", frozenset())
    # A view without a schema gets one from its model, and the other classes
    # come from that schema.
    if "schema" in generated:
        from_schema = f"from the model {_path(view.model)}"
    else:
        from_schema = f"from {_path(view.schema)}"

    def source(attribute: str) -> str | None:
        return from_schema if attribute in generated else None

    response = view.schema_response
    list_response = derive_schema_list_response(response, pagination=view.pagination)
    roles: dict[type, list[_Role]] = defaultdict(list)
    roles[view.schema].append(("schema", source("schema")))
    roles[response].append(("response class", source("schema_response")))
    # The list response comes from the response class, which can be the
    # view's own.
    roles[list_response].append(
        ("list response", source("schema_response") or f"from {_path(response)}")
    )
    roles[view.schema_create].append(("create body", source("schema_create")))
    roles[view.schema_update].append(("update body", source("schema_update")))
    return roles


def _clash_message(
    name: str, classes: list[type], keys: list[str], roles: _Roles
) -> str:
    lines = [
        f"More than one class has the name {name}. OpenAPI shows them under "
        f"long names that can change: {', '.join(keys)}. Generated clients use "
        "these names as type names."
    ]
    for cls in sorted(classes, key=_path):
        lines.append(f"- {_describe(cls, roles.get(cls, {}))}")
    lines.append(
        "Give each class its own name. A class that you wrote for a view's "
        "role goes on the view, for example as schema_create. Restly names "
        "the classes it generates after the view's schema, and the list "
        "response after the response class. To change those names, rename "
        "that class, or give a view without a schema one of its own."
    )
    if any("__restly_list_response_of__" in cls.__dict__ for cls in classes):
        lines.append(
            "A list response gets its envelope from the pagination. A custom "
            "route that names the list response of a view passes the view's "
            "pagination to derive_schema_list_response."
        )
    lines.append('See "Name your schemas" in the docs.')
    return "\n".join(lines)


def _describe(cls: type, roles: dict[_Role, list[str]]) -> str:
    """One line for a class in the warning.

    A class that Restly generates is not in the module that its path names,
    so its line says what Restly generates it from instead.
    """
    phrases = []
    for (role, source), views in roles.items():
        names = " and ".join(views)
        if source is None:
            phrases.append(f"the {role} of {names}")
        else:
            phrases.append(f"the {role} that Restly generates {source} for {names}")
    if not phrases:
        list_response_of = cls.__dict__.get("__restly_list_response_of__")
        if list_response_of is not None:
            envelope, response = list_response_of
            return (
                f"a list response with the envelope {envelope.__name__} that "
                f"Restly generates from {_path(response)}"
            )
        return _path(cls)
    if all(source is not None for _, source in roles):
        return "; ".join(phrases)
    return f"{_path(cls)}: {'; '.join(phrases)}"


def _path(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"
