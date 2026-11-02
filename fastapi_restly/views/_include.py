"""Add a view to a FastAPI app or router.

``include_view`` prepares the view class once, puts the view's own classes
in place of the markers in each route, and adds each route to the router
with its tags and operation id.
"""

import functools
import inspect
import re
import types
import warnings
from collections.abc import Mapping
from enum import Enum
from typing import (
    Annotated,
    Any,
    Callable,
    Iterable,
    Literal,
    Union,
    get_args,
    get_origin,
    overload,
)
from uuid import UUID

import fastapi
import pydantic
from fastapi import Response
from fastapi.datastructures import Default
from typing_extensions import TypeVar

from ..db._config import _fr_config
from ..exc import RestlyConfigurationError, RestlyMisuseWarning
from ..schemas._base import _reject_buried_markers
from ._base import BaseRestView, ListResult, View, _serves_route
from ._class_based_view import (
    _copy_all_parent_class_endpoints_into_this_subclass,
    _init_all_endpoints,
    _init_class_based_view,
)
from ._generated import _drop_stale_settings, _list_params_schema
from ._guards import _check_removed_settings
from ._list_params import _list_openapi_parameters
from ._misuse_lint import _warn_on_misuse
from ._openapi import _register_for_resource_ref
from ._schema_types import (
    SchemaType,
    _evaluated,
    _pydantic_generic_args,
    _replace_schema_types,
    _view_id_type,
)

V = TypeVar("V", bound=type[View])


@overload
def include_view(
    parent_router: fastapi.APIRouter | fastapi.FastAPI, view_cls: V
) -> V: ...
@overload
def include_view(
    parent_router: fastapi.APIRouter | fastapi.FastAPI,
) -> Callable[[V], V]: ...


def include_view(
    parent_router: fastapi.APIRouter | fastapi.FastAPI, view_cls: V | None = None
) -> V | Callable[[V], V]:
    """
    Add a View class's routes to a FastAPI app or APIRouter.

    Prefer the direct call form from your app/router composition layer::

        include_view(app, MyView)

    For small apps, it can also be used as a decorator::

        @include_view(app)
        class MyView(AsyncRestView):
            ...

    Registering a view on several parents mounts its routes on each; calling
    ``include_view`` again with a parent the view is already registered on is
    a no-op.
    """
    if view_cls is not None:
        _init_view_cls_and_add_to_router(view_cls, parent_router)
        return view_cls

    def class_decorator(view_cls: V) -> V:
        _init_view_cls_and_add_to_router(view_cls, parent_router)
        return view_cls

    return class_decorator


def _init_view_cls_and_add_to_router(
    view_cls: type[View], parent_router: fastapi.APIRouter | fastapi.FastAPI
):
    """
    FastAPI builds routes from callable signatures and annotations. View classes
    need a small adapter layer before registration; that preparation happens here.

    FastAPI does a lot with annotations. For example, accepted or returned JSON is
    often described with Pydantic classes like this:

        def my_endpoint(foo: FooSchema) -> FooSchema:

    Most of this preparation sets the correct annotations on inherited class
    methods.

    The class-level preparation (copying parent endpoints, renaming, annotating,
    schema generation, dataclass-style __init__) only runs once per View class —
    subsequent calls to ``include_view()`` reuse the prepared class and only
    construct a fresh APIRouter to mount on the new parent. This makes
    registering the same view on *different* routers safe (e.g. a public app and
    an admin app, or ``/v1`` and ``/v2`` sub-apps).

    Re-registering the same view on the *same* parent is a no-op (flagged by
    the opt-in misuse lint): each parent tracks the view classes already
    mounted on it, so a duplicate ``include_view()`` call (double import,
    decorator plus explicit call) cannot mount the same routes twice.
    """
    # A FastAPI app delegates include_router to its .router; track mounted
    # views on the router so app and app.router count as one parent.
    holder = (
        parent_router.router
        if isinstance(parent_router, fastapi.FastAPI)
        else parent_router
    )
    mounted: set[type[View]] = getattr(holder, "_fr_mounted_views", None) or set()
    if view_cls in mounted:
        if _fr_config.warn_on_misuse:
            warnings.warn(
                f"{view_cls.__name__} is already registered on this app/router; "
                f"this include_view() call is a no-op. Duplicate registration "
                f"usually means a double import or the decorator form combined "
                f"with an explicit include_view() call.",
                RestlyMisuseWarning,
                stacklevel=4,
            )
        return
    _prepare_view_class(view_cls)
    api_router = _init_api_router(view_cls)
    if issubclass(view_cls, BaseRestView):
        _register_for_resource_ref(parent_router, view_cls)
    parent_router.include_router(api_router)
    # Record only after the mount succeeded: a failed registration must not be
    # marked as mounted, or the next call would silently no-op a view whose
    # routes never made it onto the parent.
    mounted.add(view_cls)
    setattr(holder, "_fr_mounted_views", mounted)


