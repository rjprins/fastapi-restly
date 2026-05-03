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

Pyright/static-typing notes
---------------------------
This module contains a small number of pyright errors that are accepted
limitations of SQLAlchemy 2.0's typing model and Python's
``ClassVar[GenericT]`` rule, namely:

- ``ClassVar[type[SchemaT]]`` etc. (lines ~414-420): pyright (correctly per
  PEP 526) reports ``"ClassVar" type cannot include type variables``. These
  generic class attributes carry no runtime cost; the alternative would be
  ``type[SchemaT]`` without ``ClassVar``, which makes them per-instance.
- ``Cannot access attribute "id" for class "DeclarativeBase"``: SQLAlchemy's
  base does not declare ``id`` (subclasses add it via ``IDMixin``). We use
  ``getattr(model, "id")`` to access it generically.
- ``Cannot access attribute "index"/"get"/"post"/...``: these attributes are
  populated by ``before_include_view`` at class-construction time, so static
  type checkers cannot see them.

These warnings only surface when consumers run pyright with
``useLibraryCodeForTypes = true``. They do not appear in the framework's CI
typing gate (which checks ``tests/typing/``, the consumer-facing fixtures).
"""

import dataclasses
import functools
import inspect
import types
from enum import Enum
from math import ceil
from typing import (
    Annotated,
    Any,
    Callable,
    ClassVar,
    Generic,
    Iterator,
    Sequence,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

import fastapi
import pydantic
from fastapi import BackgroundTasks, Request, Response, WebSocket
from fastapi.params import Depends as _DependsMarker
from pydantic import create_model
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase, selectinload
from starlette.datastructures import QueryParams
from typing_extensions import TypeVar

from .._exceptions import register_default_exception_handlers
from ..query import (
    DEFAULT_LIMIT,
    DEFAULT_PAGE_SIZE,
    MAX_LIMIT,
    MAX_PAGE_SIZE,
    QueryModifierVersion,
    get_query_modifier_version,
    use_query_modifier_version,
)
from ..query._config import get_query_param_schema_creator
from ..schemas import (
    BaseSchema,
    IDRef,
    IDSchema,
    auto_generate_schema_for_view,
    create_model_with_optional_fields,
    create_model_without_read_only_fields,
    get_writable_inputs,
    is_field_writeonly,
    is_readonly_field,
)
from ._openapi import _register_for_resource_ref

ModelT = TypeVar("ModelT", bound=DeclarativeBase, default=DeclarativeBase)
SchemaT = TypeVar("SchemaT", bound=BaseSchema, default=BaseSchema)
CreateSchemaT = TypeVar("CreateSchemaT", bound=BaseSchema, default=BaseSchema)
UpdateSchemaT = TypeVar("UpdateSchemaT", bound=BaseSchema, default=BaseSchema)
IdT = TypeVar("IdT", default=int)


def _accepts_init_kwarg(model_cls: type, attr_name: str) -> bool:
    """Return True if attr_name can be passed as a keyword argument to model_cls.__init__.

    Non-dataclass models (DeclarativeBase subclasses using mapped_column) accept all
    kwargs. Dataclass-based models may have fields with init=False, in which case
    passing the attribute to __init__ raises TypeError.
    """
    if not dataclasses.is_dataclass(model_cls):
        return True
    dc_fields = {f.name: f for f in dataclasses.fields(model_cls)}
    return attr_name not in dc_fields or dc_fields[attr_name].init


def _requires_init_kwarg(model_cls: type, attr_name: str) -> bool:
    if not dataclasses.is_dataclass(model_cls):
        return False
    dc_fields = {f.name: f for f in dataclasses.fields(model_cls)}
    field = dc_fields.get(attr_name)
    if field is None or not field.init:
        return False
    return (
        field.default is dataclasses.MISSING
        and field.default_factory is dataclasses.MISSING
    )


@dataclasses.dataclass
class _CreatePlan:
    kwargs: dict[str, Any]
    post_assignments: dict[str, Any]


def _has_model_attr(model_cls: type[DeclarativeBase], attr_name: str) -> bool:
    return hasattr(model_cls, attr_name)


def _get_relationship_property(
    model_cls: type[DeclarativeBase],
    relation_name: str,
) -> Any | None:
    try:
        mapper = sa_inspect(model_cls)
    except Exception:
        return None
    return mapper.relationships.get(relation_name)


def _get_unambiguous_local_fk_name(
    model_cls: type[DeclarativeBase],
    relation_name: str,
) -> str | None:
    relationship_property = _get_relationship_property(model_cls, relation_name)
    if relationship_property is None:
        return None

    if getattr(relationship_property.direction, "name", None) != "MANYTOONE":
        return None

    local_columns = list(relationship_property.local_columns)
    if len(local_columns) != 1:
        column_names = ", ".join(column.key for column in local_columns) or "<none>"
        raise ValueError(
            f"Cannot infer a single local FK for relationship "
            f"{model_cls.__name__}.{relation_name}; found {column_names}. "
            "Use an explicit custom handler for this relationship."
        )
    return local_columns[0].key


def _is_reference_schema_field(
    schema_cls: type[BaseSchema],
    field_name: str,
) -> bool:
    field_info = schema_cls.model_fields.get(field_name)
    if field_info is None:
        return False
    return _is_idschema_reference_annotation(field_info.annotation)


def _add_assignment(target: dict[str, Any], field_name: str | None, value: Any) -> None:
    if field_name:
        target[field_name] = value


def iter_creatable_fields(
    schema_obj: BaseSchema,
    schema_cls: type[BaseSchema] | None = None,
) -> Iterator[tuple[str, Any]]:
    """Iterate over (field_name, value) pairs that should be used to construct a new
    ORM object from ``schema_obj``.

    Fields marked as ``ReadOnly`` are skipped. Unlike :func:`get_writable_inputs`,
    this also includes fields that were not explicitly provided, so that
    schema-level defaults end up on the new object.
    """
    if schema_cls is None:
        schema_cls = schema_obj.__class__
    for field_name, value in schema_obj:
        if is_readonly_field(schema_cls, field_name):
            continue
        yield field_name, value


def _add_resolved_reference_to_create_plan(
    plan: _CreatePlan,
    model_cls: type[DeclarativeBase],
    field_name: str,
    value: DeclarativeBase,
) -> None:
    if field_name.endswith("_id"):
        fk_name = field_name
        relation_name = field_name[:-3]
        accepts_relation = _has_model_attr(
            model_cls, relation_name
        ) and _accepts_init_kwarg(model_cls, relation_name)

        if (
            _requires_init_kwarg(model_cls, fk_name)
            and accepts_relation
            and _requires_init_kwarg(model_cls, relation_name)
        ):
            plan.kwargs[fk_name] = value.id
            plan.kwargs[relation_name] = value
            return

        if accepts_relation and _requires_init_kwarg(model_cls, relation_name):
            plan.kwargs[relation_name] = value
            if _has_model_attr(model_cls, fk_name):
                plan.post_assignments[fk_name] = value.id
            return

        if _accepts_init_kwarg(model_cls, fk_name):
            plan.kwargs[fk_name] = value.id
            if _has_model_attr(model_cls, relation_name):
                plan.post_assignments[relation_name] = value
            return

        if accepts_relation:
            plan.kwargs[relation_name] = value
            plan.post_assignments[fk_name] = value.id
            return

        if _has_model_attr(model_cls, fk_name):
            plan.post_assignments[fk_name] = value.id
        if _has_model_attr(model_cls, relation_name):
            plan.post_assignments[relation_name] = value
        return

    relation_name = field_name
    fk_name = _get_unambiguous_local_fk_name(model_cls, relation_name)

    if _has_model_attr(model_cls, relation_name) and _accepts_init_kwarg(
        model_cls, relation_name
    ):
        plan.kwargs[relation_name] = value
        _add_assignment(plan.post_assignments, fk_name, value.id)
        return

    if fk_name and _accepts_init_kwarg(model_cls, fk_name):
        plan.kwargs[fk_name] = value.id
        if _has_model_attr(model_cls, relation_name):
            plan.post_assignments[relation_name] = value
        return

    if _has_model_attr(model_cls, relation_name):
        plan.post_assignments[relation_name] = value
    _add_assignment(plan.post_assignments, fk_name, value.id)


def build_create_plan(
    model_cls: type[DeclarativeBase],
    schema_obj: BaseSchema,
    schema_cls: type[BaseSchema] | None = None,
) -> _CreatePlan:
    """Translate ``schema_obj`` fields into kwargs for ``model_cls(**kwargs)``.

    Shared by sync and async ``make_new_object``. Assumes any nested ``IDSchema``
    references on ``schema_obj`` have already been resolved (sync vs async).
    """
    if schema_cls is None:
        schema_cls = schema_obj.__class__

    plan = _CreatePlan(kwargs={}, post_assignments={})
    for field_name, value in iter_creatable_fields(schema_obj, schema_cls):
        if isinstance(value, IDSchema) and field_name.endswith("_id"):
            if _accepts_init_kwarg(model_cls, field_name):
                plan.kwargs[field_name] = value.id
            elif _has_model_attr(model_cls, field_name):
                plan.post_assignments[field_name] = value.id
            continue
        if isinstance(value, DeclarativeBase) and _is_reference_schema_field(
            schema_cls, field_name
        ):
            _add_resolved_reference_to_create_plan(plan, model_cls, field_name, value)
            continue

        if _accepts_init_kwarg(model_cls, field_name):
            plan.kwargs[field_name] = value
        elif _has_model_attr(model_cls, field_name):
            plan.post_assignments[field_name] = value
    return plan


def build_create_kwargs(
    model_cls: type[DeclarativeBase],
    schema_obj: BaseSchema,
    schema_cls: type[BaseSchema] | None = None,
) -> dict[str, Any]:
    return build_create_plan(model_cls, schema_obj, schema_cls).kwargs


def apply_create_assignments(
    obj: DeclarativeBase,
    assignments: dict[str, Any],
) -> None:
    for field_name, value in assignments.items():
        setattr(obj, field_name, value)


def _apply_resolved_reference_update(
    obj: DeclarativeBase,
    field_name: str,
    value: DeclarativeBase,
) -> None:
    model_cls = type(obj)
    if field_name.endswith("_id"):
        setattr(obj, field_name, value.id)
        relation_name = field_name[:-3]
        if hasattr(obj, relation_name):
            setattr(obj, relation_name, value)
        return

    if hasattr(obj, field_name):
        setattr(obj, field_name, value)

    fk_name = _get_unambiguous_local_fk_name(model_cls, field_name)
    if fk_name:
        setattr(obj, fk_name, value.id)


def apply_update_to_object(
    obj: DeclarativeBase,
    schema_obj: BaseSchema,
    schema_cls: type[BaseSchema] | None = None,
) -> None:
    """Apply writable inputs from ``schema_obj`` onto ``obj`` in place.

    Shared by sync and async ``update_object``. Assumes any nested ``IDSchema``
    references on ``schema_obj`` have already been resolved (sync vs async).
    """
    for field_name, value in get_writable_inputs(schema_obj, schema_cls).items():
        if isinstance(value, IDSchema) and field_name.endswith("_id"):
            setattr(obj, field_name, value.id)
            continue
        if isinstance(value, DeclarativeBase) and _is_reference_schema_field(
            schema_cls or schema_obj.__class__,
            field_name,
        ):
            _apply_resolved_reference_update(obj, field_name, value)
            continue
        setattr(obj, field_name, value)


def _unwrap_optional_annotation(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin not in (types.UnionType, Union, None):
        return annotation

    if origin is None:
        return annotation

    non_none_args = [arg for arg in get_args(annotation) if arg is not type(None)]
    if len(non_none_args) == 1:
        return non_none_args[0]
    return annotation


def _is_idschema_reference_annotation(annotation: Any) -> bool:
    annotation = _unwrap_optional_annotation(annotation)
    if annotation in (IDSchema, IDRef):
        return True
    if not inspect.isclass(annotation):
        return False
    try:
        if not issubclass(annotation, IDSchema):
            return False
    except TypeError:
        return False
    metadata = getattr(annotation, "__pydantic_generic_metadata__", {})
    return metadata.get("origin") in (IDSchema, IDRef)


def _serialize_idschema_value(annotation: Any, value: Any) -> Any:
    if value is None:
        return None
    id_value = value.id if hasattr(value, "id") else value
    if inspect.isclass(annotation) and issubclass(annotation, IDRef):
        return id_value
    if inspect.isclass(annotation) and issubclass(annotation, IDSchema):
        return annotation.model_construct(id=id_value)
    return {"id": id_value}


def _serialize_response_value(annotation: Any, value: Any) -> Any:
    annotation = _unwrap_optional_annotation(annotation)

    if _is_idschema_reference_annotation(annotation):
        return _serialize_idschema_value(annotation, value)

    origin = get_origin(annotation)
    if origin is list:
        item_annotation = get_args(annotation)[0] if get_args(annotation) else Any
        if _is_idschema_reference_annotation(item_annotation) and isinstance(
            value, Sequence
        ):
            return [
                _serialize_idschema_value(item_annotation, item) for item in value
            ]

    return value


def _get_nested_schema_annotation(annotation: Any) -> type[BaseSchema] | None:
    annotation = _unwrap_optional_annotation(annotation)

    try:
        if inspect.isclass(annotation) and issubclass(annotation, BaseSchema):
            return annotation
    except TypeError:
        pass

    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        if args:
            return _get_nested_schema_annotation(args[0])

    return None


def _build_relationship_loader_options(
    model_cls: type[DeclarativeBase],
    schema_cls: type[BaseSchema],
    seen: set[tuple[type[DeclarativeBase], type[BaseSchema]]] | None = None,
) -> list[Any]:
    if seen is None:
        seen = set()

    visit_key = (model_cls, schema_cls)
    if visit_key in seen:
        return []
    seen = seen | {visit_key}

    mapper = sa_inspect(model_cls)
    options: list[Any] = []
    for field_name, field_info in schema_cls.model_fields.items():
        if field_name not in mapper.relationships:
            continue

        relationship_prop = mapper.relationships[field_name]
        loader = selectinload(getattr(model_cls, field_name))
        nested_schema = _get_nested_schema_annotation(field_info.annotation)

        if nested_schema is not None:
            child_options = _build_relationship_loader_options(
                relationship_prop.mapper.class_, nested_schema, seen
            )
            if child_options:
                loader = loader.options(*child_options)

        options.append(loader)

    return options


class View:
    """
    Class-based view primitive for FastAPI.

    Group related endpoints on a class, share dependencies and metadata via
    class attributes, and let subclasses override individual handlers. Routes
    are bound at :func:`include_view` time, not at class-definition time, so
    subclassing works the way Python developers expect: override a method on
    a subclass and the override is what runs.

    Most users will subclass :class:`RestView` or :class:`AsyncRestView`,
    which extend ``View`` with CRUD scaffolding. Use ``View`` directly for
    grouped non-CRUD endpoints (auth flows, custom RPC routes, etc.).
    """

    prefix: ClassVar[str]
    tags: ClassVar[list[str] | None] = None  # View class name will be added by default
    dependencies: ClassVar[list[Any] | None] = None
    responses: ClassVar[dict[int, Any]] = {}

    @classmethod
    def before_include_view(cls):
        pass

    @classmethod
    def add_to_router(cls, parent_router: fastapi.APIRouter | fastapi.FastAPI) -> None:
        _init_view_cls_and_add_to_router(cls, parent_router)


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
    Add the routes of a View class to a FastAPI app or APIRouter.
    This function should be used for every View class.

    Can be used as a decorator::

        @include_view(app)
        class MyView(AsyncRestView):
            ...

    Or as a function::

        include_view(app, MyView)
    """
    if view_cls is not None:
        _init_view_cls_and_add_to_router(view_cls, parent_router)
        return view_cls

    def class_decorator(view_cls: V) -> V:
        _init_view_cls_and_add_to_router(view_cls, parent_router)
        return view_cls

    return class_decorator


