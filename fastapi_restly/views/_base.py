"""
This module provides a framework for class-based views on SQLAlchemy models.

View class:
This class is used to create a collection of endpoints that share an
APIRouter (created when calling `include_view()`) and dependencies
as class attributes. It uses the same mechanics as the class based
view decorator from fastapi-utils.
(https://fastapi-utils.davidmontague.xyz/user-guide/class-based-views/)

AsyncRestView:
Provides default reading and writing functions on the database using
SQLAlchemy models.
"""

import collections.abc
import dataclasses
import functools
import inspect
import itertools
import types
import warnings
from collections.abc import Iterable, Mapping
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Callable,
    ClassVar,
    Generic,
    Iterable,
    Literal,
    Sequence,
    cast,
    final,
    get_args,
    get_origin,
)

import fastapi
import pydantic
from fastapi.utils import is_body_allowed_for_status_code
from sqlalchemy import Boolean, ColumnElement, Select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select as sa_select
from starlette.datastructures import QueryParams
from typing_extensions import TypeVar

from ..clauses import WhereClause, all_of, apply_clauses

#: A per-read scope: ``None`` for the view's own, a clause that replaces it
#: for that read, or ``fr.clauses.UNSCOPED``.
ReadScope = WhereClause | None

# A per-read narrowing filter: a SQLAlchemy boolean expression, or anything
# all_of takes. Private: only the final list handler accepts one, so no
# override spells it.
_ReadWhere = ColumnElement[bool] | WhereClause | None
from .._mapping import is_mapped_class
from .._pagination import (
    _DEFAULT_PAGINATION,
    Pagination,
    _build_envelope,
    _check_fillable,
    _query_settings,
    _settings_of,
)
from ..exc import NotFound, RestlyConfigurationError, RestlySkippedFilterMethodsWarning
from ..objects import snapshot as _object_snapshot
from ..query._impl import _list_params_values, _OwnFilter, _record_of
from ..query._paged import (
    _PAGINATION_OF_BASE,
    _page_fields_base,
    _redeclared_page_fields,
    _takes_page_fields,
)
from ..query._shared import SortNulls
from ..schemas import BaseSchema
from ..schemas._base import (
    _as_response,
    _reject_table_class,
    _reject_table_subclass,
    _reject_writable_table_class,
    _response_adapter,
    _schema_resource_name,
)
from ._generated import (
    _DEFAULT_SETTINGS,
    _drop_stale_settings,
    _list_params_schema,
    _uses_default,
)
from ._guards import (
    _FINAL_HANDLERS,
    _FINAL_LOADS,
    _REMOVED_DOMAIN_UTILITIES,
    _check_atomic_write_actions,
    _check_list_settings,
    _check_load_hooks,
    _check_pagination_setting,
    _check_renamed_names,
    _check_unknown_query_params,
    _final_handler_message,
    _missing_view_attribute,
    _origin,
    _owner,
    _reject_bare_verb_route_names,
    _reject_route_names_of_settings,
    _route_parameters,
    _set_by,
)
from ._list_params import (
    _LIST_SETTING_DEFAULTS,
    _how_set,
    _list_filter_methods,
    _list_params_dependency,
    _list_params_fields,
    _list_setting,
    _validate_raw_list_params,
    reject_unknown_query_keys,
)
from ._reads import (
    _build_relationship_loader_options,
    _check_identity_tables,
    _checked_scope,
    _checked_where,
    _clause_element,
    _model_scope,
    _not_found_message,
    _primary_key_criterion,
    _reject_bad_row_identity,
    _rows_reload_statements,
)
from ._schema_types import (
    SchemaType,
    _bare,
    _fill_schema_types,
    _schema_type_in,
    _without_none,
)

# Unbound: a mapped class need not subclass DeclarativeBase (a SQLModel table,
# an imperative mapping). ``BaseRestView`` checks the mapping at class definition.
ModelT = TypeVar("ModelT", default=Any)
ResponseSchemaT = TypeVar(
    "ResponseSchemaT", bound=pydantic.BaseModel, default=BaseSchema
)
CreateSchemaT = TypeVar(
    "CreateSchemaT", bound=pydantic.BaseModel, default=pydantic.BaseModel
)
UpdateSchemaT = TypeVar(
    "UpdateSchemaT", bound=pydantic.BaseModel, default=pydantic.BaseModel
)
IdT = TypeVar("IdT", default=int)


@dataclasses.dataclass(frozen=True)
class ListResult(Generic[ModelT]):
    """Result returned by ``get_many`` before HTTP response formatting.

    ``total_count`` is ``None`` for a list without pagination, which does not
    run the count query. ``list_params`` holds the list params that
    ``get_many`` received, so :meth:`BaseRestView.to_list_response` can read
    the page from them.
    """

    #: The rows that the list loaded: one page, or every row for a list
    #: without pagination.
    objects: Sequence[ModelT]
    #: The number of rows that match the filters, on all pages. It is
    #: ``None`` for a list without pagination. A paginated list response
    #: sends ``None`` as ``0``.
    total_count: int | None = None
    #: The list params that ``get_many`` received. Pass on the list params
    #: that you received: :meth:`BaseRestView.to_list_response` reads the
    #: values of the page from them, and without them it reports the first
    #: page on every page.
    list_params: Any = None


class Action:
    """The names of the five CRUD actions: the ``action`` that ``authorize``
    gets. A write, such as ``CREATE`` or the action of a ``write_action``
    block, also passes it to ``before_action_commit`` and
    ``after_action_commit``. A read, ``GET_MANY`` or ``GET_ONE``, has no
    commit, so it reaches only ``authorize``.

    Each view has a class ``Action`` that lists all its actions. It is this
    class, until a view adds actions of its own in a subclass::

        class PostView(fr.AsyncRestView):
            class Action(fr.Action):
                PUBLISH = "publish"

    An ``Action`` class has only actions. An action has an UPPER_CASE name,
    and its value is that name in lower case. ``write_action`` takes only a
    value that the view's ``Action`` declares. A view with base classes that
    declare actions, such as a mixin, inherits from their ``Action`` classes
    too.
    """

    #: Read a list, as ``GET /`` does. A read has no commit.
    GET_MANY = "get_many"
    #: Read one row, as ``GET /{id}`` does. A read has no commit.
    GET_ONE = "get_one"
    #: Create a row, as ``POST /`` does.
    CREATE = "create"
    #: Update a row, as ``PATCH /{id}`` does.
    UPDATE = "update"
    #: Delete a row, as ``DELETE /{id}`` does.
    DELETE = "delete"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for key in dir(cls):
            if key.startswith("_"):
                continue
            if not key.isupper():
                raise RestlyConfigurationError(
                    f"{cls.__qualname__}.{key}: an Action class has only "
                    "actions, and an action has an UPPER_CASE name, such as "
                    "PUBLISH = 'publish'."
                )
            value = getattr(cls, key)
            if value != key.lower():
                raise RestlyConfigurationError(
                    f"{cls.__qualname__}.{key} is {value!r}. The value of an "
                    f"action is its name in lower case: {key} = {key.lower()!r}."
                )


def _declares(action_class: type, action: object) -> bool:
    """Whether ``action_class`` declares ``action``, such as ``"publish"``."""
    return (
        isinstance(action, str)
        and getattr(action_class, action.upper(), None) == action
    )


class View:
    """
    Class-based view primitive for FastAPI.

    Group related endpoints on a class, share dependencies and metadata via
    class attributes, and let subclasses override individual route methods.
    Routes are bound at :func:`include_view` time, not at class-definition
    time, so subclassing works the way Python developers expect: override a
    method on a subclass and the override is what runs.

    Most users will subclass :class:`RestView` or :class:`AsyncRestView`,
    which extend ``View`` with CRUD scaffolding. Use ``View`` directly for
    grouped non-CRUD endpoints (auth flows, custom RPC routes, etc.).
    """

    #: The URL prefix of every route. Each class in the hierarchy that sets one
    #: adds a segment, base first. Without a prefix, the routes are at the root.
    prefix: ClassVar[str]
    #: The OpenAPI tags of every route. When it is not set, the tag is the
    #: last part of the prefix in title case, so ``/order-items`` gives
    #: ``Order Items``. Without a prefix, the tag is the class name. Setting
    #: it replaces that tag.
    tags: ClassVar[Iterable[str | Enum] | None] = None
    #: FastAPI dependencies run for every route, without injecting a result.
    #: Each class in the hierarchy adds its own, base first, so a subclass
    #: cannot drop a base's guard. A subclass that lists a base's entry again
    #: runs it where it lists it.
    dependencies: ClassVar[Any] = None
    #: OpenAPI responses documented on every route. Each class in the hierarchy
    #: adds its own, and a subclass's entry for a status code wins. A status set
    #: to ``None`` is left out, so ``{404: None}`` removes the 404 that a CRUD
    #: view documents on a route that can answer 404.
    responses: ClassVar[dict[int | str, dict[str, Any] | None]] = {}

    if TYPE_CHECKING:
        # include_view replaces __init__: each injected attribute becomes a
        # keyword, so code that builds a view by hand passes them by name.
        def __init__(self, **dependencies: Any) -> None: ...

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _apply_modified_routes(cls)
        _reject_route_names_of_settings(cls)

    @classmethod
    def before_include_view(cls) -> None:
        """Run by :func:`include_view` once per class, before its routes are
        registered. A no-op here; override to adjust route methods first."""