def _reject_buried_markers_in_view_schemas(view_cls: type[View]) -> None:
    """Backstop the ``BaseSchema`` import-time check at view registration.

    ``BaseSchema.__pydantic_init_subclass__`` already rejects a buried
    ReadOnly/WriteOnly marker as the schema class is defined, but a view may use
    a schema (and derived create/update schemas) that does not subclass
    ``BaseSchema``. Re-check the schemas this view actually uses so those are
    covered too.
    """
    checked: set[type] = set()
    for attr in ("schema", "response_schema", "create_schema", "update_schema"):
        schema = getattr(view_cls, attr, None)
        if schema is None or schema in checked:
            continue
        checked.add(schema)
        _reject_buried_markers(schema)


def _prepare_view_class(view_cls: type[View]) -> None:
    """Run the one-time class-level setup for a View.

    Guarded by the ``_fr_initialised`` marker (stored in ``__dict__`` so it is
    not inherited from a parent class that was registered separately). Calling
    this multiple times is a no-op after the first run.
    """
    if view_cls.__dict__.get("_fr_initialised", False):
        return
    if issubclass(view_cls, BaseRestView):
        # A class decorator can set a new schema after the class definition.
        _drop_stale_settings(view_cls)
    if _fr_config.warn_on_misuse:
        _warn_on_misuse(view_cls)
    _copy_all_parent_class_endpoints_into_this_subclass(view_cls)
    _init_all_endpoints(view_cls)
    view_cls.before_include_view()
    _reject_buried_markers_in_view_schemas(view_cls)
    _init_class_based_view(view_cls)
    view_cls._fr_initialised = True  # type: ignore[attr-defined]


def _schema_type_in(annotation: Any) -> SchemaType | None:
    """The first marker in ``annotation``, or ``None``."""
    if get_origin(annotation) is Annotated:
        for metadata in get_args(annotation)[1:]:
            if isinstance(metadata, SchemaType):
                return metadata
    if get_origin(annotation) is Literal:
        return None
    for arg in (*get_args(annotation), *_pydantic_generic_args(annotation)):
        if (found := _schema_type_in(arg)) is not None:
            return found
    return None


def _schema_type_in_a_field(
    annotation: Any, seen: set[type] | None = None
) -> tuple[type, str, SchemaType] | None:
    """The first marker in a field of a Pydantic class that ``annotation`` names:
    the class, the name of the field and the marker. Or ``None``.

    Restly fills a route's own annotations, not the fields of a class that it
    names. Pydantic reads such a field as ``Any``.
    """
    seen = set() if seen is None else seen
    for arg in (*get_args(annotation), *_pydantic_generic_args(annotation)):
        if (found := _schema_type_in_a_field(arg, seen)) is not None:
            return found
    is_model = isinstance(annotation, type) and issubclass(
        annotation, pydantic.BaseModel
    )
    if not is_model or annotation in seen:
        return None
    seen.add(annotation)
    for name, field in annotation.model_fields.items():
        marker = next((m for m in field.metadata if isinstance(m, SchemaType)), None)
        if marker is None:
            marker = _schema_type_in(field.annotation)
        if marker is not None:
            return annotation, name, marker
        if (found := _schema_type_in_a_field(field.annotation, seen)) is not None:
            return found
    return None


def _view_route_args(endpoint: Callable, view_cls: Any) -> tuple[str, dict[str, Any]]:
    """The path and the route arguments of ``endpoint``, for this view.

    ``response_model`` can hold a marker. A path parameter annotated
    ``IDType`` gets the converter of the view's id type, unless the path
    gives one already. The decorator's arguments stay as written, so a
    subclass with another id type gets its own path.
    """
    path, route_kwargs = endpoint._api_route_args  # type: ignore[attr-defined]
    response_model = route_kwargs.get("response_model")
    filled = _replace_schema_types(response_model, view_cls)
    if filled is not response_model:
        route_kwargs = {**route_kwargs, "response_model": filled}
    converter = {int: "int", UUID: "uuid"}.get(_view_id_type(view_cls))
    if converter is not None:
        for name, annotation in inspect.get_annotations(endpoint).items():
            annotation = _evaluated(annotation, endpoint)
            is_id = get_origin(annotation) is Annotated and any(
                isinstance(m, SchemaType) and m.name == "IDType"
                for m in get_args(annotation)[1:]
            )
            if is_id:
                path = path.replace(f"{{{name}}}", f"{{{name}:{converter}}}")
    return path, route_kwargs