def route(path: str, **api_route_kwargs: Any) -> Callable[..., Any]:
    """Decorator to mark a View method as an endpoint.
    The path and api_route_kwargs are passed into APIRouter.add_api_route(), see for example:
    https://fastapi.tiangolo.com/reference/apirouter/#fastapi.APIRouter.get

    Endpoints methods are later added as routes to the FastAPI app using `include_view()`
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

        @route(path, methods=["PUT"], ... )

    No default status code is set; FastAPI will use 200 if none is specified.
    """
    api_route_kwargs.setdefault("methods", ["PUT"])
    return route(path, **api_route_kwargs)


def patch(path: str, **api_route_kwargs: Any) -> Callable[..., Any]:
    """Decorator to mark a View method as a PATCH endpoint.

    Equivalent to::

        @route(path, methods=["PATCH"], ... )

    No default status code is set; FastAPI will use 200 if none is specified.
    """
    api_route_kwargs.setdefault("methods", ["PATCH"])
    return route(path, **api_route_kwargs)


def delete(path: str, **api_route_kwargs: Any) -> Callable[..., Any]:
    """Decorator to mark a View method as a DELETE endpoint.

    Equivalent to::

        @route(path, methods=["DELETE"], status_code=204, ... )
    """
    api_route_kwargs.setdefault("methods", ["DELETE"])
    api_route_kwargs.setdefault("status_code", 204)
    return route(path, **api_route_kwargs)