def route(path: str, **api_route_kwargs: Any) -> Callable[..., Any]:
    """Mark a view method as an endpoint, with the HTTP method and the route
    options that you pass.

    ``path`` and ``api_route_kwargs`` go to FastAPI's
    ``APIRouter.add_api_route()`` when :func:`include_view` registers the
    view. Without ``methods=``, the route is a ``GET`` with status 200, as in
    FastAPI. :func:`get`, :func:`post`, :func:`patch`, :func:`put` and
    :func:`delete` set the method and the status code for you.
    """

    def store_args_decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        # Create a new attribute: '_api_route_args'
        func._api_route_args = (path, api_route_kwargs)  # type: ignore[attr-defined]
        return func

    return store_args_decorator


def get(path: str, **api_route_kwargs: Any) -> Callable[..., Any]:
    """Decorator to mark a View method as a GET endpoint.

    Equivalent to::

        @route(path, methods=["GET"], status_code=200, ... )
    """
    api_route_kwargs.setdefault("methods", ["GET"])
    api_route_kwargs.setdefault("status_code", 200)
    return route(path, **api_route_kwargs)


def post(path: str, **api_route_kwargs: Any) -> Callable[..., Any]:
    """Decorator to mark a View method as a POST endpoint.

    Equivalent to::

        @route(path, methods=["POST"], status_code=201, ... )
    """
    api_route_kwargs.setdefault("methods", ["POST"])
    api_route_kwargs.setdefault("status_code", 201)
    return route(path, **api_route_kwargs)


def put(path: str, **api_route_kwargs: Any) -> Callable[..., Any]:
    """Decorator to mark a View method as a PUT endpoint.

    Equivalent to::

        @route(path, methods=["PUT"], status_code=200, ... )
    """
    api_route_kwargs.setdefault("methods", ["PUT"])
    api_route_kwargs.setdefault("status_code", 200)
    return route(path, **api_route_kwargs)


def patch(path: str, **api_route_kwargs: Any) -> Callable[..., Any]:
    """Decorator to mark a View method as a PATCH endpoint.

    Equivalent to::

        @route(path, methods=["PATCH"], status_code=200, ... )
    """
    api_route_kwargs.setdefault("methods", ["PATCH"])
    api_route_kwargs.setdefault("status_code", 200)
    return route(path, **api_route_kwargs)


def delete(path: str, **api_route_kwargs: Any) -> Callable[..., Any]:
    """Decorator to mark a View method as a DELETE endpoint.

    Equivalent to::

        @route(path, methods=["DELETE"], status_code=204, ... )
    """
    api_route_kwargs.setdefault("methods", ["DELETE"])
    api_route_kwargs.setdefault("status_code", 204)
    return route(path, **api_route_kwargs)


@dataclasses.dataclass(frozen=True, eq=False)
class _ModifiedRoute:
    """What :func:`modify_route` returns. When the class is defined, a copy of
    the inherited route takes its place."""

    path: str | None
    api_route_kwargs: dict[str, Any]


def modify_route(path: str | None = None, **api_route_kwargs: Any) -> Any:
    """Change the options of a route that the view inherits, and keep the rest.

    Use it in the class body, under the name of the route method::

        class TagView(fr.AsyncRestView):
            prefix = "/tags"
            model = Tag

            create_endpoint = fr.modify_route(status_code=200, name="create_tag")
            delete_endpoint = fr.modify_route(dependencies=[Depends(require_admin)])

    It takes the arguments of a route decorator, such as :func:`post`: they go
    to FastAPI's ``APIRouter.add_api_route()``. ``path`` replaces the path of
    the route.

    Each argument replaces the one of the inherited route. ``dependencies`` and
    ``responses`` add up, as they do on a view. ``dependencies`` run base first.
    ``responses`` add up per status code, and a status code set to ``None`` is
    left out. A status code without a body, such as 204, also removes the
    response model of the route.

    ``response_model`` raises
    :class:`~fastapi_restly.exc.RestlyConfigurationError` when the view
    registers, on a route that answers with the view's response. Such a route
    builds its response with ``to_single_response`` or ``to_list_response``,
    and another response model would skip that step. Set ``response_schema``
    or ``list_response_schema``, or write the route method.

    This changes the route of this view and its subclasses. The route of the
    base class stays the same.

    A name that is not an inherited route raises
    :class:`~fastapi_restly.exc.RestlyConfigurationError` when the class is
    defined.
    """
    return _ModifiedRoute(path, api_route_kwargs)


def _apply_modified_routes(cls: type) -> None:
    """Put a copy of the inherited route in the place of each
    :func:`modify_route` that ``cls`` gets.

    So the attribute is a normal route for the rest of Restly, and
    ``super().create_endpoint(...)`` finds a function. A plain mixin holds its
    options until a view class takes them, so this reads every class in the
    MRO: for each name, the options in front of the first method.
    """
    names = {
        name
        for klass in cls.__mro__
        for name, value in vars(klass).items()
        if isinstance(value, _ModifiedRoute)
    }
    for name in sorted(names):
        owners = [klass for klass in cls.__mro__ if name in vars(klass)]
        values = [vars(klass)[name] for klass in owners]
        options = list(
            itertools.takewhile(lambda v: isinstance(v, _ModifiedRoute), values)
        )
        for index in range(len(options), len(values)):
            later = values[index + 1 :]
            if isinstance(values[index], _ModifiedRoute) and not any(
                hasattr(value, "_api_route_args") for value in later
            ):
                raise RestlyConfigurationError(
                    f"{cls.__name__}.{name}: the fr.modify_route of "
                    f"{owners[index].__name__} changes nothing, because "
                    f"{owners[index].__name__} comes after the class with the "
                    f"route. Put {owners[index].__name__} before the view "
                    f"class in the bases of {cls.__name__}."
                )
        if not options:
            continue
        endpoint = values[len(options)] if len(values) > len(options) else None
        if not hasattr(endpoint, "_api_route_args"):
            raise RestlyConfigurationError(
                f"{cls.__name__}.{name} uses fr.modify_route, but the class "
                f"inherits no route method with this name. Use the name of a "
                f"route method, such as create_endpoint."
            )
        path, route_kwargs = endpoint._api_route_args  # type: ignore[union-attr]
        route_kwargs = dict(route_kwargs)
        dropped = getattr(endpoint, "_dropped_response_model", None)
        if dropped is not None:
            route_kwargs.pop("response_model", None)
            route_kwargs.update(dropped)
        for option in reversed(options):
            path = path if option.path is None else option.path
            for key, value in option.api_route_kwargs.items():
                if key == "dependencies":
                    value = _added_dependencies(route_kwargs.get(key), value)
                elif key == "responses":
                    value = {**(route_kwargs.get(key) or {}), **(value or {})}
                route_kwargs[key] = value
        # The inherited response model belongs to a status with a body, and
        # FastAPI refuses a response model on a status without one. A subclass
        # with a status with a body gets the model back, see above. A model
        # that an option names stays, so FastAPI refuses it.
        dropped = None
        if not any("response_model" in option.api_route_kwargs for option in options):
            if not is_body_allowed_for_status_code(route_kwargs.get("status_code")):
                dropped = {
                    key: value
                    for key, value in route_kwargs.items()
                    if key == "response_model"
                }
                route_kwargs["response_model"] = None
        copied = _copy_route(endpoint, cls, name)
        copied._api_route_args = (path, route_kwargs)  # type: ignore[attr-defined]
        copied._dropped_response_model = dropped  # type: ignore[attr-defined]
        copied._from_modify_route = True  # type: ignore[attr-defined]
        if any("response_model" in option.api_route_kwargs for option in options):
            # registration refuses it if this route answers with the view's response
            copied._response_model_of = getattr(  # type: ignore[attr-defined]
                endpoint, "_response_model_of", endpoint
            )
        # Delete first: in the class dict, a name keeps its first place. The routes
        # that the class writes come first, see the copy of the inherited routes.
        if name in vars(cls):
            delattr(cls, name)
        setattr(cls, name, copied)


def _added_dependencies(dependencies: Any, new: Any) -> list[Any]:
    """``dependencies``, then ``new``. A dependency that ``new`` lists again
    moves to where ``new`` lists it, so what comes before it still runs first."""
    added = list(dependencies or ())
    for dependency in new or ():
        added = [seen for seen in added if seen is not dependency]
        added.append(dependency)
    return added


#: The routes that load one row by the id in the path.
_ITEM_ROUTES = ("get_one_endpoint", "update_endpoint", "delete_endpoint", "put")


def _check_composite_key(cls: Any) -> None:
    """Refuse item routes on a model with a composite primary key and the
    default ``where_id``: an id from the path cannot name one row."""
    if len(sa_inspect(cls.model).primary_key) < 2:
        return
    if getattr(cls.where_id, "__module__", "").startswith("fastapi_restly."):
        routes = [name for name in _ITEM_ROUTES if _serves_route(cls, name)]
        if routes:
            raise RestlyConfigurationError(
                f"{cls.__name__}: {cls.model.__name__} has a composite primary "
                f"key, so an id from the path cannot name one row, and the "
                f"item routes ({', '.join(routes)}) need it. Set "
                "id_type = str, and override where_id(id): split the id, and "
                "return sqlalchemy.and_(Model.a == a, Model.b == b). Or leave "
                "the item routes out, such as get_one_endpoint = None."
            )


