"""Checks of the settings and the method names of a view class.

They refuse a name that Restly renamed or removed, a final handler that a
view overrides, a load hook that the loads cannot call, a list setting with
a bad value, and a route with the name of a setting. ``_owner`` and
``_set_by`` find the class that sets a setting, for the messages.
"""

import inspect
import sys
from collections.abc import Mapping
from typing import Any, Callable, ClassVar, get_origin

from .._pagination import (
    DEFAULT_PAGE_SIZE,
    NoPagination,
    NumberedPagination,
    OffsetPagination,
    Pagination,
    _check_query_param,
)
from ..exc import RestlyConfigurationError
from ..query._impl import _KEY_SETTINGS, _check_default_sort, _check_key_setting

if sys.version_info >= (3, 14):
    import annotationlib


def _own_annotations(klass: type) -> Mapping[str, Any]:
    """The annotations that ``klass`` itself declares, without evaluating one
    that names something not defined yet; see :func:`_route_parameters`."""
    if sys.version_info >= (3, 14):
        return annotationlib.get_annotations(
            klass, format=annotationlib.Format.FORWARDREF
        )
    return klass.__dict__.get("__annotations__", {})


def _setting_names(view_cls: type) -> dict[str, type]:
    """The view settings of ``view_cls``, with the Restly class that declares
    each. A setting is a ``ClassVar`` of Restly's view classes, such as
    ``tags`` or ``model``: Restly reads it from the class."""
    settings: dict[str, type] = {}
    for klass in reversed(view_cls.__mro__):
        if not klass.__module__.startswith("fastapi_restly."):
            continue
        for name, annotation in _own_annotations(klass).items():
            if get_origin(annotation) is not ClassVar or name.startswith("_"):
                continue
            # a private class that declares the setting again, such as a
            # mixin, is no name for a message
            if name not in settings or not klass.__name__.startswith("_"):
                settings[name] = klass
    return settings


def _reject_route_names_of_settings(view_cls: type) -> None:
    """Refuse a route method with the name of a view setting, also from a
    mixin. A method is a class attribute too, so it would replace the
    setting. A class may still set the setting over a route of a mixin."""
    for name, declarer in _setting_names(view_cls).items():
        if hasattr(inspect.getattr_static(view_cls, name, None), "_api_route_args"):
            raise RestlyConfigurationError(
                f"{view_cls.__name__}.{name}(){_set_by(view_cls, name)} is a "
                f"route method with the name of the view setting "
                f"{declarer.__name__}.{name}, so it replaces the setting. Give "
                f"the method another name. The route keeps its path."
            )


#: Bare business-method names. A ``@route``-decorated method of a CRUD view
#: must not be named like one of these: it would shadow the verb (which the
#: ``handle_<verb>`` handlers call) and collide with ``<verb>_endpoint`` at the
#: same path. Override the business method *without* a decorator for domain
#: logic; use ``<verb>_endpoint`` or a distinct name for a custom route.
#: ``get_one`` and ``get_many`` are final, so the class-definition guard
#: refuses them first. A plain ``View`` has none of these methods, so the
#: names are free there.
_BARE_VERB_NAMES = ("create", "update", "delete")


def _reject_bare_verb_route_names(view_cls: type) -> None:
    """Refuse a route method of a CRUD view with the name of a business
    method, also from a mixin."""
    for name in _BARE_VERB_NAMES:
        if hasattr(inspect.getattr_static(view_cls, name, None), "_api_route_args"):
            raise RestlyConfigurationError(
                f"{view_cls.__name__}.{name}(){_set_by(view_cls, name)} is a "
                f"route method with the name of the business method {name}, so "
                f"it replaces that method, and its route collides with "
                f"{name}_endpoint. To replace the route, override "
                f"{name}_endpoint instead. For a custom action, give the method "
                f"another name."
            )


# Removed view methods. The business methods no longer flush, so these steps
# moved: a definition on a view would be dead code, and a stamp or a save
# hooked there would silently stop running.
_REMOVED_DOMAIN_UTILITIES = {
    "make_new_object": (
        "super().create(data) builds the new object of the view's model, "
        "and the commit bracket saves it after create returns."
    ),
    "update_object": (
        "super().update(obj, data) applies the data to the object, and "
        "the commit bracket saves it after update returns."
    ),
    "save_object": (
        "The commit bracket saves the object that create or update returns, "
        "and the obj of a write_action block: it adds it, flushes, refreshes "
        "it and loads it with the loader options. Call "
        "session.flush() when you need an id earlier."
    ),
}

# Callable from a custom route, never overridden. Every generated CRUD route
# then runs ``authorize`` and, on writes, the commit bracket.
_FINAL_HANDLERS = (
    "handle_get_many",
    "handle_get_one",
    "handle_create",
    "handle_update",
    "handle_delete",
)