class BaseRestView(
    View, Generic[ModelT, SchemaT, CreateSchemaT, UpdateSchemaT, IdT]
):
    """
    Base class for RestView implementations.

    This class contains the common functionality shared between AsyncRestView
    and RestView, including schema definitions, model configuration, and
    common CRUD operation logic.
    """

    responses: ClassVar[dict[int, Any]] = {404: {"description": "Not found"}}

    schema: ClassVar[type[SchemaT]]
    # If 'creation_schema' is not defined it will be created from 'schema'
    # using `create_model_without_read_only_fields()`.
    creation_schema: ClassVar[type[CreateSchemaT]]
    update_schema: ClassVar[type[UpdateSchemaT]]
    model: ClassVar[type[ModelT]]
    id_type: ClassVar[type[IdT]] = int
    include_pagination_metadata: ClassVar[bool] = False  # Set True to include count/total in list responses
    exclude_routes: ClassVar[tuple[str, ...]] = ()
    query_modifier_version: ClassVar[QueryModifierVersion]  # Controls V1 vs V2 query parameter style; defaults to global setting
    #: Default ``limit`` for V1 list endpoints. ``None`` means "no implicit
    #: cap" (the framework default). Override per-view.
    default_limit: ClassVar[int | None] = DEFAULT_LIMIT
    #: Maximum ``limit`` accepted on V1 list endpoints. Above this returns 422.
    max_limit: ClassVar[int] = MAX_LIMIT
    #: Default ``page_size`` for V2 list endpoints. ``None`` means "no
    #: implicit cap" (the framework default). Override per-view.
    default_page_size: ClassVar[int | None] = DEFAULT_PAGE_SIZE
    #: Maximum ``page_size`` accepted on V2 list endpoints. Above this returns 422.
    max_page_size: ClassVar[int] = MAX_PAGE_SIZE
    index_param_schema: ClassVar[type[pydantic.BaseModel]]
    pagination_response_schema: ClassVar[type[pydantic.BaseModel]]

    request: fastapi.Request

    def get_query_modifier_version(self) -> QueryModifierVersion:
        return getattr(self, "query_modifier_version", get_query_modifier_version())

    def get_relationship_loader_options(self) -> list[Any]:
        return _build_relationship_loader_options(self.model, self.schema)

    def to_response_schema(self, obj: ModelT | SchemaT) -> SchemaT:
        """Serialize an ORM object to the configured response schema."""
        if isinstance(obj, self.schema):
            return cast(SchemaT, obj)

        # Build a payload using canonical field names. Alias rendering happens
        # when FastAPI serializes the response model.
        payload: dict[str, Any] = {}
        for field_name, field_info in self.schema.model_fields.items():
            if is_field_writeonly(self.schema, field_name):
                continue
            if hasattr(obj, field_name):
                value = getattr(obj, field_name)
                payload[field_name] = _serialize_response_value(
                    field_info.annotation, value
                )
            elif field_info.alias and hasattr(obj, field_info.alias):
                payload[field_name] = getattr(obj, field_info.alias)

        # model_construct intentionally bypasses validation so response-only
        # omissions (for example WriteOnly fields) don't trigger required errors.
        return cast(SchemaT, self.schema.model_construct(**payload))

    @staticmethod
    def _to_query_params(query_params: Any) -> QueryParams:
        if isinstance(query_params, QueryParams):
            return query_params
        if isinstance(query_params, pydantic.BaseModel):
            dumped = query_params.model_dump(
                exclude_none=True, by_alias=True, mode="json"
            )
            return QueryParams({k: str(v) for k, v in dumped.items()})
        if isinstance(query_params, dict):
            return QueryParams({k: str(v) for k, v in query_params.items()})
        return QueryParams(query_params)

    @classmethod
    def _create_pagination_response_schema(
        cls, response_schema: type[BaseSchema]
    ) -> type[pydantic.BaseModel]:
        return create_model(
            f"{cls.__name__}PaginatedResponse",
            items=(Sequence[response_schema], ...),
            total=(int, ...),
            page=(int | None, None),
            page_size=(int | None, None),
            total_pages=(int | None, None),
            limit=(int | None, None),
            offset=(int | None, None),
        )

    def _build_pagination_payload(
        self, query_params: Any, items: Sequence[Any], total: int
    ) -> dict[str, Any]:
        params = self._to_query_params(query_params)
        payload: dict[str, Any] = {
            "items": [self.to_response_schema(obj) for obj in items],
            "total": total,
            "page": None,
            "page_size": None,
            "total_pages": None,
            "limit": None,
            "offset": None,
        }
        uses_v2_pagination = (
            self.get_query_modifier_version() == QueryModifierVersion.V2
        )
        if uses_v2_pagination or "page" in params or "page_size" in params:
            page_size_raw = params.get("page_size")
            if page_size_raw is None and self.default_page_size is None:
                # Unlimited V2: no implicit cap and the client did not ask for
                # one. Leave page/page_size/total_pages as None.
                return payload
            page = int(params.get("page", "1"))
            page_size = int(
                page_size_raw
                if page_size_raw is not None
                else self.default_page_size
            )
            payload["page"] = page
            payload["page_size"] = page_size
            payload["total_pages"] = ceil(total / page_size) if page_size > 0 else 0
            payload["limit"] = page_size
            payload["offset"] = (page - 1) * page_size
            return payload

        if "limit" in params:
            payload["limit"] = int(params["limit"])
        if "offset" in params:
            payload["offset"] = int(params["offset"])
        return payload

    @classmethod
    def before_include_view(cls):
        """
        Apply type annotations needed for FastAPI, before creating an APIRouter from
        this view and registering it.

        This function can be overridden to further tweak the endpoints before they
        are added to FastAPI.
        """
        # Auto-generate schema if none is provided
        if not hasattr(cls, "schema"):
            if not hasattr(cls, "model"):
                raise ValueError(
                    f"'{cls.__name__}.model' must be specified to auto-generate schema"
                )
            cls.schema = cast(type[SchemaT], auto_generate_schema_for_view(cls, cls.model))

        if not hasattr(cls, "query_modifier_version"):
            cls.query_modifier_version = get_query_modifier_version()
        if not hasattr(cls, "index_param_schema"):
            with use_query_modifier_version(cls.query_modifier_version):
                creator = get_query_param_schema_creator()
            if cls.query_modifier_version == QueryModifierVersion.V2:
                cls.index_param_schema = creator(
                    cls.schema,
                    default_page_size=cls.default_page_size,
                    max_page_size=cls.max_page_size,
                )
            else:
                cls.index_param_schema = creator(
                    cls.schema,
                    default_limit=cls.default_limit,
                    max_limit=cls.max_limit,
                )
        if not hasattr(cls, "creation_schema"):
            cls.creation_schema = cast(
                type[CreateSchemaT],
                create_model_without_read_only_fields(cls.schema),
            )
        if not hasattr(cls, "update_schema"):
            cls.update_schema = cast(
                type[UpdateSchemaT],
                create_model_with_optional_fields(cls.schema),
            )

        response_schema = cls.schema

        # Only annotate if the methods exist (they will be overridden in subclasses)
        index_response_annotation: Any = Sequence[response_schema]
        if cls.include_pagination_metadata:
            cls.pagination_response_schema = cls._create_pagination_response_schema(
                response_schema
            )
            index_response_annotation = cls.pagination_response_schema

        if hasattr(cls, "index"):
            _annotate(
                cls.index,
                return_annotation=index_response_annotation,
                query_params=Annotated[cls.index_param_schema, fastapi.Query()],
            )
        if hasattr(cls, "get"):
            _annotate(cls.get, return_annotation=response_schema, id=cls.id_type)
        if hasattr(cls, "post"):
            _annotate(
                cls.post,
                return_annotation=response_schema,
                schema_obj=cls.creation_schema,
            )
        if hasattr(cls, "patch"):
            _annotate(
                cls.patch,
                return_annotation=response_schema,
                schema_obj=cls.update_schema,
                id=cls.id_type,
            )
        if hasattr(cls, "delete"):
            _annotate(cls.delete, return_annotation=fastapi.Response, id=cls.id_type)
        _exclude_routes(cls)