def _check_actions(cls: type) -> None:
    """Refuse a view whose ``Action`` does not inherit from the ``Action`` of
    a base class.

    Each class that declares ``Action`` adds actions, and a view's ``Action``
    must have all of them: the view's own class, or a mixin's, or two bases
    that each declare one, where Python takes the first and drops the other.
    """
    action = cls.Action  # type: ignore[attr-defined]
    declared: list[tuple[type, Any]] = []
    for klass in cls.__mro__:
        if "Action" not in vars(klass):
            continue
        value = vars(klass)["Action"]
        if all(value is not seen for _, seen in declared):
            declared.append((klass, value))
    for klass, value in declared:
        if not (isinstance(value, type) and issubclass(value, Action)):
            raise RestlyConfigurationError(
                f"{cls.__name__}.Action{_origin(cls, klass)} does not inherit "
                "from fr.Action. Declare it as class Action(fr.Action)."
            )
    missing = [
        (klass, base) for klass, base in declared if not issubclass(action, base)
    ]
    if not missing:
        return
    # the classes to inherit from: the bases' Action classes, without one
    # that another of them already inherits from
    bases = [(klass, base) for klass, base in declared if klass is not cls]
    keep = [
        _action_class_name(klass, base)
        for klass, base in bases
        if not any(other is not base and issubclass(other, base) for _, other in bases)
    ]
    raise RestlyConfigurationError(
        f"{cls.__name__}.Action{_set_by(cls, 'Action')} does not inherit from "
        f"{', '.join(_action_class_name(klass, base) for klass, base in missing)}. "
        "A view's Action has all the actions of its bases. "
        f"Declare it on {cls.__name__} with every base: "
        f"class Action({', '.join(keep)})."
    )


def _action_class_name(klass: type, action: type) -> str:
    """How a message names the ``Action`` that ``klass`` declares."""
    return "fr.Action" if action is Action else f"{klass.__name__}.Action"


def _check_action_name(view: Any, action: Any) -> None:
    """Raise ``ValueError`` when ``write_action`` gets an action that the
    view's ``Action`` does not declare."""
    if _declares(type(view).Action, action):
        return
    name = type(view).__name__
    raise ValueError(
        f"{name}.write_action({action!r}, ...): unknown action {action!r}. "
        f"{name}.Action does not declare it."
    )