# The loads are final: every read and the load of every write go through
# them, so an override could drop the scope or leak into a write. Each
# message names the hooks that answer the question an override asked.
_FINAL_LOADS = {
    "get_one": (
        "get_one is the load of every item route: GET, PATCH, DELETE and "
        "your action routes. It applies the scope and raises the 404. To find "
        "the row by another key, such as a slug, override where_id(id). To "
        "change the 404, override not_found(id). To load more relationships "
        "or a SQL expression, override get_loader_options. To add a value to "
        "the response, override to_single_response. To read from another "
        "source on one route, replace get_one_endpoint."
    ),
    "get_many": (
        "get_many is the load of the list. It applies the scope, the list "
        "params, the page and the count. To filter, sort or page in another "
        "way, override apply_list_params, or add a filter with "
        "fr.list_filter. To load more relationships or a SQL expression, "
        "override get_loader_options. To add a value to each row, override "
        "to_single_response. To read from another source, replace "
        "get_many_endpoint."
    ),
    "select": (
        "select is the start of every read: select(model), the scope and the "
        "loader options. To change the rows, set scope. To change the loads, "
        "override get_loader_options."
    ),
}
# Hooks that the loads call. A value with one of these names, such as a
# text, would fail only at the first read.
_LOAD_HOOKS = ("where_id", "not_found")
_WRITE_VERBS = ("create", "update", "delete")


def _check_load_hooks(cls: type) -> None:
    """Refuse a load hook that the loads cannot call.

    ``where_id`` builds SQL, so it is never ``async``. ``not_found`` may be
    ``async`` on an async view, which awaits it, but not on a sync one.
    """
    is_async = getattr(cls, "_fr_async_view", False)
    for klass in cls.__mro__:
        if klass.__module__.startswith("fastapi_restly."):
            continue
        origin = _origin(cls, klass)
        for name in _LOAD_HOOKS:
            if name not in vars(klass):
                continue
            # through the descriptor: a classmethod or a staticmethod is
            # callable once it is looked up
            hook = getattr(klass, name)
            if not callable(hook):
                raise RestlyConfigurationError(
                    f"{cls.__name__}.{name}{origin} is a "
                    f"{type(vars(klass)[name]).__name__}, but the loads call "
                    f"{name}(id). "
                    f"Define {name} as a method, or use another name for the "
                    "value."
                )
            if inspect.iscoroutinefunction(hook) and (
                name == "where_id" or not is_async
            ):
                raise RestlyConfigurationError(
                    f"{cls.__name__}.{name}{origin} is async, but "
                    + (
                        "where_id builds SQL, so it runs no query. Define it with def."
                        if name == "where_id"
                        else "a sync view cannot await it. Define it with def."
                    )
                )


def _final_handler_message(view_name: str, name: str, origin: str) -> str:
    """What a subclass that defines ``name`` is told to use instead."""
    verb = name.removeprefix("handle_")
    if verb not in _WRITE_VERBS:
        return (
            f"{view_name} defines {name}{origin}, which is a final handler: it "
            "handles the steps of the request, and a custom route calls it. "
            "Put a gate in authorize, the rows in scope, the lookup of an id "
            "in where_id, the list params in apply_list_params, a response "
            f"value in to_single_response, and the HTTP contract in "
            f"{verb}_endpoint."
        )
    return (
        f"{view_name} defines {name}{origin}, which is a final handler: it "
        f"handles the steps of the request, and a custom route calls it. Put "
        f"domain logic in the {verb} business method, a gate in authorize, "
        "a side effect in before_action_commit or after_action_commit, "
        f"and the HTTP contract in {verb}_endpoint. To put several writes in "
        f"one commit, call {name} inside shared_write_action_commit()."
    )


# Replaced by ``pagination``. A view that still sets one is told what to write.
_REPLACED_PAGINATION_SETTINGS = ("paginated", "default_page_size", "max_page_size")