def _exclude_routes(cls: type[View]):
    for method_name in cls.exclude_routes:
        # @route decorator adds `_api_route_args` to a method to create the route later.
        # By removing it from the method, the method will no longer be added as a route.
        try:
            view_func = getattr(cls, method_name)
        except AttributeError:
            raise AttributeError(f"{method_name!r} is not a route on {cls.__name__}")
        if not hasattr(view_func, "_api_route_args"):
            raise AttributeError(f"{method_name!r} is not a route on {cls.__name__}")
        del view_func._api_route_args


def _init_view_cls_and_add_to_router(
    view_cls: type[View], parent_router: fastapi.APIRouter | fastapi.FastAPI
):
    """
    To make View classes work in FastAPI some hacks are needed. Those hacks are
    applied here.

    FastAPI does a lot with annotations. For example, accepted or returned JSON is
    often described with Pydantic classes like this:

        def my_endpoint(foo: FooSchema) -> FooSchema:

    Most of the hacks here are to set the correct annotations on (inherited) class
    methods.

    The class-level preparation (copying parent endpoints, renaming, annotating,
    schema generation, dataclass-style __init__) only runs once per View class —
    subsequent calls to ``include_view()`` reuse the prepared class and only
    construct a fresh APIRouter to mount on the new parent. This makes
    registering the same view on multiple routers safe.
    """
    _prepare_view_class(view_cls)
    api_router = _init_api_router(view_cls)
    _register_for_resource_ref(parent_router, view_cls)
    parent_router.include_router(api_router)
    # Fallback registration for users who skip ``fr.configure(app=...)``.
    # ``register_default_exception_handlers`` is idempotent and only acts on
    # FastAPI apps (it ignores nested APIRouter parents).
    if isinstance(parent_router, fastapi.FastAPI):
        register_default_exception_handlers(parent_router)