class BaseRestView(
    View, Generic[ModelT, ResponseSchemaT, CreateSchemaT, UpdateSchemaT, IdT]
):
    """
    The part that :class:`RestView` and :class:`AsyncRestView` share: the
    settings, the read statement and the response methods.

    It has no routes, no handlers and no business methods. Those are on
    ``RestView`` and ``AsyncRestView``, in a sync and an async form. Subclass
    this class only to write a CRUD base class of your own.
    """

    # The view's actions: the names that authorize and the transaction hooks
    # get. A view with actions of its own declares a subclass of fr.Action
    # with this name. (The docs show fr.Action, not this attribute.)
    Action = Action

    #: The main schema: the Pydantic class that lists the fields of the
    #: resource. Restly builds :attr:`response_schema`, :attr:`create_schema`
    #: and :attr:`update_schema` from it. When a view sets none, Restly
    #: generates it from :attr:`model` with
    #: :func:`~fastapi_restly.schemas.derive_schema`.
    schema: ClassVar[type[pydantic.BaseModel]] = _DEFAULT_SETTINGS["schema"]
    #: The class of everything that goes out: the single responses, the
    #: items of the list response, and the fields the list params filter and
    #: sort on. The relationships it names are the ones the view loads. When
    #: a view sets none, Restly builds it with
    #: :func:`~fastapi_restly.schemas.derive_response_schema`: the main
    #: schema without its ``WriteOnly`` fields, named ``<Resource>Response``.
    #: A class that a view sets is used as it is.
    response_schema: ClassVar[type[pydantic.BaseModel]] = _DEFAULT_SETTINGS[
        "response_schema"
    ]
    #: The class of the list response: what ``GET /`` and a route that
    #: declares :data:`~fastapi_restly.views.ListResponseSchema` send. When a
    #: view sets none, Restly builds it with
    #: :func:`~fastapi_restly.schemas.derive_list_response_schema`: the
    #: envelope of the view's :attr:`pagination`, with the response schema as
    #: its items, named ``<Resource>ListResponse``. Restly fills a class that
    #: a view sets as it fills an envelope: by field name, from the values of
    #: the view's pagination, such as ``data``, ``total_count``, ``page``,
    #: ``page_size`` and ``total_pages``. A view that replaces
    #: ``get_many_endpoint`` can set any type here.
    list_response_schema: ClassVar[Any] = _DEFAULT_SETTINGS["list_response_schema"]
    #: The class of the ``POST`` body. When a view sets none, Restly builds
    #: it from the main schema without its ``ReadOnly`` fields and without
    #: a primary key that the server generates. It is named
    #: ``<Resource>Create``. A class that a view sets is used as it is.
    create_schema: ClassVar[type[pydantic.BaseModel]] = _DEFAULT_SETTINGS[
        "create_schema"
    ]
    #: The class of the ``PATCH`` body. When a view sets none, Restly builds
    #: it from the main schema without its ``ReadOnly`` fields and without
    #: a primary key that the server generates, and makes every other field
    #: optional. It is named ``<Resource>Update``. A class that a view sets
    #: is used as it is.
    update_schema: ClassVar[type[pydantic.BaseModel]] = _DEFAULT_SETTINGS[
        "update_schema"
    ]
    #: The SQLAlchemy model that the view serves: a mapped class. A view that
    #: you register needs it. A class that is not mapped raises
    #: :class:`~fastapi_restly.exc.RestlyConfigurationError` when Python
    #: creates the view class.
    model: ClassVar[type[Any]]
    #: The clause every read on this view applies: list, count, and retrieve
    #: (a row outside it is 404). ``None`` (the default) falls back to the
    #: model's declared ``default_scope``; ``fr.clauses.UNSCOPED`` reads
    #: unscoped despite that default. Declaring a scope replaces the default,
    #: it does not stack on it; compose the replacement from the same leaves
    #: (``ItemClauses.trashed`` containing the tenant clause ``visible``
    #: contains). A rule that must hold under every scope is a session-level
    #: ``with_loader_criteria``, not a view concern (see the Multi-Tenancy
    #: guide). See the Scopes guide for scopes.
    scope: ClassVar[WhereClause | None] = None
    #: The type of the ``{id}`` path parameter on the default routes. ``None``
    #: (the default) takes the Python type of the model's primary key, and a
    #: composite key gets ``int``. Set a type to override it. Built-in item
    #: routes use Starlette's ``int`` or ``uuid`` path converter for those
    #: types. Integer paths accept non-negative digits. An unmatched value
    #: falls through to another route or returns 404, instead of 422.
    id_type: ClassVar[type[Any] | None] = None
    #: What a list route does with a query key that it does not read.
    #: ``"forbid"`` (the default) rejects the request with 422, so a typo in
    #: a filter key does not return more rows than the client asked for.
    #: ``"ignore"`` leaves the key out, as FastAPI does. The values are those of
    #: Pydantic's ``extra`` setting. The same rule applies to a ``QueryParams``
    #: that a method gets as list params.
    unknown_query_params: ClassVar[Literal["forbid", "ignore"]] = "forbid"
    #: How list endpoints paginate. The default :class:`NumberedPagination`
    #: takes ``page`` / ``page_size`` query parameters, runs the count query,
    #: and wraps the list in its ``envelope`` (:class:`NumberedEnvelope`:
    #: ``data`` plus ``total_count`` / ``page`` / ``page_size`` /
    #: ``total_pages``). An :class:`OffsetPagination` does the same with
    #: ``limit`` / ``offset`` query parameters (:class:`OffsetEnvelope`:
    #: ``data`` plus ``total_count`` / ``limit`` / ``offset``). A
    #: :class:`NoPagination` returns every matching row
    #: with no count, in its ``envelope``; ``None`` is short for
    #: ``NoPagination()``, a plain :class:`Envelope` (``data`` only). A view
    #: inherits it, so a project base view sets it once; a view that differs
    #: changes one setting with ``replace()`` on the shared settings. For a
    #: shape no envelope model can express, such as a header, replace
    #: ``get_many_endpoint`` with a matching ``response_model`` (see
    #: :class:`AsyncReactAdminView`).
    pagination: ClassVar[Pagination | None] = _DEFAULT_PAGINATION
    #: The filter keys that the list params keep, from the keys that Restly
    #: generates from :attr:`response_schema`. An entry is a key with the
    #: Python name of its field, such as ``"name__icontains"``, also for a
    #: field with an alias, or a pattern. ``*``
    #: matches any characters except ``.``, so ``"created_at__*"`` is every
    #: filter on ``created_at`` with a suffix, and ``"vendor.*"`` is every
    #: key on the fields of ``vendor``. The default, ``("*",)``, keeps the
    #: keys of the resource's own fields, and no dotted keys. ``()`` keeps
    #: none. An entry that the view sets and that matches no key fails when
    #: the view registers. So does an entry with the name in the URL of a
    #: field with an alias, also when the view inherits it.
    include_filters: ClassVar[tuple[str, ...]] = ("*",)
    #: The filter keys that the list params leave out, after
    #: :attr:`include_filters`. The entries are the same as there, but a name
    #: without a suffix leaves out the whole field: the plain key and each key
    #: with a suffix. So ``("name",)`` also removes ``name__in``.
    exclude_filters: ClassVar[tuple[str, ...]] = ()
    #: The keys that ``sort`` accepts: the Python name of a field, such as
    #: ``"name"`` or ``"vendor.name"``, or a pattern as in
    #: :attr:`include_filters`. The
    #: default, ``("*",)``, is the resource's own fields. A sort key never goes
    #: through a to-many relationship. With ``()``, the list has no ``sort``
    #: parameter.
    include_sort_keys: ClassVar[tuple[str, ...]] = ("*",)
    #: The sort keys to leave out, after :attr:`include_sort_keys`.
    exclude_sort_keys: ClassVar[tuple[str, ...]] = ()
    #: The name of the sort parameter. Set it to keep the URLs of another
    #: library, such as ``"ordering"`` for Django REST framework or
    #: ``"order_by"`` for fastapi-filter. The old name is then free for a
    #: filter key.
    sort_query_param: ClassVar[str] = "sort"
    #: The sort when the request has none, written as ``?sort=`` with the
    #: Python names of the fields, such as ``"-created_at"``. It becomes the
    #: default of the sort parameter, with the names in the URL, so OpenAPI
    #: shows it. A key must be a field of :attr:`response_schema` that a sort
    #: key could have, but :attr:`include_sort_keys` does not have to keep
    #: it. Use each key only once. ``None`` (the default) sorts by the primary
    #: key. The primary key is always the last term. A view that inherits it
    #: and does not have all its keys fails when it registers.
    default_sort: ClassVar[str | None] = None
    #: Where a sort puts NULLs: ``"last"`` (the default) or ``"first"`` in
    #: both directions, on every database. ``None`` leaves them where the
    #: database puts them, which differs: PostgreSQL puts them last in an
    #: ascending sort, SQLite and MySQL first.
    sort_nulls: ClassVar[SortNulls] = "last"
    #: The list params (filter, sort, page) as a pydantic model, generated
    #: from ``response_schema`` and ``model`` by
    #: :func:`~fastapi_restly.query.derive_list_params_schema`. Any route
    #: method on the view that declares a ``list_params`` parameter takes it:
    #: typed for FastAPI and OpenAPI, and guarded against unknown keys like
    #: ``GET /``, so a custom list route (a trash route naming its own scope)
    #: reads the same list params. The route can take other query parameters
    #: beside it.
    list_params_schema: ClassVar[type[pydantic.BaseModel]] = _DEFAULT_SETTINGS[
        "list_params_schema"
    ]

    #: Whether each write action of this view is one transaction. ``True`` (the
    #: default) refuses a ``session.commit()`` in the action, and an error undoes
    #: all of its work. ``False`` allows that commit, for code that must keep it,
    #: such as a library, or a function that a job also calls. All else stays,
    #: but the action is no longer one transaction: what is committed stays
    #: saved when a later step raises, and ``after_action_commit`` does not run
    #: then. There is no savepoint, so an error rolls back all that is not
    #: committed, also the work of the route before the action. The outermost
    #: write action decides for the actions inside it, and an outermost
    #: ``shared_write_action_commit()`` block always refuses a commit. See "Code
    #: that must commit in a write action" in the Lifecycle guide.
    atomic_write_actions: ClassVar[bool] = True

    #: The ``fastapi.Request`` of the current request, which Restly sets for
    #: each request. After :func:`include_view`, a view that you create
    #: yourself must get ``request=`` (``None`` outside a request) and
    #: ``session=``.
    request: fastapi.Request

    if not TYPE_CHECKING:
        # Hidden from type checkers, which report an unknown name themselves.

        def __getattr__(self, name: str) -> Any:
            """Name the replacement of a removed or renamed view method, so an
            old call such as ``self.make_new_object(data)`` says what to use."""
            if inspect.getattr_static(type(self), name, None) is not None:
                # the name exists: its own getter raised AttributeError, so
                # raise that error again rather than hide it
                return object.__getattribute__(self, name)
            raise AttributeError(
                _missing_view_attribute(type(self).__name__, name), name=name, obj=self
            )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _drop_stale_settings(cls)
        _reject_bare_verb_route_names(cls)
        # validate at class definition, so a raw expression fails at
        # import instead of on the first read
        scope = cls.__dict__.get("scope")
        if not (scope is None or isinstance(scope, WhereClause)):
            raise RestlyConfigurationError(
                f"{cls.__name__}.scope must be a WhereClause, "
                f"fr.clauses.UNSCOPED, or None, got {type(scope).__name__}; "
                "wrap a raw expression with where_clause()"
            )
        # the annotation accepts any class, so the mapping is checked here
        model = cls.__dict__.get("model")
        if model is not None and not is_mapped_class(model):
            label = getattr(model, "__name__", repr(model))
            raise RestlyConfigurationError(
                f"{cls.__name__}.model must be a SQLAlchemy mapped class, "
                f"got {label}, which has no mapper"
            )
        # build_query is removed; a definition would be silently dead code,
        # and dead visibility filtering is a security hole
        for klass in cls.__mro__:
            if "build_query" in vars(klass):
                origin = _origin(cls, klass)
                raise RestlyConfigurationError(
                    f"{cls.__name__} defines build_query{origin}, which is "
                    "removed and no longer called. Declare visibility as a "
                    "clause: default_scope on the model's ClauseNamespace, or "
                    "the scope attribute on the view. Reshape a list in "
                    "apply_list_params, and relationship loading in "
                    "get_loader_options. See Migrating from "
                    "build_query in the Scopes guide."
                )
        # delete_object is removed; a stale soft-delete override would be
        # silently dead code, and a dead soft delete is a hard delete
        for klass in cls.__mro__:
            if "delete_object" in vars(klass):
                origin = _origin(cls, klass)
                raise RestlyConfigurationError(
                    f"{cls.__name__} defines delete_object{origin}, which is "
                    "removed and no longer called. Override delete instead: a "
                    "soft delete flips a timestamp there. A raw row delete is "
                    "fr.objects.delete_object / async_delete_object on "
                    "self.session."
                )
        # the view's domain utilities are removed: a definition here would
        # be dead code, and nothing would say so
        for klass in cls.__mro__:
            for name, instead in _REMOVED_DOMAIN_UTILITIES.items():
                if name in vars(klass):
                    origin = _origin(cls, klass)
                    raise RestlyConfigurationError(
                        f"{cls.__name__} defines {name}{origin}, which was "
                        f"removed from the view and is no longer called. "
                        f"{instead} For another model, use "
                        f"fr.objects.{name} or async_{name}. A value that "
                        "every write path must set belongs on the model, as a "
                        "column default or a mapper event."
                    )
        # the loads are final: an override could drop the scope of a read,
        # and it was also the load of every write
        for klass in cls.__mro__:
            if klass.__module__.startswith("fastapi_restly."):
                continue
            for name, instead in _FINAL_LOADS.items():
                if name in vars(klass):
                    origin = _origin(cls, klass)
                    raise RestlyConfigurationError(
                        f"{cls.__name__} defines {name}{origin}, which is "
                        f"final. {instead}"
                    )
        _check_load_hooks(cls)
        # the handlers are final: an override re-implements the load,
        # authorize and commit order to reach something another seam gives
        # directly, and a generated route could then skip either
        for klass in cls.__mro__:
            if klass.__module__.startswith("fastapi_restly."):
                continue
            for name in _FINAL_HANDLERS:
                if name in vars(klass):
                    origin = _origin(cls, klass)
                    raise RestlyConfigurationError(
                        _final_handler_message(cls.__name__, name, origin)
                    )
        _check_pagination_setting(cls)
        _check_list_settings(cls)
        _check_unknown_query_params(cls)
        _check_atomic_write_actions(cls)
        _check_renamed_names(cls)
        _check_actions(cls)

    def _apply_scope(self, stmt: Select[Any], scope: ReadScope) -> Select[Any]:
        # the one path every read takes, so retrieve, list and count cannot
        # disagree about which rows exist; not an override point
        if scope is None:
            scope = resolve_scope(self)
        else:
            # a per-read scope replaces the resolution rather than joining it
            scope = _checked_scope(scope, type(self).__name__, "a per-read scope")
        return apply_clauses(stmt, scope)

    def _narrowed_scope(self, scope: ReadScope, where: _ReadWhere) -> ReadScope:
        # the list handler is final, so it can fold where= into the scope it
        # forwards: get_many keeps one visibility input, and an override
        # keeps its signature
        if where is None:
            return scope
        label = type(self).__name__
        if scope is None:
            scope = resolve_scope(self)
        else:
            scope = _checked_scope(scope, label, "a per-read scope")
        return all_of(scope, _checked_where(where, label))

    @final
    def select(self, *, scope: ReadScope = None) -> Select[Any]:
        """The statement of the view's rows: ``select(model)``, the scope,
        and :meth:`get_loader_options`.

        ``get_one`` and ``get_many`` start here, and so does a custom route
        that builds its own read: ``self.select().where(Item.price > 10)``.
        The rows are the ones the view shows, and each row loads what the
        response needs. Final: set ``scope`` to change the rows, and override
        :meth:`get_loader_options` to change the loads.

        :param scope: a clause that replaces the view scope for this
            statement; ``fr.clauses.UNSCOPED`` reads past it.
        """
        stmt = self._apply_scope(sa_select(self.model), scope)
        options = self.get_loader_options()
        return stmt.options(*options) if options else stmt

    def where_id(self, id: Any) -> ColumnElement[bool]:
        """The WHERE that picks the row an id from a path names.

        The default compares the primary key: ``Item.id == id``. Override
        to find the row by another key, such as a slug. Every load by id
        uses it: ``GET``, ``PATCH`` and ``DELETE /{id}``, and
        ``self.get_one(id)`` in your routes::

            def where_id(self, id: str) -> ColumnElement[bool]:
                return Recipe.slug == id

        A model with a composite primary key needs an override: set
        ``id_type = str``, split the id from the path, and return
        ``and_(...)``. The expression must name
        the view's model, not another table, and it runs no query, so define
        it with ``def``, also on an async view.
        """
        return _primary_key_criterion(self.model, id)

    def not_found(self, id: Any) -> Exception:
        """The exception for an id that names no row: a 404 by default, such
        as ``User with id 5 was not found``. The name is the resource of the
        main schema, as in the names of the schemas that Restly generates.

        ``get_one`` raises what this returns, so an override can change the
        text or the status, such as ``410`` for a row that was deleted. On
        an async view it may be ``async def``, and Restly awaits it. ``id``
        is the id from the path, or ``None`` when a route loaded by an
        expression, such as ``handle_get_one(Item.slug == slug)``.
        """
        return NotFound(_not_found_message(_schema_resource_name(self.schema), id))

    def _row_criterion(self, id: Any) -> ColumnElement[bool]:
        """The WHERE of ``get_one``: an expression that a route passes in
        place of the id, or :meth:`where_id` of the id."""
        name = type(self).__name__
        if isinstance(id, ColumnElement):
            _check_identity_tables(self.model, id, name)
            return id
        _reject_bad_row_identity(name, id)
        criterion = _clause_element(self.where_id(id))
        if not (
            isinstance(criterion, ColumnElement) and isinstance(criterion.type, Boolean)
        ):
            raise TypeError(
                f"{name}.where_id must return a SQL condition, such as "
                f"Model.slug == id, got {type(criterion).__name__}."
            )
        _check_identity_tables(self.model, criterion, f"{name}.where_id")
        return criterion

    def get_loader_options(self) -> list[Any]:
        """The loader options of every read: what each row loads.

        The default is a recursive ``selectinload(...)`` for each
        relationship that :attr:`response_schema` names. :meth:`select`
        applies them, so ``get_one``, ``get_many`` and your routes load the
        same, and so does the reload after the save of a write. Override to
        load more: a relationship that the response schema does not name, or
        a ``query_expression`` through ``with_expression(...)``. Add to
        ``super().get_loader_options()`` to keep the default loads. See
        "Return related objects in a response" on the Relationships guide
        page.
        """
        return _build_relationship_loader_options(
            self.model, type(self).response_schema
        )

    def _get_reload_statements(self, subject: Any, others: list[Any]) -> list[Any]:
        """The SELECTs that make the saved rows of a write ready for the
        hooks and the response.

        Called by the save step of the commit bracket after the flush and the
        refresh of the subject. One reload covers the subject, when the view
        has loader options, and the other rows of the view's model that the
        action wrote. So a value that only the loader options load, such as
        a relationship that ``to_single_response`` reads or a
        ``with_expression``, is there after every write, and a route that
        changes several rows returns each of them as the database has it.
        The view's scope does not apply: a row that the write moved out of
        it, such as a soft-deleted row, is still the row that the action
        returns.
        """
        rows = list(others)
        options = self.get_loader_options()
        if options and isinstance(subject, self.model):
            if sa_inspect(subject).persistent:
                rows.insert(0, subject)
        if not rows:
            return []
        return _rows_reload_statements(self.model, rows, options)

    def _reject_unknown_query_params(self) -> None:
        """Reject any query-string key that neither the list params nor the
        route reads.

        Runs before every route that declares ``list_params`` (see
        :attr:`list_params_schema`).

        The list params dependency (:func:`_list_params_dependency`) reads
        their keys and ignores any other, which would let typoed
        filters or unsupported operators (e.g.
        ``active__gte=true`` on a boolean column where the schema does not
        emit a range operator) widen the result set without telling the
        caller. We treat unknown keys as a validation error instead, mirroring
        FastAPI's 422 envelope shape so the response is consistent with
        bound-violation errors, unless :attr:`unknown_query_params` is
        ``"ignore"``.

        No-op when there is no live request, as when code calls a route
        method directly, outside an HTTP request. Then there is no URL to
        check, and the caller is responsible for what it passes.
        """
        request = getattr(self, "request", None)
        if request is None:
            return
        params_model = _list_params_schema(type(self))
        list_keys = {key for key, _, _ in _list_params_fields(params_model)}
        self._reject_unknown_keys(request.query_params, list_keys)

    def _reject_unknown_keys(self, query: QueryParams, allowed: set[str]) -> None:
        """Raise a 422 for a key of ``query`` that is not in ``allowed``, and
        that the route does not read.

        Does nothing when :attr:`unknown_query_params` is ``"ignore"``.
        """
        if self.unknown_query_params == "ignore":
            return
        reject_unknown_query_keys(query, allowed, getattr(self, "request", None))

    def _validated_list_params(self, list_params: Any) -> Any:
        """``list_params`` as the list route gives them.

        A ``QueryParams`` or a mapping goes through :attr:`list_params_schema`
        first, as on the list route. A ``QueryParams`` is the query of a
        request, so it can also hold keys that the route reads. Those are
        left out, and any other key is unknown, as on the list route (see
        :attr:`unknown_query_params`). A mapping holds list params only, so
        a key that they do not have is an error. Anything else is list
        params already, such as a Pydantic model, which is validated. A view
        that is not registered gets the list params that registration would
        set.
        """
        if not isinstance(list_params, Mapping):
            return list_params
        params_model = _list_params_schema(type(self))
        if isinstance(list_params, QueryParams):
            keys = {key for key, _, _ in _list_params_fields(params_model)}
            self._reject_unknown_keys(list_params, keys)
            list_params = QueryParams(
                [
                    (key, value)
                    for key, value in list_params.multi_items()
                    if key in keys
                ]
            )
        return _validate_raw_list_params(params_model, list_params)

    def _filter_method_values(self, list_params: Any) -> list[tuple[_OwnFilter, Any]]:
        """The filter methods to call for ``list_params``, each with its value.

        The class of ``list_params`` records its filter methods. Other list
        params, such as a Pydantic model of your own, record none, so the
        view's filter methods do not run. A warning then names them.
        """
        record = _record_of(type(list_params))
        params_model = _list_params_schema(type(self))
        if type(list_params) is not params_model:
            own = _record_of(params_model)
            skipped = [
                own_filter.method
                for own_filter in (own.own_filters if own else ())
                if record is None or own_filter not in record.own_filters
            ]
            if skipped:
                methods = "methods" if len(skipped) > 1 else "method"
                verb = "do" if len(skipped) > 1 else "does"
                warnings.warn(
                    f"{type(self).__name__} got list params of the class "
                    f"{type(list_params).__name__}, so its filter {methods} "
                    f"{', '.join(skipped)} {verb} not run. Only an instance of "
                    "its list_params_schema runs the filter methods. Pass one, "
                    "or pass a dict or a QueryParams, which Restly validates "
                    "through list_params_schema.",
                    RestlySkippedFilterMethodsWarning,
                    stacklevel=3,
                )
        return record.own_filter_values(list_params) if record else []

    def to_single_response(self, obj: ModelT | ResponseSchemaT) -> ResponseSchemaT:
        """Serialize one ORM object to the view's :attr:`response_schema`.

        By default the response schema is the main schema without its
        WriteOnly fields, named like ``UserResponse``. An override may return
        an instance of another model, such as :attr:`schema`. Only the fields
        of the response schema go out. Restly copies the values of an instance
        of a subclass of the response schema, and of :attr:`schema` when
        Restly derived the response schema, without validating them again. It
        validates any other model into the response schema from its
        attributes.

        The view loads only the relationships that the response schema names.
        An override that builds :attr:`schema` from the ORM object also reads
        the relationships of its WriteOnly fields. On an async view that fails
        with ``MissingGreenlet``: add them in
        :meth:`get_loader_options`, or build the response schema
        instead.

        The ORM path below validates through the response schema, so a view's
        main schema that declares a WriteOnly field the ORM object doesn't carry
        (e.g. ``password`` backed by a ``password_hash`` column) doesn't fail
        response validation. It validates in lax mode, also for a strict
        class, so a strict ``float`` field reads the text ``"NaN"`` that a
        JSON column stores.
        """
        response_schema = type(self).response_schema
        response = _as_response(response_schema, obj)
        if isinstance(response, response_schema):
            return cast(ResponseSchemaT, response)

        # Build a payload of raw attribute values keyed by schema field name;
        # re-validating it below serializes each field through its own type. The
        # response schema's ``from_attributes`` config resolves nested schemas and
        # reference types (IDRef/IDSchema) straight from the ORM rows, with no
        # view-layer special-casing. Alias rendering happens when FastAPI
        # serializes the response model.
        payload: dict[str, Any] = {}
        for field_name, field_info in response_schema.model_fields.items():
            if hasattr(obj, field_name):
                payload[field_name] = getattr(obj, field_name)
            elif field_info.alias and hasattr(obj, field_info.alias):
                payload[field_name] = getattr(obj, field_info.alias)

        # Lax mode, also for a strict class, as a PydanticJSON column reads
        # its document: a JSON column holds the text "NaN" for a float.
        adapter = _response_adapter(response_schema)
        return cast(
            ResponseSchemaT,
            adapter.validate_python(
                payload, by_alias=False, by_name=True, strict=False
            ),
        )

    def _single_response(self, obj: Any) -> pydantic.BaseModel:
        """One object as an instance of the response schema. An override of
        :meth:`to_single_response` may return an instance of another model."""
        return _as_response(type(self).response_schema, self.to_single_response(obj))

    def to_list_response(self, list_result: ListResult[ModelT]) -> Any:
        """Build the list response body: an instance of
        :attr:`list_response_schema`.

        By default this class is the ``envelope`` of the view's
        :attr:`pagination`: a :class:`NumberedEnvelope` (``data`` plus
        ``total_count`` / ``page`` / ``page_size`` / ``total_pages``), an
        :class:`OffsetEnvelope` (``data`` plus ``total_count`` / ``limit`` /
        ``offset``), or an :class:`Envelope` (``data`` only) for a view
        without pagination. To
        change the shape, set :attr:`list_response_schema` or the pagination's
        ``envelope``. A list type, such as ``list[UserSummary]``, gives a list
        without the page values. For a ``Content-Range`` header, see
        :class:`AsyncReactAdminView`: it overrides this method and sets
        :attr:`list_response_schema` to match.

        The values of the page come from ``list_result.list_params``.
        """
        data = [self._single_response(obj) for obj in list_result.objects]
        pagination = _settings_of(self.pagination)
        envelope = type(self).list_response_schema
        if not _is_pydantic_class(envelope):
            return _type_adapter(envelope).validate_python(
                data, from_attributes=True, by_alias=False, by_name=True
            )
        values = (
            _list_params_values(list_result.list_params, pagination=self.pagination)
            if pagination._has_pages
            else {}
        )
        return _build_envelope(
            envelope, pagination._page_info(data, list_result.total_count or 0, values)
        )

    def snapshot(self, obj: Any) -> dict[str, Any]:
        """The column values that are loaded on ``obj``, passed as ``old`` to
        ``before_action_commit`` and ``after_action_commit`` to compare old
        and new values.

        The default is :func:`~fastapi_restly.objects.snapshot`: a shallow
        copy, which shares a mutable value, such as a JSON dict, with the
        object. Override it to change what ``old`` holds, for example the
        state of a relationship before the change.
        """
        return _object_snapshot(obj)

    @classmethod
    def _check_schema_settings(cls) -> None:
        """Each schema setting holds a Pydantic class, or a function that gives
        one. Another value would fail only on a request, or not at all."""
        for name in _DEFAULT_SETTINGS:
            value = getattr(cls, name)
            marker = _schema_type_in(value)
            if marker is not None:
                raise RestlyConfigurationError(
                    f"{cls.__name__}.{name} is {value!r}, which uses {marker!r}. "
                    "A setting cannot hold a marker, as Restly fills a marker in "
                    "a route. Write a function: "
                    f"{name} = SchemaType(lambda view: ...), and read "
                    "view.response_schema in it."
                )
            if name == "list_response_schema":
                continue  # any type, see _check_list_response_schema
            if _is_pydantic_class(value):
                where = f"{cls.__name__}.{name}"
                if name == "schema":
                    _reject_table_class(
                        value,
                        where,
                        "Leave `schema` out, and Restly generates it from the "
                        "model. Or set a class without `table=True`.",
                    )
                elif name in ("create_schema", "update_schema"):
                    _reject_table_class(
                        value,
                        where,
                        f"Remove {name}, and Restly generates it from the `schema` "
                        "of the view. Or set a class without `table=True`.",
                    )
                continue
            if isinstance(inspect.getattr_static(cls, name), SchemaType):
                raise RestlyConfigurationError(
                    f"The SchemaType function of {cls.__name__}.{name} must "
                    f"return a Pydantic class, but it returned {value!r}. A route "
                    "can use another type: put the SchemaType in the annotation "
                    "of the route."
                )
            raise RestlyConfigurationError(
                f"{cls.__name__}.{name} must be a Pydantic class or a "
                f"fastapi_restly.views.SchemaType, got {value!r}."
            )

    @classmethod
    def _check_write_schemas(cls) -> None:
        """A write schema that a route of the view takes holds no SQLModel table
        class in a field that a client can write."""
        for name, routes in _WRITE_SCHEMA_ROUTES:
            if any(_serves_route(cls, route) for route in routes):
                _reject_writable_table_class(
                    getattr(cls, name), f"{cls.__name__}.{name}"
                )

    @classmethod
    def _check_table_subclasses(cls) -> None:
        """No schema of the view holds a subclass of a SQLModel table class, as
        OpenAPI would show that class with no required fields."""
        for name in _DEFAULT_SETTINGS:
            if name != "list_params_schema":
                _reject_table_subclass(getattr(cls, name), f"{cls.__name__}.{name}")

    @classmethod
    def _check_list_response_schema(cls) -> None:
        """A class that the default ``GET /`` cannot fill fails here, and not
        on the first request."""
        if _uses_default(cls, "list_response_schema"):
            return
        label = f"{cls.__name__}.list_response_schema"
        value = cls.list_response_schema
        if value is None:
            raise RestlyConfigurationError(
                f"{label} is None. It must be a type, such as "
                "NumberedEnvelope[UserSummary] or list[UserSummary]."
            )
        if _has_items_without_a_type(value):
            raise RestlyConfigurationError(
                f"{label} is the generic class {value.__name__} without a type for "
                "its items, so OpenAPI would show items without a type. Write "
                f"{value.__name__}[UserSummary]."
            )
        # A view that replaces what fills the class decides what the class is.
        endpoint = getattr(cls, "get_many_endpoint", None)
        if (
            endpoint is None
            or not endpoint.__module__.startswith("fastapi_restly.")
            or not cls.to_list_response.__module__.startswith("fastapi_restly.")
            or not _serves_route(cls, "get_many_endpoint")
        ):
            return
        other_shape = " For another shape, replace get_many_endpoint."
        if _is_pydantic_class(value):
            sample = _settings_of(cls.pagination)._page_info_sample()
            _check_fillable(label, value, sample, other_shape)
        elif get_origin(value) not in (list, collections.abc.Sequence):
            raise RestlyConfigurationError(
                f"{label} must be a Pydantic class or a list type for the default "
                f"GET /, got {value!r}.{other_shape}"
            )
        item = _list_item_annotation(value)
        if item is not None and item is not Any and _list_item_class(value) is None:
            raise RestlyConfigurationError(
                f"The items of {label} must be a Pydantic class for the default "
                f"GET /, got {item!r}. Restly fills the items from the response "
                f"class.{other_shape}"
            )
        response = cls.response_schema
        lacking = _fields_the_items_lack(value, response)
        if lacking:
            raise RestlyConfigurationError(
                f"The items of {label} have fields that {response.__name__} does "
                f"not have: {', '.join(lacking)}. Restly fills the items from the "
                f"response schema, so add these fields to it.{other_shape}"
            )

    @classmethod
    def _check_declared_list_params_schema(cls) -> None:
        """Reject declared list params that do not follow the view's settings.

        Restly generates list params from the pagination, the list settings
        and the filter methods. A subclass that changes one of them gets new
        list params only when the declared ones are what Restly would
        generate for the class that declares them (see
        ``_generated_for``). Otherwise the declared list params stay, and
        must fit the view. List params from ``derive_list_params_schema``
        must have the view's settings: other settings give other keys than
        the view says. Hand-written list params take no settings and no
        filter methods, and have the page fields of the view's pagination.
        """
        declared = _DeclaredListParams(cls, _owner(cls, "list_params_schema"))
        record = _record_of(cls.list_params_schema)
        if record is None:
            declared.check_hand_written()
            declared.check_pages()
            declared.check_keys()
        else:
            declared.check_derived(record)

    @classmethod
    def before_include_view(cls):
        """
        Apply type annotations needed for FastAPI, before creating an APIRouter from
        this view and registering it.

        This function can be overridden to further tweak the endpoints before they
        are added to FastAPI.
        """
        _check_pagination_setting(cls)
        _check_renamed_names(cls)

        if not hasattr(cls, "model"):
            if _uses_default(cls, "schema"):
                raise ValueError(
                    f"'{cls.__name__}.model' must be specified to auto-generate schema"
                )
            if _uses_default(cls, "list_params_schema"):
                raise ValueError(
                    f"'{cls.__name__}.model' must be specified: it is needed to "
                    "generate the list params."
                )
        cls._check_schema_settings()
        cls._check_write_schemas()
        cls._check_table_subclasses()
        cls._check_list_response_schema()
        if not _uses_default(cls, "list_params_schema"):
            cls._check_declared_list_params_schema()

        # Every route that declares ``list_params`` takes the view's list
        # params: ``GET /`` and any custom list route alike, guarded the same
        # way. The guard checks keys against ``list_params_schema`` whatever
        # the annotation says, so they replace an explicit annotation too. A
        # dependency reads them rather than a FastAPI query model, which
        # collapses next to any other query parameter (see
        # :func:`_list_params_dependency`).
        params_model = _list_params_schema(cls)
        list_params = Annotated[
            params_model, fastapi.Depends(_list_params_dependency(params_model))
        ]
        for name, route in list(cls.__dict__.items()):
            if not hasattr(route, "_api_route_args"):
                continue
            if "list_params" in _route_parameters(route) and not getattr(
                route, "_fr_list_guard", False
            ):
                route = _guard_list_params(route)
                setattr(cls, name, route)
            _fill_schema_types(route, cls, list_params)
        _check_composite_key(cls)