def _without_none(annotation: Any) -> tuple[Any, bool]:
    """``annotation`` without ``None``, and whether it allowed ``None``:
    ``ResponseSchema | None`` gives ``(ResponseSchema, True)``."""
    if get_origin(annotation) in (Union, types.UnionType):
        args = get_args(annotation)
        rest = [arg for arg in args if arg is not type(None)]
        if len(rest) == 1 and len(rest) < len(args):
            return rest[0], True
    return annotation, False


def _view_response_kind(view_cls: Any, endpoint: Callable) -> tuple[str | None, Any]:
    """How a route answers with the view's response, and the response model
    that Restly sets for it, if any.

    The kind is ``"single"`` for one row, ``"many"`` for a list of rows, and
    ``"list"`` for a ``ListResult``. It comes from the response that the route
    declares: the ``response_model`` of its decorator, else its return
    annotation. A marker of the view's response, the view's own response
    class, or the view's model in the return annotation, declares the view's
    response, also with ``| None``. Another class that the route names keeps
    FastAPI's behaviour.
    """
    _, route_kwargs = endpoint._api_route_args  # type: ignore[attr-defined]
    in_decorator = "response_model" in route_kwargs
    if in_decorator:
        declared = route_kwargs["response_model"]
    else:
        declared = inspect.get_annotations(endpoint).get("return")
    declared, optional = _without_none(_evaluated(declared, endpoint))
    kind, response_model = _declared_view_response(view_cls, declared, in_decorator)
    if optional and response_model is not None:
        response_model = Union[response_model, None]
    return kind, response_model


def _declared_view_response(
    view_cls: Any, declared: Any, in_decorator: bool
) -> tuple[str | None, Any]:
    """The kind and the response model of :func:`_view_response_kind`, for a
    declared response without ``None``.

    A marker declares the view's response, and so does the view's own
    response class or list response class, such as the one from
    :func:`~fastapi_restly.schemas.derive_response_schema`: each call with
    the same schema returns that same class.
    """
    response = view_cls.response_schema
    list_response = _bare(view_cls.list_response_schema)
    declared = _bare(_replace_schema_types(declared, view_cls))
    if declared is response:
        return "single", None
    if declared is list_response:
        return "list", None
    if get_origin(declared) is list:
        (item,) = get_args(declared) or (None,)
        item = _bare(item)
        if item is response:
            return "many", None
        if not in_decorator and item is view_cls.model:
            return "many", list[response]  # type: ignore[valid-type]
    if not in_decorator and declared is view_cls.model:
        return "single", response
    return None, None


def _bare(annotation: Any) -> Any:
    """``annotation`` without its ``Annotated``: the class that it names."""
    if get_origin(annotation) is Annotated:
        return get_args(annotation)[0]
    return annotation