def _prepare_view_class(view_cls: type[View]) -> None:
    """Run the one-time class-level setup for a View.

    Guarded by the ``_fr_initialised`` marker (stored in ``__dict__`` so it is
    not inherited from a parent class that was registered separately). Calling
    this multiple times is a no-op after the first run.
    """
    if view_cls.__dict__.get("_fr_initialised", False):
        return
    _copy_all_parent_class_endpoints_into_this_subclass(view_cls)
    _init_all_endpoints(view_cls)
    view_cls.before_include_view()
    _init_class_based_view(view_cls)
    view_cls._fr_initialised = True  # type: ignore[attr-defined]


def _copy_all_parent_class_endpoints_into_this_subclass(view_cls: type[View]):
    """
    Override all methods with a @route decorator of the parent classes of view_cls
    with a new copy directly on view_cls . This allows us to change the
    annotations on these endpoints without affecting the parent endpoints.

    For example, FooView.get() delegates to AsyncRestView.get() if it is not
    overridden (this is called implicit delegation through method resolution). And if
    we add the annotation that FooView.get() returns FooSchema but do not make a copy
    then AsyncRestView.get() and all other subclasses will get the FooSchema
    annotation as well.
    """
    for endpoint in _get_all_parent_endpoints(view_cls):
        # Use `cls.__dict__` to check what attributes are directly on the class.
        # This way we side-step the method resolution.
        if endpoint.__name__ in view_cls.__dict__:
            # This endpoint is already overridden!
            continue

        # The original endpoint might be shared between subclasses.
        # So make a copy and put that on the view_cls.
        endpoint_wrapper = _make_copy(endpoint, view_cls)
        # Set explicit __qualname__ for debugging purposes.
        endpoint_wrapper.__qualname__ = (
            f"{view_cls.__name__}_{endpoint.__qualname__}_wrapper"
        )
        setattr(view_cls, endpoint.__name__, endpoint_wrapper)