#: Whether the list params that a class declares are the ones that Restly
#: would generate for it.
@dataclasses.dataclass(frozen=True)
class _DeclaredListParams:
    """The checks of the list params that a view declares, and the words
    that their messages share."""

    cls: type
    #: The class that declares the list params: the view or a base class.
    declarer: type

    @property
    def origin(self) -> str:
        """`` (from AppView)`` when a base class declares the list params."""
        return _origin(self.cls, self.declarer)

    @property
    def its(self) -> str:
        return f"its list_params_schema{self.origin}"

    @property
    def out(self) -> str:
        """What to leave out to have the list params generated, after
        "Leave" or "leave"."""
        of = "" if self.declarer is self.cls else f" of {self.declarer.__name__}"
        return f"list_params_schema out{of} to have it generated"

    def below(self, setter: type) -> bool:
        """Whether ``setter`` is a subclass of the declaring class."""
        mro = self.cls.__mro__
        return mro.index(setter) < mro.index(self.declarer)

    def remove(self, name: str, setter: type) -> str:
        if setter is self.cls:
            return "Remove the setting"
        return f"Remove {name} from {setter.__name__}"

    def check_hand_written(self) -> None:
        """Hand-written list params take no filter methods and no list
        settings."""
        for name, method in _list_filter_methods(self.cls).items():
            raise self._filter_method_error(
                name, method._fr_list_filter, "is written by hand"
            )
        for name, default in _LIST_SETTING_DEFAULTS.items():
            if _list_setting(self.cls, name) != default:
                raise self._setting_error(
                    name,
                    "is written by hand, so Restly cannot apply the setting",
                    expected=default,
                    on_declarer=self.remove(name, self.declarer),
                )

    def check_pages(self) -> None:
        """Hand-written list params of a view with pages have the page fields
        of its pagination, or the list does not page while its response says
        that it has pages. The same holds for a page size or a limit with the
        default ``None``. ``fr.query.PagedListParams`` and
        ``fr.query.OffsetListParams`` get the fields from the pagination, and
        a subclass cannot declare them again. A view that builds its list
        response itself, as a react-admin view does, pages in its own way."""
        list_params = self.cls.list_params_schema
        pagination = getattr(self.cls, "pagination", None)
        base = _page_fields_base(list_params)
        if base is not None:
            needed = type(_PAGINATION_OF_BASE[base]).__name__
            if not _takes_page_fields(list_params, pagination):
                raise RestlyConfigurationError(
                    f"{self.cls.__name__}.list_params_schema{self.origin} is a "
                    f"fr.query.{base.__name__}, which needs a fr.{needed}, but the "
                    f"pagination of the view is {pagination!r}. Set a fr.{needed}, "
                    "or let the list params subclass pydantic.BaseModel."
                )
            for name in _redeclared_page_fields(list_params, base):
                raise RestlyConfigurationError(
                    f"{self.cls.__name__}.list_params_schema{self.origin} declares "
                    f"the field {name!r} again. A view gives this field the settings "
                    "of its pagination, so your default and bounds would have no "
                    f"effect. Set them on the fr.{needed} of the view, or let the "
                    "list params subclass pydantic.BaseModel."
                )
            return
        settings = _settings_of(pagination)
        if not settings._has_pages:
            return
        if self.cls.to_list_response is not BaseRestView.to_list_response:
            return
        keys = {key for key, _, _ in _list_params_fields(list_params)}
        missing = [name for name in settings._query_params() if name not in keys]
        if missing:
            fields = " and no ".join(repr(name) for name in missing)
            raise RestlyConfigurationError(
                f"{self.cls.__name__}.list_params_schema{self.origin} has no "
                f"{fields} field, so the list would not page while its response "
                f"says that it has pages. {settings._own_list_params_advice}"
            )
        limit_param = settings._row_limit_query_param()
        for key, field, _ in _list_params_fields(list_params):
            if key == limit_param and field.default is None:
                raise RestlyConfigurationError(
                    f"{self.cls.__name__}.list_params_schema{self.origin} gives the "
                    f"field {key!r} the default None, so a request without it would "
                    "return every row while its response says that it has pages. "
                    "Give the field a number as its default. "
                    f"{settings._own_list_params_advice}"
                )

    def check_keys(self) -> None:
        """Each field of hand-written list params has its own query key. One
        value would fill the fields of a shared key, and OpenAPI would show the
        key twice, which it does not allow."""
        keys = [key for key, _, _ in _list_params_fields(_list_params_schema(self.cls))]
        for key in keys:
            if keys.count(key) > 1:
                raise RestlyConfigurationError(
                    f"{self.cls.__name__}.list_params_schema{self.origin} has more "
                    f"than one field with the query key {key!r}. One value would "
                    "fill them all, and OpenAPI would show the key twice. Rename a "
                    "field, or the parameter of the pagination."
                )

    def check_derived(self, record: Any) -> None:
        """List params from ``derive_list_params_schema`` have the view's
        filter methods, list settings and pagination: other ones give other
        keys than the view says."""
        recorded = [(own.key, own.method) for own in record.own_filters]
        methods = [
            (method._fr_list_filter, name)
            for name, method in _list_filter_methods(self.cls).items()
        ]
        for key, name in methods:
            if (key, name) not in recorded:
                raise self._filter_method_error(name, key, "was created without it")
        others = [key for key, name in recorded if (key, name) not in methods]
        if others:
            # the list params of another view, with its filter methods
            raise RestlyConfigurationError(
                f"{self.cls.__name__}.list_params_schema{self.origin} has the "
                "keys of filter methods that the view does not have: "
                f"{', '.join(others)}. Leave {self.out}."
            )
        for name in _LIST_SETTING_DEFAULTS:
            value = _list_setting(self.cls, name)
            expected = getattr(record, name)
            if value != expected:
                raise self._setting_error(
                    name,
                    f"was created with {name}={expected!r}. A view's settings "
                    "must be the ones that its list params were created with",
                    expected=expected,
                    on_declarer=f"Create it with {name}={value!r}",
                )
        if _query_settings(record.pagination) != _query_settings(self.cls.pagination):
            setter = _owner(self.cls, "pagination")
            if self.below(setter):
                fix = (
                    f"{self.remove('pagination', setter)}, or declare "
                    f"list_params_schema on {setter.__name__} as well, created "
                    "with its pagination"
                )
            elif self.cls.pagination is _DEFAULT_PAGINATION:
                fix = f"Create it with the default pagination, or leave {self.out}"
            else:
                fix = (
                    f"Create it with the pagination of {setter.__name__}, or "
                    f"leave {self.out}"
                )
            raise RestlyConfigurationError(
                f"{self.cls.__name__}.list_params_schema{self.origin} was "
                f"created for other pagination settings than the view's. {fix}."
            )

    def _filter_method_error(
        self, name: str, key: str, kind: str
    ) -> RestlyConfigurationError:
        setter = _owner(self.cls, name)
        fix = f"Leave {self.out}"
        if setter is not self.declarer and not self.below(setter):
            fix += f", or set {name} = None on {self.declarer.__name__} to drop it"
        return RestlyConfigurationError(
            f"{self.cls.__name__} has the filter method {name}"
            f"{_origin(self.cls, setter)}, but {self.its} {kind}. Restly adds "
            f"the key {key!r} of a filter method only to list params that it "
            f"generates. {fix}."
        )

    def _setting_error(
        self, name: str, problem: str, *, expected: Any, on_declarer: str
    ) -> RestlyConfigurationError:
        """The error for a list setting of the view that the list params do
        not have.

        :param on_declarer: The fix when the declarer sets the setting.
        """
        value = _list_setting(self.cls, name)
        setter = _owner(self.cls, name)
        if self.below(setter):
            fix = (
                f"{self.remove(name, setter)}, or declare list_params_schema on "
                f"{setter.__name__} as well, created by "
                f"fr.query.derive_list_params_schema() with {name}={value!r}"
            )
        elif setter is self.declarer:
            fix = f"{on_declarer}, or leave {self.out}"
        else:
            fix = (
                f"Set {name} = {expected!r} on {self.declarer.__name__}, or "
                f"leave {self.out}"
            )
        return RestlyConfigurationError(
            f"{self.cls.__name__} {_how_set(self.cls, name)}, but {self.its} "
            f"{problem}. {fix}."
        )