def _with_view_response(
    view_cls: Any, endpoint: Callable, route_kwargs: dict[str, Any]
) -> tuple[Callable, dict[str, Any]]:
    """The endpoint that FastAPI calls, and its route arguments.

    A route that declares the view's response answers through the view's
    response step, also when it returns the row: a row goes through
    :meth:`~BaseRestView.to_single_response`, each row of a list, or of another
    iterable such as the result of ``session.scalars()``, too, and a
    ``ListResult`` through :meth:`~BaseRestView.to_list_response`. Anything else, such as
    ``None`` or a response object that the route built itself, goes out as
    it is.
    """
    kind, response_model = _view_response_kind(view_cls, endpoint)
    if kind is None:
        return endpoint, route_kwargs
    if response_model is not None:
        route_kwargs = {**route_kwargs, "response_model": response_model}
    signature = inspect.signature(endpoint)
    self_name = next(iter(signature.parameters))

    def respond(view: Any, result: Any) -> Any:
        if kind == "single" and isinstance(result, view.model):
            return view._single_response(result)
        if kind == "many" and _is_rows(result):
            rows = list(result)
            if all(isinstance(row, view.model) for row in rows):
                return [view.to_single_response(row) for row in rows]
            # the iterable is used up: send the items that it gave
            return rows
        if kind == "list" and isinstance(result, ListResult):
            return view.to_list_response(result)
        return result

    if inspect.iscoroutinefunction(endpoint):

        @functools.wraps(endpoint)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            result = await endpoint(*args, **kwargs)
            return respond(args[0] if args else kwargs[self_name], result)

    else:

        @functools.wraps(endpoint)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            result = endpoint(*args, **kwargs)
            return respond(args[0] if args else kwargs[self_name], result)

    # FastAPI reads string annotations in the module of the function it
    # calls; an older FastAPI does not look through the wrapper
    wrapped.__signature__ = signature.replace(  # type: ignore[attr-defined]
        parameters=[
            param.replace(annotation=_evaluated(param.annotation, endpoint))
            for param in signature.parameters.values()
        ],
        return_annotation=(
            response_model
            if response_model is not None
            else _evaluated(signature.return_annotation, endpoint)
        ),
    )
    return wrapped, route_kwargs


def _is_rows(result: Any) -> bool:
    """Whether ``result`` can be a list of rows: an iterable that is not
    text, a mapping, a model or a response."""
    return isinstance(result, Iterable) and not isinstance(
        result, (str, bytes, Mapping, pydantic.BaseModel, Response)
    )


def _reject_schema_types_on_a_plain_view(
    view_cls: type, name: str, endpoint: Callable, route_kwargs: Mapping[str, Any]
) -> None:
    """Raise when a route of a plain View uses a marker: there is no type to
    put in its place."""
    sig = inspect.signature(endpoint)
    annotations = [p.annotation for p in sig.parameters.values()]
    annotations += [sig.return_annotation, route_kwargs.get("response_model")]
    for annotation in annotations:
        marker = _schema_type_in(_evaluated(annotation, endpoint))
        if marker is not None:
            raise RestlyConfigurationError(
                f"{view_cls.__name__}.{name} uses {marker!r}, which stands for "
                f"a type of a CRUD view. {view_cls.__name__} is a plain View, "
                "so it has no such type. Write the type, or base the view on "
                "fr.AsyncRestView or fr.RestView."
            )


def _reject_schema_types_in_fields(
    view_cls: type, name: str, endpoint: Callable, route_kwargs: Mapping[str, Any]
) -> None:
    """Raise when a route names a Pydantic class with a marker in a field: the
    field would have no type in OpenAPI."""
    sig = inspect.signature(endpoint)
    annotations = [p.annotation for p in sig.parameters.values()]
    annotations += [sig.return_annotation, route_kwargs.get("response_model")]
    seen: set[type] = set()
    for annotation in annotations:
        found = _schema_type_in_a_field(_evaluated(annotation, endpoint), seen)
        if found is not None:
            model, field, marker = found
            raise RestlyConfigurationError(
                f"{view_cls.__name__}.{name} names the class {model.__name__}, "
                f"and its field {field} uses {marker!r}. Restly fills a marker "
                "in the annotations of a route, not in a field of a class, so "
                "this field would have no type in OpenAPI. Make the class "
                "generic and write Wrapper[ResponseSchema] in the route, or build "
                "the class in a fastapi_restly.views.SchemaType function."
            )