def _make_copy(endpoint: Callable, view_cls: type[View]) -> Callable:
    """
    Wrap the endpoint in a new function as kind of copy.

    Fun fact: You cannot do this inside a for loop, because the closure of 'endpoint'
    inside the wrapper works on the variable, not on the value. And for-loops in Python
    do not have their own variable scope.

    https://eev.ee/blog/2011/04/24/gotcha-python-scoping-closures/
    """
    if inspect.iscoroutinefunction(endpoint):

        @functools.wraps(endpoint)
        async def endpoint_wrapper(self, *args, **kwargs):
            return await endpoint(self, *args, **kwargs)

    else:

        @functools.wraps(endpoint)
        def endpoint_wrapper(self, *args, **kwargs):
            return endpoint(self, *args, **kwargs)

    endpoint_wrapper.__annotations__ = endpoint.__annotations__.copy()
    return endpoint_wrapper


def _init_all_endpoints(view_cls: type[View]):
    """
    Ensure every endpoint has a unique name and update the 'self' annotation.
    """
    for attr in view_cls.__dict__.values():
        if not hasattr(attr, "_api_route_args"):
            continue
        endpoint = attr
        # Give every endpoint a unique name
        # This will give the FooView.post() endpoint the name "fooview_post"
        endpoint.__name__ = view_cls.__name__.lower() + "_" + endpoint.__name__
        _annotate_self(view_cls, endpoint)