#: A ``BaseRestView`` at any parameterization, for the signature below.
_AnyRestView = BaseRestView[Any, Any, Any, Any, Any]


def resolve_scope(target: type[Any] | _AnyRestView) -> WhereClause:
    """The scope a read applies, resolved down the ladder.

    Pass a view, class or instance, for the visibility that view's reads
    apply: its declared
    :attr:`scope <fastapi_restly.views.BaseRestView.scope>`, the model's
    ``default_scope`` when the view declares none, and
    ``fr.clauses.UNSCOPED`` when neither does. Pass a mapped model class
    for the model rung alone, which is what every reference check applies.
    The two answers differ wherever a view declares a scope, so code off
    the request path that wants what the API shows passes the view.

    The result is a clause or ``fr.clauses.UNSCOPED``, never ``None``, so
    it composes without a branch::

        stmt = fr.apply_clauses(
            select(Task).where(Task.project_id == id),
            fr.resolve_scope(TaskView),
        )

    Every read resolves here, so a route that applies the result sees the
    same rows as
    :meth:`get_one <fastapi_restly.views.RestView.get_one>` and
    :meth:`get_many <fastapi_restly.views.RestView.get_many>`. It resolves
    the scope and does not apply it: applying stays with the framework,
    and there is no apply-side override point.

    :param target: a view class or instance, or a mapped model class.
    :raises RestlyConfigurationError: if the declared scope is not a clause.
    :raises TypeError: if ``target`` is neither a view nor a mapped model.
    """
    label = target.__name__ if isinstance(target, type) else type(target).__name__
    if is_mapped_class(target):
        return _model_scope(target)
    if not isinstance(target, BaseRestView) and not (
        isinstance(target, type) and issubclass(target, BaseRestView)
    ):
        raise TypeError(
            "resolve_scope() takes a RestView / AsyncRestView class or instance, "
            f"or a mapped model class, got {label}"
        )
    declared = target.scope
    if declared is None:
        return _model_scope(target.model)
    # backstop for a post-definition assignment; the declared form is
    # validated in __init_subclass__
    return _checked_scope(declared, label, "the scope")