def _replaced_pagination_message(
    view_name: str, found: dict[str, tuple[Any, str]]
) -> str:
    """What a view that sets old pagination settings writes instead: one
    suggestion that carries every value it set. ``found`` maps each setting
    to its value and where it came from."""
    values = {name: value for name, (value, _) in found.items()}
    sizes = {
        name: values[name]
        for name in ("default_page_size", "max_page_size")
        if name in values
    }
    maximum = sizes.get("max_page_size")
    if (
        "default_page_size" not in sizes
        and isinstance(maximum, int)
        and maximum < DEFAULT_PAGE_SIZE
    ):
        # the default page size of 50 would be above the new maximum
        sizes = {"default_page_size": maximum, **sizes}
    if values.get("paginated") is False:
        instead = "Write 'pagination = None' to return every row."
    elif sizes:
        arguments = ", ".join(f"{name}={value!r}" for name, value in sizes.items())
        instead = (
            f"Write 'pagination = fr.NumberedPagination({arguments})', or call "
            f"'.replace({arguments})' on pagination settings that views share."
        )
        if "paginated" in values:
            instead += " Remove 'paginated': views paginate by default."
    else:
        instead = "Remove it: views paginate by default."
    names = ", ".join(f"{name}{origin}" for name, (_, origin) in found.items())
    verb = "was" if len(found) == 1 else "were"
    return (
        f"{view_name} sets {names}, which {verb} replaced by the 'pagination' "
        f"setting. {instead}"
    )


def _check_pagination_setting(cls: type) -> None:
    """Reject the replaced pagination settings, and a ``pagination`` that is
    not a settings instance. Runs when the class is defined and again at
    registration, so a value assigned to the class later is caught too."""
    # the old settings are no longer read; a definition would be silently
    # dead, and a dead max_page_size is a cap that is gone
    found: dict[str, tuple[Any, str]] = {}
    for klass in cls.__mro__:
        for name in _REPLACED_PAGINATION_SETTINGS:
            if name in vars(klass) and name not in found:
                origin = _origin(cls, klass)
                found[name] = (vars(klass)[name], origin)
    if found:
        raise RestlyConfigurationError(
            _replaced_pagination_message(cls.__name__, found)
        )
    pagination = getattr(cls, "pagination", None)
    if not (pagination is None or isinstance(pagination, Pagination)):
        hint = (
            f" Create an instance: fr.{pagination.__name__}()."
            if pagination in (NumberedPagination, OffsetPagination, NoPagination)
            else ""
        )
        raise RestlyConfigurationError(
            f"{cls.__name__}.pagination must be a fr.NumberedPagination, "
            "fr.OffsetPagination or fr.NoPagination instance, or None, "
            f"got {pagination!r}.{hint}"
        )


def _check_list_settings(cls: type) -> None:
    """Reject a list setting with a bad value, when the class is defined: a
    key setting that is not a tuple of keys and patterns, a sort parameter
    name that cannot be a query parameter, a ``default_sort`` that is not
    sort keys, or a ``sort_nulls`` that is not ``'first'``, ``'last'`` or
    ``None``. Whether each key matches a field is checked when the view
    registers, with the schema."""
    for name in _KEY_SETTINGS:
        if name in vars(cls):
            _check_key_setting(f"{cls.__name__}.{name}", vars(cls)[name])
    if "sort_query_param" in vars(cls):
        _check_query_param(
            f"{cls.__name__}.sort_query_param", vars(cls)["sort_query_param"]
        )
    if "default_sort" in vars(cls):
        _check_default_sort(f"{cls.__name__}.default_sort", vars(cls)["default_sort"])
    # also a value from a mixin, which nothing else checks
    sort_nulls = getattr(cls, "sort_nulls", "last")
    if sort_nulls not in ("first", "last", None):
        raise RestlyConfigurationError(
            f"{cls.__name__}.sort_nulls{_set_by(cls, 'sort_nulls')} must be "
            f"'first', 'last' or None, got {sort_nulls!r}."
        )


def _check_unknown_query_params(cls: type) -> None:
    value = vars(cls).get("unknown_query_params", "forbid")
    if value not in ("forbid", "ignore"):
        raise RestlyConfigurationError(
            f"{cls.__name__}.unknown_query_params must be 'forbid' or 'ignore', "
            f"got {value!r}."
        )


def _origin(cls: type, klass: type) -> str:
    """`` (from AppView)`` when ``klass`` is a base class of ``cls``."""
    return "" if klass is cls else f" (from {klass.__name__})"


def _owner(cls: type, name: str) -> type:
    """The class in the MRO of ``cls`` that sets ``name``, or ``cls`` when
    none does."""
    return next((klass for klass in cls.__mro__ if name in vars(klass)), cls)


def _set_by(cls: type, name: str) -> str:
    """`` (from AppView)`` when a base class of ``cls`` sets ``name``."""
    return _origin(cls, _owner(cls, name))


# Renamed before 1.0. Restly no longer reads the old names, so a definition
# would be silently dead, or would fail only on a request.
_RENAMED_SETTINGS = {
    "listing_param_schema": "list_params_schema",
    "schema_list_params": "list_params_schema",
    "schema_response": "response_schema",
    "schema_create": "create_schema",
    "schema_update": "update_schema",
}
_RENAMED_METHODS = {
    "to_listing_response": "to_list_response",
    "to_react_admin_listing_response": "to_list_response",
    "to_response_schema": "to_single_response",
    "apply_query_params": "apply_list_params",
    "get_relationship_loader_options": "get_loader_options",
}