def _annotate(func: Callable, return_annotation: Any = None, **param_annotations):
    """
    Annotate a function by setting func.__signature__ explicitly.
    """
    sig = inspect.signature(func)
    new_params = []
    for param in sig.parameters.values():
        if param.name in param_annotations:
            annotation = param_annotations[param.name]
            new_param = param.replace(annotation=annotation)
            new_params.append(new_param)
        else:
            new_params.append(param)
    func.__signature__ = sig.replace(  # type: ignore[attr-defined]
        parameters=new_params, return_annotation=return_annotation
    )


def _get_all_parent_endpoints(view_cls: type[View]) -> list[Callable]:
    endpoints = []
    for cls in view_cls.mro():
        if cls is view_cls:
            continue
        for name, value in cls.__dict__.items():
            if hasattr(value, "_api_route_args"):
                endpoints.append(value)
    return endpoints


def _init_api_router(view_cls: type[View]) -> fastapi.APIRouter:
    tags: list[str | Enum] = [view_cls.__name__]
    if view_cls.tags:
        tags += view_cls.tags

    # Concatenate prefixes defined at each level of the class hierarchy (base → derived).
    prefix = "".join(c.__dict__["prefix"] for c in reversed(view_cls.mro()) if "prefix" in c.__dict__)
    api_router = fastapi.APIRouter(
        prefix=prefix,
        tags=tags,
        responses=view_cls.responses,
        dependencies=view_cls.dependencies,
    )

    # Find all endpoint functions in this class and add them to the router
    for attr in view_cls.__dict__.values():
        if not hasattr(attr, "_api_route_args"):
            continue
        endpoint = attr
        path, route_kwargs = endpoint._api_route_args
        api_router.add_api_route(path, endpoint, **route_kwargs)

    return api_router