def _guard_list_params(route: Callable) -> Callable:
    """Run the unknown-key guard before a route that takes ``list_params``.

    A copy, like the parent-endpoint copies: it keeps the route args and the
    signature, and it passes on its arguments as it gets them. The marker keeps
    a subclass's copy from being wrapped twice.
    """
    self_name = next(iter(inspect.signature(route).parameters))
    if inspect.iscoroutinefunction(route):

        async def _async_guarded(*args, **kwargs):
            (args[0] if args else kwargs[self_name])._reject_unknown_query_params()
            return await route(*args, **kwargs)

        guarded: Callable = _async_guarded
    else:

        def _sync_guarded(*args, **kwargs):
            (args[0] if args else kwargs[self_name])._reject_unknown_query_params()
            return route(*args, **kwargs)

        guarded = _sync_guarded

    guarded = _wrap_endpoint(guarded, route)
    guarded._fr_list_guard = True  # type: ignore[attr-defined]
    return guarded


def _is_pydantic_class(value: Any) -> bool:
    return isinstance(value, type) and issubclass(value, pydantic.BaseModel)


@functools.cache
def _type_adapter(annotation: Any) -> pydantic.TypeAdapter[Any]:
    return pydantic.TypeAdapter(annotation)


def _list_item_type(annotation: Any) -> Any:
    """``X`` for ``list[X]`` and ``Sequence[X]``, also with ``| None``. Or
    ``None``."""
    annotation, _ = _without_none(annotation)
    if get_origin(annotation) not in (list, collections.abc.Sequence):
        return None
    return next(iter(get_args(annotation)), None)