def _init_api_router(view_cls: type[View]) -> fastapi.APIRouter:
    # Also for a plain View, which _check_renamed_names does not see.
    _check_removed_settings(view_cls)
    # Each level of the class hierarchy adds to these, base first: a subclass
    # cannot drop a guard its base declares.
    levels = [c.__dict__ for c in reversed(view_cls.mro())]
    prefix = "".join(level["prefix"] for level in levels if "prefix" in level)
    dependencies: list[Any] = []
    responses: dict[int | str, dict[str, Any]] = {}
    for level in levels:
        for dependency in level.get("dependencies") or ():
            # A subclass that spells out ``[own, *Base.dependencies]`` lists
            # the base's entries again. They move to where it lists them, so
            # ``own`` still runs first.
            dependencies = [seen for seen in dependencies if seen is not dependency]
            dependencies.append(dependency)
        responses.update(level.get("responses") or {})
    tags = _get_router_tags(view_cls, prefix)
    api_router = fastapi.APIRouter(
        prefix=prefix, tags=tags, responses=responses, dependencies=dependencies
    )

    # Find all endpoint functions in this class and add them to the router
    list_parameters: list[dict[str, Any]] | None = None
    is_rest_view = issubclass(view_cls, BaseRestView)
    for name, attr in list(view_cls.__dict__.items()):
        if not hasattr(attr, "_api_route_args"):
            continue
        # include_routes and exclude_routes are settings of the CRUD views only
        if is_rest_view and not _serves_route(view_cls, name):
            continue
        endpoint = attr
        if is_rest_view:
            path, route_kwargs = _view_route_args(endpoint, view_cls)
        else:
            path, route_kwargs = endpoint._api_route_args
            _reject_schema_types_on_a_plain_view(view_cls, name, endpoint, route_kwargs)
        _reject_schema_types_in_fields(view_cls, name, endpoint, route_kwargs)
        defaults = {}
        if is_rest_view and name in _CRUD_ROUTE_METADATA:
            route_name, summary = _CRUD_ROUTE_METADATA[name]
            # FastAPI carries placeholders through include_router(), so an
            # explicit generator on the route, router, or app still wins.
            # https://github.com/fastapi/fastapi/blob/0.115.0/fastapi/utils.py
            defaults = {
                "name": route_name,
                "summary": summary,
                "generate_unique_id_function": Default(
                    functools.partial(
                        _generate_crud_operation_id,
                        is_member=name not in {"get_many_endpoint", "create_endpoint"},
                    )
                ),
            }
        route_kwargs = {**defaults, **route_kwargs}
        if is_rest_view:
            endpoint, route_kwargs = _with_view_response(
                view_cls, endpoint, route_kwargs
            )
        if getattr(endpoint, "_fr_list_guard", False):
            if list_parameters is None:
                list_parameters = _list_openapi_parameters(
                    _list_params_schema(view_cls)
                )
            extra = dict(route_kwargs.get("openapi_extra") or {})
            extra["parameters"] = [*list_parameters, *extra.get("parameters", [])]
            route_kwargs["openapi_extra"] = extra
        _add_api_route(api_router, view_cls, path, endpoint, route_kwargs)

    return api_router


_CRUD_ROUTE_METADATA = {
    "get_many_endpoint": ("get_many", "List"),
    "get_one_endpoint": ("get_one", "Get"),
    "create_endpoint": ("create", "Create"),
    "update_endpoint": ("update", "Update"),
    "delete_endpoint": ("delete", "Delete"),
    "put": ("put", "Update (PUT)"),
}


def _generate_crud_operation_id(
    route: fastapi.routing.APIRoute, *, is_member: bool
) -> str:
    """Combine the mounted resource path and action, omitting the member key."""
    path = route.path_format.rstrip("/")
    if is_member:
        path = re.sub(r"/\{[^}]+\}$", "", path)
    segments = [
        f"by_{segment[1:-1]}" if segment.startswith("{") else segment
        for segment in path.strip("/").split("/")
        if segment
    ]
    resource = re.sub(r"\W+", "_", "_".join(segments)).strip("_") or "root"
    action = {"get_many": "list", "get_one": "get"}.get(route.name, route.name)
    return re.sub(r"\W+", "_", f"{resource}_{action}").strip("_")


def _get_router_tags(view_cls: type[View], prefix: str) -> list[str | Enum]:
    if view_cls.tags is not None:
        return list(view_cls.tags)
    return [_derive_tag_from_prefix(prefix) or view_cls.__name__]


def _derive_tag_from_prefix(prefix: str) -> str | None:
    segments = [segment for segment in prefix.strip("/").split("/") if segment]
    if not segments:
        return None
    return segments[-1].replace("-", " ").replace("_", " ").title()


def _add_api_route(
    api_router: fastapi.APIRouter,
    view_cls: type[View],
    path: str,
    endpoint: Callable,
    route_kwargs: dict[str, Any],
) -> None:
    if _should_add_collection_route_alias(view_cls, path):
        api_router.add_api_route("", endpoint, **route_kwargs)
        hidden_alias_kwargs = {**route_kwargs, "include_in_schema": False}
        api_router.add_api_route("/", endpoint, **hidden_alias_kwargs)
        return

    api_router.add_api_route(path, endpoint, **route_kwargs)


def _should_add_collection_route_alias(view_cls: type[View], path: str) -> bool:
    return issubclass(view_cls, BaseRestView) and path == "/"