def _annotate_self(view_cls: type[View], endpoint: Callable) -> None:
    """
    Annotate the 'self' argument as 'self=Depends(view_cls)'. That way FastAPI instantiates the
    view_cls before calling the endpoint function and passes it as 'self'.
    Note that it sets endpoint.__signature__ which overrides any other inspection.

    Note: Copied (MIT license) and adjusted from: https://github.com/dmontagu/fastapi-utils/blob/master/fastapi_utils/cbv.py

    Fixes the endpoint signature to ensure FastAPI performs dependency injection properly.
    """
    sig = inspect.signature(endpoint)
    params: list[inspect.Parameter] = list(sig.parameters.values())
    self_param = params[0]
    new_self_param = self_param.replace(default=fastapi.Depends(view_cls))

    new_params = [new_self_param] + [
        param.replace(kind=inspect.Parameter.KEYWORD_ONLY) for param in params[1:]
    ]
    endpoint.__signature__ = sig.replace(parameters=new_params)  # type: ignore[attr-defined]


# Bare-typed annotations FastAPI special-cases for parameter injection
# (no ``Depends(...)`` marker required). Treated alongside ``Depends``-
# marked annotations as DI-wired class attributes; everything else is
# left as plain typing.
_FASTAPI_SPECIAL_INJECTABLE: tuple[type, ...] = (
    Request,
    Response,
    BackgroundTasks,
    WebSocket,
)


def _init_class_based_view(view_cls: type[View]) -> None:
    """
    Note: Copied (MIT license) and adjusted from: https://github.com/dmontagu/fastapi-utils/blob/master/fastapi_utils/cbv.py

    Idempotently modifies the provided `cls`, performing the following modifications:
    * The `__init__` function is updated to set any class-annotated dependencies as instance attributes
    * The `__signature__` attribute is updated to indicate to FastAPI what arguments should be passed to the initializer
    """
    if getattr(view_cls, "__class_based_view", False):
        return  # Already initialized
    old_init: Callable[..., Any] = view_cls.__init__
    old_signature = inspect.signature(old_init)
    old_parameters = list(old_signature.parameters.values())[1:]  # drop `self`
    new_parameters = [
        x
        for x in old_parameters
        if x.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]
    # Marker-based DI with MRO-aware shadowing: walk the MRO from the
    # base classes upward and pick, for each name, an annotation that
    # either carries a ``Depends(...)`` marker or names one of FastAPI's
    # bare-injectable special types (``Request`` / ``Response`` etc.).
    # A *plain* annotation on a more-derived class (e.g. a mixin
    # declaring ``session: AsyncSession`` for static-typing purposes)
    # does NOT shadow a marker-bearing annotation from a base — the
    # framework prefers wiring fidelity over the most-derived hint.
    # Without this rule, any plain annotation a mixin adds would
    # silently break dependency injection.
    di_annotations: dict[str, Any] = {}
    for cls in reversed(view_cls.__mro__):
        try:
            cls_hints = get_type_hints(cls, include_extras=True)
        except Exception:
            continue
        for name, annotation in cls_hints.items():
            if get_origin(annotation) is ClassVar:
                continue
            metadata = getattr(annotation, "__metadata__", ())
            has_depends_marker = any(
                isinstance(m, _DependsMarker) for m in metadata
            )
            underlying = (
                annotation
                if get_origin(annotation) is not Annotated
                else (get_args(annotation)[0] if get_args(annotation) else annotation)
            )
            is_special_type = (
                inspect.isclass(underlying)
                and issubclass(underlying, _FASTAPI_SPECIAL_INJECTABLE)
            )
            if has_depends_marker or is_special_type:
                # Marker-bearing annotation wins, regardless of MRO position.
                di_annotations[name] = annotation
            # Plain annotations are silently ignored — they neither set
            # nor clear an entry in di_annotations.

    dependency_names: list[str] = []
    for name, annotation in di_annotations.items():
        dependency_names.append(name)
        default_value = getattr(view_cls, name, inspect.Parameter.empty)
        new_parameters.append(
            inspect.Parameter(
                name=name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=default_value,
                annotation=annotation,
            )
        )
    new_signature = old_signature.replace(parameters=new_parameters)

    def new_init(self: Any, *args: Any, **kwargs: Any) -> None:
        for dep_name in dependency_names:
            dep_value = kwargs.pop(dep_name)
            setattr(self, dep_name, dep_value)
        old_init(self, *args, **kwargs)

    setattr(view_cls, "__signature__", new_signature)
    setattr(view_cls, "__init__", new_init)
    setattr(view_cls, "__class_based_view", True)