def _has_items_without_a_type(list_response: Any) -> bool:
    """Whether ``list_response`` is a generic class that was not given the type of
    its items, such as ``NumberedEnvelope``."""
    if not _is_pydantic_class(list_response):
        return False
    field = list_response.model_fields.get("data")
    return field is not None and isinstance(_list_item_type(field.annotation), TypeVar)


def _list_item_annotation(list_response: Any) -> Any:
    """The type of the items of a list response: ``X`` for ``list[X]`` and for a
    class with a field ``data: list[X]``. Or ``None``."""
    annotation = list_response
    if _is_pydantic_class(list_response):
        field = list_response.model_fields.get("data")
        annotation = None if field is None else field.annotation
    return _list_item_type(annotation)


def _list_item_class(list_response: Any) -> type[pydantic.BaseModel] | None:
    """The Pydantic class of the items of a list response, also for ``X | None``
    or ``Annotated[X, ...]``. Or ``None``."""
    item, _ = _without_none(_bare(_list_item_annotation(list_response)))
    item = _bare(item)
    return item if _is_pydantic_class(item) else None


def _fields_the_items_lack(
    list_response: Any, response: type[pydantic.BaseModel]
) -> list[str]:
    """The fields without a default of the items of ``list_response`` that
    ``response`` has neither as a field nor as an attribute. A validator that
    runs before the fields may fill them, so such a class is not looked at."""
    item = _list_item_class(list_response)
    if item is None or any(
        validator.info.mode == "before"
        and not validator.func.__module__.startswith("fastapi_restly.")
        for validator in item.__pydantic_decorators__.model_validators.values()
    ):
        return []
    return [
        name
        for name, item_field in item.model_fields.items()
        if item_field.is_required()
        and name not in response.model_fields
        and not hasattr(response, name)
    ]


#: Each write schema of a CRUD view, and the routes that take it. The PUT route
#: of a react-admin view takes the update schema too.
_WRITE_SCHEMA_ROUTES = (
    ("create_schema", ("create_endpoint",)),
    ("update_schema", ("update_endpoint", "put")),
)


def _serves_route(view_cls: type, method_name: str) -> bool:
    """True if the view has a route method ``method_name``. A view leaves out
    an inherited route with ``method_name = None``."""
    return hasattr(getattr(view_cls, method_name, None), "_api_route_args")


def _copy_route(endpoint: Callable, view_cls: type, name: str) -> Callable:
    """A copy of a route of a base class, for ``view_cls`` to hold as ``name``."""
    copied = _make_copy(endpoint)
    if getattr(endpoint, "__module__", "").startswith("fastapi_restly."):
        # The shells carry override-redirect docstrings for help()/source
        # readers. FastAPI reads endpoint.__doc__ as the OpenAPI operation
        # description, so strip the copy: framework guidance must not leak
        # into the user's API docs. User-defined endpoints keep theirs.
        copied.__doc__ = None
    # Set explicit __qualname__ for debugging purposes.
    copied.__qualname__ = f"{view_cls.__name__}_{name}_wrapper"
    return copied


def _make_copy(endpoint: Callable) -> Callable:
    """
    Wrap the endpoint in a new function as kind of copy.

    The copy passes on its arguments as it gets them. FastAPI calls an endpoint
    with keyword arguments only, ``self`` too, and a decorator on the method must
    see that same call. slowapi, for one, fails when ``self`` alone is positional.

    Fun fact: You cannot do this inside a for loop, because the closure of 'endpoint'
    inside the wrapper works on the variable, not on the value. And for-loops in Python
    do not have their own variable scope.

    https://eev.ee/blog/2011/04/24/gotcha-python-scoping-closures/
    """
    if inspect.iscoroutinefunction(endpoint):

        async def _async_wrapper(*args, **kwargs):
            return await endpoint(*args, **kwargs)

        endpoint_wrapper: Callable = _async_wrapper
    else:

        def _sync_wrapper(*args, **kwargs):
            return endpoint(*args, **kwargs)

        endpoint_wrapper = _sync_wrapper

    return _wrap_endpoint(endpoint_wrapper, endpoint)


def _wrap_endpoint(wrapper: Callable, endpoint: Callable) -> Callable:
    """Copy the endpoint's metadata and globals onto a wrapper."""
    # FastAPI 0.115 resolves string annotations against the wrapper's
    # globals; FastAPI 0.128 and later unwrap the endpoint first. These
    # wrappers read only their arguments and closure, so the endpoint's
    # globals can replace this module's without changing what they do.
    copied = types.FunctionType(
        wrapper.__code__,
        inspect.unwrap(endpoint).__globals__,
        wrapper.__name__,
        wrapper.__defaults__,
        wrapper.__closure__,
    )
    functools.update_wrapper(copied, endpoint)
    copied.__annotations__ = endpoint.__annotations__.copy()
    return copied
