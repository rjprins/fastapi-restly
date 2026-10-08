"""Warn when two classes in the OpenAPI spec have the same name.

OpenAPI keeps all classes in one list, by name. When two different classes
have the same name, pydantic shows both under long names built from the
module path, such as ``app__users__views__UserCreate``. When the module path
is the same too, it numbers them. Generated clients use these names as type
names, and they change when a module moves or when the views are registered
in another order. Restly warns when the spec is built, and names the classes.
"""

import re
import warnings
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

import fastapi
import pydantic
from fastapi.openapi.utils import get_fields_from_routes
from pydantic.json_schema import GenerateJsonSchema
from starlette.routing import BaseRoute

from .._pagination import _list_envelope
from ..exc import RestlyMisuseWarning
from ..schemas._base import _derive_schema_response

_CLASHES_ATTR = "_fr_openapi_name_clashes"
_normalize = GenerateJsonSchema().normalize_name
# What pydantic adds to a long name: the mode, then the number.
_LONG_NAME_SUFFIX = re.compile(r"(?:-Input|-Output)?(?:__\d+)?$")


def _warn_on_name_clashes(
    app: fastapi.FastAPI, spec: dict[str, Any], views: Iterable[type]
) -> None:
    """Warn once for each name that more than one class in ``spec`` has.

    The check runs once per spec. The warnings come on every call, so a test
    that builds the spec sees them even when an earlier test built it first.
    """
    cached = app.__dict__.get(_CLASHES_ATTR)
    if cached is None or cached[0] is not spec:
        cached = (spec, _clash_messages(app.routes, spec, views))
        setattr(app, _CLASHES_ATTR, cached)
    for message in cached[1]:
        warnings.warn(message, RestlyMisuseWarning, stacklevel=3)


def _clash_messages(
    routes: Sequence[BaseRoute], spec: dict[str, Any], views: Iterable[type]
) -> list[str]:
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

    roles = _view_roles(views)
    return [
        _clash_message(name, list(classes[name]), sorted(keys[name]), roles)
        for name in sorted(classes)
    ]


def _component_classes(routes: Sequence[BaseRoute]) -> dict[str, type]:
    """The classes that FastAPI can put in the spec, by pydantic's core ref."""
    found: dict[str, type] = {}
    seen: set[int] = set()
    for field in get_fields_from_routes(routes):
        try:
            core_schema = pydantic.TypeAdapter(field.field_info.annotation).core_schema
        except Exception:  # noqa: BLE001 - FastAPI reports a type it cannot use
            continue
        _collect_classes(core_schema, found, seen)
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


def _view_roles(views: Iterable[type]) -> dict[type, dict[str, list[str]]]:
    """For each class a view uses in a role, the role and the views."""
    roles: dict[type, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for view in views:
        generated = getattr(view, "_fr_generated", frozenset())
        response = _derive_schema_response(view.schema)
        roles[response]["the response class that Restly generates for"].append(
            view.__name__
        )
        roles[_list_envelope(view.pagination, response)][
            "the list response that Restly generates for"
        ].append(view.__name__)
        for attribute, role in (
            ("schema_create", "create"),
            ("schema_update", "update"),
        ):
            made = "that Restly generates " if attribute in generated else ""
            roles[getattr(view, attribute)][f"the {role} body {made}for"].append(
                view.__name__
            )
    return roles


def _clash_message(
    name: str,
    classes: list[type],
    keys: list[str],
    roles: dict[type, dict[str, list[str]]],
) -> str:
    lines = [
        f"More than one class has the name {name}. OpenAPI shows them under "
        f"long names that can change: {', '.join(keys)}. Generated clients use "
        "these names as type names."
    ]
    for cls in sorted(classes, key=_path):
        role = "; ".join(
            f"{phrase} {' and '.join(views)}"
            for phrase, views in roles.get(cls, {}).items()
        )
        lines.append(f"- {_path(cls)}: {role}" if role else f"- {_path(cls)}")
    lines.append(
        "Give each class its own name. A class that you wrote for a view's "
        "role goes on the view, for example as schema_create. Restly names "
        "the classes it generates after the view's schema, so to change those "
        'names, rename the view\'s schema. See "Name your schemas" in the docs.'
    )
    return "\n".join(lines)


def _path(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"