# Removed before 1.0, with what to use instead.
_REMOVED_METHODS = {
    "to_response": (
        "To change one object in a response, override to_single_response. To "
        "change the list response, set list_response_schema or override "
        "to_list_response. To change what one route sends, replace its endpoint "
        "method."
    )
}


def _missing_view_attribute(view_name: str, name: str) -> str:
    """The message for a view attribute that does not exist: the
    replacement when it is a removed or renamed method."""
    if name in _REMOVED_DOMAIN_UTILITIES:
        return (
            f"{view_name}.{name} was removed from the view. "
            f"{_REMOVED_DOMAIN_UTILITIES[name]} For another model, use "
            f"fr.objects.{name} or async_{name}."
        )
    if name in _REMOVED_METHODS:
        return f"{view_name}.{name} was removed. {_REMOVED_METHODS[name]}"
    if name in _RENAMED_METHODS:
        return (
            f"{view_name}.{name} is now {_RENAMED_METHODS[name]}."
            f"{_RENAMED_METHOD_HINTS.get(name, '')}"
        )
    if name in _RENAMED_SETTINGS:
        return f"{view_name}.{name} is now {_RENAMED_SETTINGS[name]}."
    return f"{view_name!r} object has no attribute {name!r}"


_RENAMED_METHOD_HINTS = {
    "to_listing_response": (
        " It takes only the list result: the list params are in "
        "list_result.list_params."
    ),
    "to_react_admin_listing_response": (
        " The list params are in list_result.list_params."
    ),
    "apply_query_params": " Its arguments are (query, list_params).",
    "get_relationship_loader_options": (
        " It holds everything that a read loads, a with_expression too."
    ),
}
# Removed before 1.0, with what to use instead.
_REMOVED_SETTINGS = {
    "route_options": (
        "Override the endpoint method and pass the route arguments to its "
        "decorator, as in FastAPI."
    ),
    "extra_query_params": (
        "Declare the key where your code reads it: a parameter of the endpoint "
        "method, a dependency, or @fr.list_filter. For keys that are only known "
        'at run time, set unknown_query_params = "ignore".'
    ),
}


def _route_parameters(route: Callable[..., Any]) -> Mapping[str, inspect.Parameter]:
    """The parameters of ``route``, without evaluating its annotations.

    On 3.14 a route may name a class that its module defines further down,
    because annotations are read only when asked for. Evaluating them while
    the view class is defined would raise ``NameError`` for that name.
    """
    if sys.version_info >= (3, 14):
        return inspect.signature(
            route, annotation_format=annotationlib.Format.FORWARDREF
        ).parameters
    return inspect.signature(route).parameters


def _check_removed_settings(cls: type) -> None:
    """Reject a setting that Restly no longer reads. Without this a guard in
    ``route_options`` would stop running, and nothing would say so."""
    for klass in cls.__mro__:
        for name, instead in _REMOVED_SETTINGS.items():
            if name in vars(klass):
                raise RestlyConfigurationError(
                    f"{cls.__name__} sets {name}{_origin(cls, klass)}, which was "
                    f"removed. {instead}"
                )


def _check_renamed_names(cls: type) -> None:
    """Reject a setting, a method or a route parameter that still uses a name
    from before the rename. Runs when the class is defined and again at
    registration, so a value assigned to the class later is caught too."""
    _check_removed_settings(cls)
    for klass in cls.__mro__:
        origin = _origin(cls, klass)
        for old, new in _RENAMED_SETTINGS.items():
            if old in vars(klass):
                raise RestlyConfigurationError(
                    f"{cls.__name__} sets {old}{origin}, which was renamed to "
                    f"{new}. Rename the setting."
                )
        for old, new in _RENAMED_METHODS.items():
            if old in vars(klass):
                raise RestlyConfigurationError(
                    f"{cls.__name__} defines {old}{origin}, which was renamed "
                    f"to {new}.{_RENAMED_METHOD_HINTS.get(old, '')} Rename the "
                    "method."
                )
        for name, instead in _REMOVED_METHODS.items():
            if name in vars(klass):
                raise RestlyConfigurationError(
                    f"{cls.__name__} defines {name}{origin}, which was removed. "
                    f"{instead}"
                )
        for name, value in vars(klass).items():
            if not hasattr(value, "_api_route_args"):
                continue
            if "query_params" in _route_parameters(value):
                raise RestlyConfigurationError(
                    f"{cls.__name__}.{name}{origin} declares a query_params "
                    "parameter, which was renamed to list_params. Rename the "
                    "parameter, so the route still takes the filter, sort and "
                    "page parameters."
                )
