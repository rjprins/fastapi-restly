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

import dataclasses
import functools
import inspect
import re
import types
import warnings
from collections.abc import Mapping, Set
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Callable,
    ClassVar,
    Generic,
    Iterable,
    Iterator,
    Protocol,
    Sequence,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)
from uuid import UUID

import fastapi
import pydantic
from fastapi import BackgroundTasks, Request, Response, WebSocket
from fastapi.datastructures import Default
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.models import APIKeyIn
from fastapi.params import Body as _BodyMarker
from fastapi.params import Depends as _DependsMarker
from fastapi.params import Param as _ParamMarker
from fastapi.security.api_key import APIKeyBase
from pydantic.fields import FieldInfo
from sqlalchemy import JSON as _JSONType
from sqlalchemy import ColumnElement, Select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select as sa_select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import del_attribute
from starlette.datastructures import QueryParams
from typing_extensions import TypeVar

from .._exception_handlers import register_default_exception_handlers
from ..clauses import (
    UNSCOPED,
    Unscoped,
    WhereClause,
    all_of,
    apply_clauses,
    where_clause,
)
from ..clauses._scopes import _default_scope

#: A per-read scope: ``None`` for the view's own, a clause that replaces it
#: for that read, or ``fr.clauses.UNSCOPED``.
ReadScope = WhereClause | Unscoped | None

# A per-read narrowing filter: a SQLAlchemy boolean expression, or anything
# all_of takes. Private: only the final list handler accepts one, so no
# override spells it.
_ReadWhere = ColumnElement[bool] | WhereClause | Unscoped | None
from .._mapping import is_mapped_class, is_mapped_instance
from .._pagination import (
    _DEFAULT_PAGINATION,
    DEFAULT_PAGE_SIZE,
    NoPagination,
    NumberedPagination,
    _build_envelope,
    _list_envelope,
    _numbered_page_info,
    _unpaginated_page_info,
)
from ..db._globals import _fr_globals
from ..exc import RestlyConfigurationError, RestlyMisuseWarning
from ..objects import snapshot as _object_snapshot
from ..query import derive_schema_list_params
from ..query._impl import _UNKNOWN, _created_with, _query_settings
from ..schemas import BaseSchema, IDSchema
from ..schemas._base import (
    _model_id_type,
    _reject_buried_markers,
    _unwrap_optional_annotation,
    create_model_with_optional_fields,
    create_model_without_read_only_fields,
    get_writable_inputs,
    is_readonly_field,
    is_reference_field,
    is_writeonly_field,
    reference_origin_and_target,
)
from ..schemas._generator import derive_schema
from ._openapi import _register_for_resource_ref

# Unbound: a mapped class need not subclass DeclarativeBase (a SQLModel table,
# an imperative mapping). ``BaseRestView`` checks the mapping at class definition.
ModelT = TypeVar("ModelT", default=Any)
SchemaT = TypeVar("SchemaT", bound=pydantic.BaseModel, default=BaseSchema)
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

    objects: Sequence[ModelT]
    total_count: int | None = None
    list_params: Any = None


class ViewRoute(str, Enum):
    """Default CRUD route names that can be referenced by view options.

    Values are the endpoint method names so ``exclude_routes`` can drop them.
    """

    GET_MANY = "get_many_endpoint"
    GET_ONE = "get_one_endpoint"
    CREATE = "create_endpoint"
    UPDATE = "update_endpoint"
    DELETE = "delete_endpoint"


class ResponseShape(str, Enum):
    """The wire shape an endpoint method asks :meth:`BaseRestView.to_response` to
    produce.

    This is separate from write-action names such as ``"publish"``. Endpoint
    methods choose one of these three response shapes; custom actions remain an
    open string namespace.
    """

    SINGLE = "single"  # one serialized object
    LIST = "list"  # a ListResult -> the list envelope
    EMPTY = "empty"  # 204 No Content


class Action:
    """Canonical CRUD action names passed to ``authorize`` / ``before_action_commit``
    / ``after_action_commit``.

    This is a constants class, not an ``Enum``: custom actions and mixins add
    their own names. Use constants for typo checking at import time.
    """

    GET_MANY = "get_many"
    GET_ONE = "get_one"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


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
    # Many-to-one relationships the input did not supply.
    unsupplied_relationships: list[str] = dataclasses.field(default_factory=list)


class _HasID(Protocol):
    """Anything with an ``id`` attribute. By framework convention, primary
    keys are named ``id``; ``IDBase`` formalizes this but isn't required."""

    id: Any


def _has_model_attr(model_cls: type[Any], attr_name: str) -> bool:
    return hasattr(model_cls, attr_name)


def _get_relationship_property(model_cls: type[Any], relation_name: str) -> Any | None:
    try:
        mapper = sa_inspect(model_cls)
    except Exception:
        return None
    return mapper.relationships.get(relation_name)


def _column_attr_name(mapper: Any, column: Any) -> str | None:
    """Map a Column object back to its mapped attribute name.

    ``Mapper.columns`` is keyed by the mapped attribute name, but a Column's own
    ``key`` is the DB column name; the two differ when a column is declared
    ``mapped_column("db_name", ...)``. Reference routing works in attribute-name
    space (kwargs and ``setattr``), so callers need the attribute name, not the
    column key. Compared by identity because ``Column.__eq__`` builds a SQL
    expression rather than a boolean.
    """
    for attr_name, mapped_column in mapper.columns.items():
        if mapped_column is column:
            return attr_name
    return None


def _get_unambiguous_local_fk_name(
    model_cls: type[Any], relation_name: str
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
    # Return the mapped attribute name (what kwargs/setattr need), which differs
    # from the column's DB-name ``key`` under ``mapped_column("db_name", ...)``.
    try:
        mapper = sa_inspect(model_cls)
    except Exception:
        return local_columns[0].key
    return _column_attr_name(mapper, local_columns[0]) or local_columns[0].key


def _many_to_one_names(model_cls: type[Any]) -> list[str]:
    try:
        mapper = sa_inspect(model_cls)
    except Exception:
        return []
    return [
        relationship_property.key
        for relationship_property in mapper.relationships
        if getattr(relationship_property.direction, "name", None) == "MANYTOONE"
    ]


def _relationship_name_for_fk(model_cls: type[Any], fk_attr_name: str) -> str | None:
    """Reverse of :func:`_get_unambiguous_local_fk_name`.

    Given a scalar FK column's attribute name, return the single many-to-one
    relationship that uses it as its local column, or ``None`` when there is no
    such relationship or more than one (ambiguous, so no pairing is guessed).
    This is how a FK reference field finds its partner relationship without
    relying on the ``<relation>_id`` naming convention.
    """
    try:
        mapper = sa_inspect(model_cls)
    except Exception:
        return None
    fk_column = mapper.columns.get(fk_attr_name)
    if fk_column is None:
        return None
    # Match by Column identity, not by ``.key``: the column's key is the DB name,
    # which differs from ``fk_attr_name`` under ``mapped_column("db_name", ...)``.
    # (``is`` also avoids ``Column.__eq__``'s SQL-expression overload.)
    matches = [
        relationship_property.key
        for relationship_property in mapper.relationships
        if getattr(relationship_property.direction, "name", None) == "MANYTOONE"
        and any(column is fk_column for column in relationship_property.local_columns)
    ]
    return matches[0] if len(matches) == 1 else None


def _is_mapped_column(model_cls: type[Any], field_name: str) -> bool:
    """True if ``field_name`` maps to a (scalar) column on ``model_cls``.

    This is the target for writing a resolved reference's raw id. Decided by the
    mapper rather than the field name, so a FK column works under any name, not
    only ``<relation>_id``.
    """
    try:
        mapper = sa_inspect(model_cls)
    except Exception:
        return False
    return field_name in mapper.columns


def _is_json_column(model_cls: type[Any], field_name: str) -> bool:
    """True if ``field_name`` maps to a plain ``JSON`` column (``JSONB`` too).

    A ``TypeDecorator`` over ``JSON`` is not a ``JSON`` instance and so is not
    one: it has its own bind processor, which may well want the object as it
    stands.
    """
    try:
        mapper = sa_inspect(model_cls)
    except Exception:
        return False
    column = mapper.columns.get(field_name)
    return column is not None and isinstance(column.type, _JSONType)


def _json_ready(model_cls: type[Any], field_name: str, value: Any) -> Any:
    """Dump pydantic models on their way into a JSON column.

    A schema field typed as a nested model validates to a model instance, and
    a JSON column binds through ``json.dumps``, which cannot take one. Restly
    owns the schema-to-model translation, so it does the dump here rather than
    letting the driver fail at flush with the model in the bind parameters.
    """
    if isinstance(value, pydantic.BaseModel):
        if not _is_json_column(model_cls, field_name):
            return value
        _reject_excluded_json_fields(value, f"{model_cls.__name__}.{field_name}")
        return value.model_dump(mode="json")
    if isinstance(value, list | tuple) and any(
        isinstance(item, pydantic.BaseModel) for item in value
    ):
        if not _is_json_column(model_cls, field_name):
            return value
        _reject_excluded_json_fields(value, f"{model_cls.__name__}.{field_name}")
        return [
            item.model_dump(mode="json")
            if isinstance(item, pydantic.BaseModel)
            else item
            for item in value
        ]
    return value


def _reject_excluded_json_fields(value: Any, column: str) -> None:
    """Reject exclusions and document values that cannot be inspected safely."""
    pending = [value]
    seen: set[int] = set()
    while pending:
        item = pending.pop()
        if dataclasses.is_dataclass(item) or isinstance(item, Iterator):
            raise RestlyConfigurationError(
                f"{column}: documents containing dataclasses or iterators cannot "
                "be stored automatically in a plain JSON column. Use a SQLAlchemy "
                "TypeDecorator to define their storage representation."
            )
        if isinstance(item, str | bytes | bytearray) or not isinstance(
            item, (pydantic.BaseModel, Mapping, Sequence, Set, Enum)
        ):
            continue
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, pydantic.BaseModel):
            for name, field in type(item).model_fields.items():
                if field.exclude:
                    raise RestlyConfigurationError(
                        f"{column}: {type(item).__name__}.{name} is excluded from "
                        "serialization. Documents with WriteOnly or "
                        "Field(exclude=True) fields cannot be stored automatically "
                        "in a plain JSON column. Use a SQLAlchemy TypeDecorator "
                        "to define their storage representation."
                    )
                pending.append(getattr(item, name, None))
            if item.model_extra:
                pending.extend(item.model_extra.values())
        elif isinstance(item, Mapping):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, Enum):
            pending.append(item.value)
        else:
            pending.extend(item)


def _add_assignment(target: dict[str, Any], field_name: str | None, value: Any) -> None:
    if field_name:
        target[field_name] = value


_EXPLICIT_NULL_REF = object()


def _reference_identity(value: Any) -> tuple[type[Any] | None, Any] | object | None:
    if value is None:
        return _EXPLICIT_NULL_REF
    if is_mapped_instance(value):
        return type(value), getattr(value, "id", None)
    if isinstance(value, IDSchema):
        sql_model = value.get_sql_model_annotation()
        return sql_model, value.id
    return None


def _reference_identity_detail(identity: object) -> Any:
    if identity is _EXPLICIT_NULL_REF:
        return None
    if isinstance(identity, tuple) and len(identity) == 2:
        return identity[1]
    return identity


def validate_resolved_reference_consistency(
    model_cls: type[Any],
    schema_obj: pydantic.BaseModel,
    resolved: dict[str, Any] | None = None,
) -> None:
    """Validate explicitly supplied FK and relationship fields agree.

    IDRef/IDSchema resolution looks model-aware references up as ORM objects
    (in ``resolved``) before object construction/update. If the client supplied
    both ``author_id`` and ``author`` independently, they must refer to the same
    row. ``resolved`` is the ``{field: object}`` mapping from the resolver; a
    field absent from it keeps its (unresolved) value on ``schema_obj``.
    """
    schema_cls = type(schema_obj)
    resolved = resolved or {}

    for fk_field in schema_obj.model_fields_set:
        # Start from the FK-column side of a reference pair and derive its
        # partner relationship from the mapper; relationship-named fields are
        # reached as the partner, not iterated here.
        if not is_reference_field(schema_cls, fk_field):
            continue
        if _get_relationship_property(model_cls, fk_field) is not None:
            continue

        relation_field = _relationship_name_for_fk(model_cls, fk_field)
        if (
            relation_field is None
            or relation_field not in schema_obj.model_fields_set
            or not is_reference_field(schema_cls, relation_field)
        ):
            continue

        fk_identity = _reference_identity(
            resolved.get(fk_field, getattr(schema_obj, fk_field, None))
        )
        relation_identity = _reference_identity(
            resolved.get(relation_field, getattr(schema_obj, relation_field, None))
        )
        if fk_identity is None or relation_identity is None:
            continue

        if fk_identity == relation_identity:
            continue

        raise fastapi.HTTPException(
            status_code=422,
            detail=(
                f"Conflicting references for {fk_field} and {relation_field}: "
                f"{_reference_identity_detail(fk_identity)!r} != "
                f"{_reference_identity_detail(relation_identity)!r}"
            ),
        )


def iter_creatable_fields(schema_obj: pydantic.BaseModel) -> Iterator[tuple[str, Any]]:
    """Iterate over (field_name, value) pairs that should be used to construct a new
    ORM object from ``schema_obj``.

    Fields marked as ``ReadOnly`` are skipped. Unlike :func:`get_writable_inputs`,
    this also includes fields that were not explicitly provided, so that
    schema-level defaults end up on the new object.
    """
    schema_cls = type(schema_obj)
    for field_name, value in schema_obj:
        if is_readonly_field(schema_cls, field_name):
            continue
        yield field_name, value


def _add_null_reference_to_create_plan(
    plan: _CreatePlan, model_cls: type[Any], field_name: str
) -> None:
    """Write a null reference (explicit ``post=None``, or an omitted field
    defaulting to None) into the plan.

    Unlike a resolved reference, a null writes only the field's own slot, plus
    the partner FK column when the dataclass ``__init__`` requires it. It never
    mirrors further, and never overwrites a slot another field already planned:
    schemas may declare both names of an FK/relationship pair as reference
    fields, and the unset sibling's default None must not clobber the side the
    client supplied. (Later fields overwrite a planned None naturally, so the
    ``setdefault`` guards are order-independent. A relationship the input did
    not supply is left to :func:`build_create_plan`, which passes None only
    where ``__init__`` requires it and has it unset after construction.)
    """
    relationship = _get_relationship_property(model_cls, field_name)
    if relationship is None and not _is_mapped_column(model_cls, field_name):
        return  # unmapped reference name; nothing to write

    if _accepts_init_kwarg(model_cls, field_name):
        plan.kwargs.setdefault(field_name, None)
    elif _has_model_attr(model_cls, field_name):
        plan.post_assignments.setdefault(field_name, None)

    if relationship is None:
        return

    # A required-init partner FK column must be constructed even for a null
    # reference, or ``__init__`` rejects the missing kwarg. A composite-FK
    # relationship has no single partner column — but a null also has no id to
    # route, so there is nothing to infer; skip instead of raising.
    try:
        fk_name = _get_unambiguous_local_fk_name(model_cls, field_name)
    except ValueError:
        return
    if fk_name and _requires_init_kwarg(model_cls, fk_name):
        plan.kwargs.setdefault(fk_name, None)


def _add_resolved_reference_to_create_plan(
    plan: _CreatePlan, model_cls: type[Any], field_name: str, value: Any
) -> None:
    # ``cast`` only; ``.id`` is accessed lazily inside the branches so a
    # composite-keyed relationship raises its descriptive ValueError (from
    # the single-FK inference) before any ``.id`` attribute error.
    ref = cast(_HasID, value)
    # Route by what the field maps to on the model (mapper introspection), not
    # by its name: a relationship gets the ORM object, a scalar FK column gets
    # the row's id. The partner attribute is derived from the mapper so this
    # works for any column name, not only the ``<relation>_id`` convention.
    relationship = _get_relationship_property(model_cls, field_name)
    if relationship is not None:
        relation_name = field_name
        fk_name = _get_unambiguous_local_fk_name(model_cls, relation_name)

        # A required-init FK column must be constructed, not post-assigned, or
        # the dataclass __init__ rejects the missing kwarg. Pass its id at
        # construction: alongside the relationship object when that too is an
        # init kwarg (consistent ids), or instead of it when it isn't.
        if fk_name and _requires_init_kwarg(model_cls, fk_name):
            plan.kwargs[fk_name] = ref.id
            if _accepts_init_kwarg(model_cls, relation_name):
                plan.kwargs[relation_name] = value
            else:
                plan.post_assignments[relation_name] = value
            return

        if _accepts_init_kwarg(model_cls, relation_name):
            plan.kwargs[relation_name] = value
            _add_assignment(plan.post_assignments, fk_name, ref.id)
            return

        if fk_name and _accepts_init_kwarg(model_cls, fk_name):
            plan.kwargs[fk_name] = ref.id
            plan.post_assignments[relation_name] = value
            return

        plan.post_assignments[relation_name] = value
        _add_assignment(plan.post_assignments, fk_name, ref.id)
        return

    if not _is_mapped_column(model_cls, field_name):
        # Reference field that maps to neither a relationship nor a column;
        # there is nothing to write (matches the relationship branch's drop of
        # unmapped names).
        return

    fk_name = field_name
    relation_name = _relationship_name_for_fk(model_cls, fk_name)

    if relation_name is None:
        if _accepts_init_kwarg(model_cls, fk_name):
            plan.kwargs[fk_name] = ref.id
        else:
            plan.post_assignments[fk_name] = ref.id
        return

    # A partner relationship exists; keep the FK column and the relationship
    # object consistent, honoring dataclass init requirements on either side.
    accepts_relation = _accepts_init_kwarg(model_cls, relation_name)
    relation_required = _requires_init_kwarg(model_cls, relation_name)

    if (
        _requires_init_kwarg(model_cls, fk_name)
        and accepts_relation
        and relation_required
    ):
        plan.kwargs[fk_name] = ref.id
        plan.kwargs[relation_name] = value
        return

    if accepts_relation and relation_required:
        plan.kwargs[relation_name] = value
        plan.post_assignments[fk_name] = ref.id
        return

    if _accepts_init_kwarg(model_cls, fk_name):
        plan.kwargs[fk_name] = ref.id
        plan.post_assignments[relation_name] = value
        return

    if accepts_relation:
        plan.kwargs[relation_name] = value
        plan.post_assignments[fk_name] = ref.id
        return

    plan.post_assignments[fk_name] = ref.id
    plan.post_assignments[relation_name] = value


def build_create_plan(
    model_cls: type[Any],
    schema_obj: pydantic.BaseModel,
    resolved: dict[str, Any] | None = None,
) -> _CreatePlan:
    """Translate ``schema_obj`` fields into kwargs for ``model_cls(**kwargs)``.

    Shared by sync and async ``make_new_object``. ``resolved`` is the
    ``{field: object_or_list}`` mapping returned by the IDSchema resolver (sync
    vs async); a resolved reference field uses that ORM value instead of the
    wire-shaped ``IDRef`` still on ``schema_obj``.
    """
    schema_cls = type(schema_obj)
    resolved = resolved or {}

    plan = _CreatePlan(kwargs={}, post_assignments={})
    for field_name, value in iter_creatable_fields(schema_obj):
        if field_name in resolved:
            value = resolved[field_name]
        if isinstance(value, IDSchema) and _is_mapped_column(model_cls, field_name):
            if _accepts_init_kwarg(model_cls, field_name):
                plan.kwargs[field_name] = value.id
            else:
                plan.post_assignments[field_name] = value.id
            continue
        if value is None and is_reference_field(schema_cls, field_name):
            _add_null_reference_to_create_plan(plan, model_cls, field_name)
            continue
        if is_mapped_instance(value) and is_reference_field(schema_cls, field_name):
            _add_resolved_reference_to_create_plan(plan, model_cls, field_name, value)
            continue

        value = _json_ready(model_cls, field_name, value)
        if _accepts_init_kwarg(model_cls, field_name):
            plan.kwargs[field_name] = value
        elif _has_model_attr(model_cls, field_name):
            plan.post_assignments[field_name] = value

    plan.unsupplied_relationships = [
        name
        for name in _many_to_one_names(model_cls)
        if name not in schema_obj.model_fields_set
        or is_readonly_field(schema_cls, name)
    ]
    for name in plan.unsupplied_relationships:
        # __init__ demands the argument; the None is unset after construction.
        if _requires_init_kwarg(model_cls, name):
            plan.kwargs.setdefault(name, None)
    return plan


def apply_create_assignments(obj: Any, plan: _CreatePlan) -> None:
    """Finish an object built from ``plan.kwargs``.

    Sets the attributes the constructor cannot take, then unsets each
    many-to-one relationship the input did not supply that holds ``None``.
    That ``None`` is a default: the dataclass ``__init__`` assigns
    ``relationship(default=None)``, and the plan passes ``None`` for an
    omitted reference field or a required relationship argument. SQLAlchemy's
    flush would copy it over the foreign key, discarding an id the input did
    supply. Unset, the relationship takes no part in the flush, as SQLAlchemy
    2.1 treats dataclass defaults. A ``null`` the client sent stays.
    """
    for field_name, value in plan.post_assignments.items():
        setattr(obj, field_name, value)
    loaded = sa_inspect(obj).dict
    for name in plan.unsupplied_relationships:
        if name in loaded and loaded[name] is None:
            del_attribute(obj, name)


def _apply_resolved_reference_update(obj: Any, field_name: str, value: Any) -> None:
    ref = cast(_HasID, value)
    model_cls = type(obj)
    relationship = _get_relationship_property(model_cls, field_name)
    if relationship is None:
        # Scalar FK column: write the id, and mirror onto the partner
        # relationship (derived from the mapper) when there is one.
        if not _is_mapped_column(model_cls, field_name):
            return
        setattr(obj, field_name, ref.id)
        relation_name = _relationship_name_for_fk(model_cls, field_name)
        if relation_name is not None:
            setattr(obj, relation_name, value)
        return

    # Relationship field: assign the object, mirror onto the local FK if any.
    setattr(obj, field_name, value)
    fk_name = _get_unambiguous_local_fk_name(model_cls, field_name)
    if fk_name:
        setattr(obj, fk_name, ref.id)


def apply_update_to_object(
    obj: Any, schema_obj: pydantic.BaseModel, resolved: dict[str, Any] | None = None
) -> None:
    """Apply writable inputs from ``schema_obj`` onto ``obj`` in place.

    Shared by sync and async ``update_object``. ``resolved`` is the
    ``{field: object_or_list}`` mapping returned by the IDSchema resolver (sync
    vs async); a resolved reference field uses that ORM value instead of the
    wire-shaped ``IDRef`` still on ``schema_obj``.
    """
    schema_cls = type(schema_obj)
    resolved = resolved or {}
    for field_name, value in get_writable_inputs(schema_obj).items():
        if field_name in resolved:
            value = resolved[field_name]
        if isinstance(value, IDSchema) and _is_mapped_column(type(obj), field_name):
            setattr(obj, field_name, value.id)
            continue
        if is_mapped_instance(value) and is_reference_field(schema_cls, field_name):
            _apply_resolved_reference_update(obj, field_name, value)
            continue
        setattr(obj, field_name, _json_ready(type(obj), field_name, value))


def _get_nested_schema_annotation(annotation: Any) -> type[pydantic.BaseModel] | None:
    annotation = _unwrap_optional_annotation(annotation)

    try:
        if inspect.isclass(annotation) and issubclass(annotation, pydantic.BaseModel):
            return annotation
    except TypeError:
        pass

    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        if args:
            return _get_nested_schema_annotation(args[0])

    return None


class _OmitWriteOnlyMixin(pydantic.BaseModel):
    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)

        writeonly_fields = [
            name for name in cls.model_fields if is_writeonly_field(cls, name)
        ]
        for name in writeonly_fields:
            del cls.model_fields[name]

        cls.model_rebuild(force=True)


@functools.cache
def _create_response_validation_schema(
    schema_cls: type[pydantic.BaseModel],
) -> type[pydantic.BaseModel]:
    if not any(
        is_writeonly_field(schema_cls, name) for name in schema_cls.model_fields
    ):
        return schema_cls

    return type(
        f"Response{schema_cls.__name__}",
        (_OmitWriteOnlyMixin, schema_cls),
        {
            "__module__": schema_cls.__module__,
            "__doc__": (schema_cls.__doc__ or "")
            + "\nWrite-only fields have been removed for response validation.",
        },
    )


def _generated_primary_key_fields(model_cls: type[Any]) -> frozenset[str]:
    """The primary key attributes the server generates, never the client.

    A derived create or update schema leaves these out even when the schema
    does not mark them ReadOnly. Generated means an autoincrement column, a
    column default, or a dataclass field outside ``__init__``. A key without
    any of these is a natural key, and the client supplies it.
    """
    mapper = sa_inspect(model_cls)
    autoincrement = getattr(mapper.local_table, "autoincrement_column", None)
    not_in_init = (
        {f.name for f in dataclasses.fields(model_cls) if not f.init}
        if dataclasses.is_dataclass(model_cls)
        else set()
    )
    generated = set()
    for column in mapper.primary_key:
        key = mapper.get_property_by_column(column).key
        if (
            column is autoincrement
            or column.default is not None
            or column.server_default is not None
            or key in not_in_init
        ):
            generated.add(key)
    return frozenset(generated)


@functools.cache
def _response_validation_adapter(
    schema_cls: type[pydantic.BaseModel],
) -> pydantic.TypeAdapter[pydantic.BaseModel]:
    return pydantic.TypeAdapter(_create_response_validation_schema(schema_cls))


def _build_relationship_loader_options(
    model_cls: type[Any],
    schema_cls: type[pydantic.BaseModel],
    seen: set[tuple[type[Any], type[pydantic.BaseModel]]] | None = None,
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


def _relationship_reload_statement(obj: Any, options: list[Any]) -> Any:
    """A primary-key SELECT that fills ``obj``'s unloaded relationships.

    Deliberately *not* ``populate_existing``: that would expire every
    relationship the options do not name and overwrite the ones they do, so a
    value the caller had already put there would be replaced by a fresh read.
    Without it the loaders fill only what is unloaded, which is exactly what
    serialization would otherwise have lazy-loaded.
    """
    state = sa_inspect(obj)
    if state.identity is None:
        return None
    mapper = state.mapper
    conditions = [
        column == value
        for column, value in zip(mapper.primary_key, state.identity, strict=True)
    ]
    return sa_select(mapper.class_).where(*conditions).options(*options)


def _schema_relationships_are_loaded(
    obj: Any,
    model_cls: type[Any],
    schema_cls: type[pydantic.BaseModel],
    seen: set[tuple[int, type[pydantic.BaseModel]]] | None = None,
) -> bool:
    """Is every relationship ``schema_cls`` reaches already loaded on ``obj``?

    Walks the same model/schema pairing as
    :func:`_build_relationship_loader_options`, so it answers exactly the
    question "would serializing this object need IO?". Reads only the committed
    attribute dict, never the descriptors, so asking cannot itself emit a query.
    """
    if seen is None:
        seen = set()
    # Dedup on (instance, schema), not the instance alone: the same object can
    # be reached under two response sub-schemas that name different
    # relationships, and each pairing must be checked. Mirrors the
    # (model, schema) dedup in _build_relationship_loader_options.
    key = (id(obj), schema_cls)
    if key in seen:
        return True
    seen.add(key)

    state = sa_inspect(obj, raiseerr=False)
    if state is None:
        return True

    mapper = sa_inspect(model_cls)
    for field_name, field_info in schema_cls.model_fields.items():
        if field_name not in mapper.relationships:
            continue
        if field_name in state.unloaded:
            return False

        nested_schema = _get_nested_schema_annotation(field_info.annotation)
        if nested_schema is None:
            continue

        value = state.dict.get(field_name)
        if value is None:
            continue
        relationship_prop = mapper.relationships[field_name]
        related = value if relationship_prop.uselist else [value]
        for item in related:
            if not _schema_relationships_are_loaded(
                item, relationship_prop.mapper.class_, nested_schema, seen
            ):
                return False

    return True


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

    #: The URL prefix of every route. Each class in the hierarchy that sets one
    #: adds a segment, base first.
    prefix: ClassVar[str]
    tags: ClassVar[Iterable[str | Enum] | None] = None
    #: FastAPI dependencies run for every route, without injecting a result.
    #: Each class in the hierarchy adds its own, base first, so a subclass
    #: cannot drop a base's guard. A subclass that lists a base's entry again
    #: runs it where it lists it.
    dependencies: ClassVar[Any] = None
    #: OpenAPI responses documented on every route. Each class in the hierarchy
    #: adds its own, and a subclass's entry for a status code wins.
    responses: ClassVar[dict[int | str, dict[str, Any]]] = {}
    #: FastAPI route keyword arguments keyed by endpoint method name or
    #: ``ViewRoute``. These override decorator metadata without replacing the
    #: endpoint method. A subclass inherits this mapping unless it declares
    #: its own, which replaces it.
    route_options: ClassVar[Mapping[str | ViewRoute, Mapping[str, Any]]] = {}

    if TYPE_CHECKING:
        # include_view replaces __init__: each injected attribute becomes a
        # keyword, so code that builds a view by hand passes them by name.
        def __init__(self, **dependencies: Any) -> None: ...

    @classmethod
    def before_include_view(cls) -> None:
        """Run by :func:`include_view` once per class, before its routes are
        registered. A no-op here; override to adjust route methods first."""


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


def route(path: str, **api_route_kwargs: Any) -> Callable[..., Any]:
    """Decorator to mark a View method as an endpoint.
    The path and api_route_kwargs are passed into APIRouter.add_api_route(), see for example:
    https://fastapi.tiangolo.com/reference/apirouter/#fastapi.APIRouter.get

    Endpoints methods are later added as routes to the FastAPI app using `include_view()`
    """

    def store_args_decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        # A new declaration takes precedence over metadata copied by wraps().
        func.__dict__.pop("_fr_typed_id_route", None)
        # Create a new attribute: '_api_route_args'
        func._api_route_args = (path, api_route_kwargs)  # type: ignore[attr-defined]
        return func

    return store_args_decorator


def _typed_id_route(endpoint: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a built-in item route whose copied path follows the view's ID type."""
    endpoint._fr_typed_id_route = True  # type: ignore[attr-defined]
    return endpoint


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


# Callable from a verb override, never overridden. The framework's own view
# classes define them; anything else in the MRO that does is a mistake.
_FINAL_DOMAIN_UTILITIES = ("make_new_object", "update_object", "save_object")

# Callable from a custom route, never overridden. Every generated CRUD route
# then runs ``authorize`` and, on writes, the commit bracket.
_FINAL_HANDLERS = (
    "handle_get_many",
    "handle_get_one",
    "handle_create",
    "handle_update",
    "handle_delete",
)
_WRITE_VERBS = ("create", "update", "delete")


def _final_handler_message(view_name: str, name: str, origin: str) -> str:
    """What a subclass that defines ``name`` is told to use instead."""
    verb = name.removeprefix("handle_")
    message = (
        f"{view_name} defines {name}{origin}, which is a final handler, not "
        f"a seam: it is the tier a custom route calls. Put domain logic in "
        f"the {verb} business method, a gate in authorize, "
    )
    if verb in _WRITE_VERBS:
        message += "a side effect in before_action_commit or after_action_commit, "
    message += f"and the HTTP contract in {verb}_endpoint."
    if verb in _WRITE_VERBS:
        message += (
            f" To put several writes in one commit, call {name} inside "
            "shared_write_action_commit()."
        )
    return message


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
                origin = "" if klass is cls else f" (from {klass.__name__})"
                found[name] = (vars(klass)[name], origin)
    if found:
        raise RestlyConfigurationError(
            _replaced_pagination_message(cls.__name__, found)
        )
    pagination = getattr(cls, "pagination", None)
    if not (
        pagination is None or isinstance(pagination, (NumberedPagination, NoPagination))
    ):
        hint = (
            f" Create an instance: fr.{pagination.__name__}()."
            if pagination in (NumberedPagination, NoPagination)
            else ""
        )
        raise RestlyConfigurationError(
            f"{cls.__name__}.pagination must be a fr.NumberedPagination or "
            f"fr.NoPagination instance, or None, got {pagination!r}.{hint}"
        )


# Renamed before 1.0. Restly no longer reads the old names, so a definition
# would be silently dead, or would fail only on a request.
_RENAMED_SETTINGS = {"listing_param_schema": "schema_list_params"}
_RENAMED_METHODS = {
    "to_listing_response": "to_list_response",
    "to_response_schema": "to_single_response",
    "apply_query_params": "apply_list_params",
}
_RENAMED_METHOD_HINTS = {
    "to_listing_response": (
        " It takes only the list result: the list params are in "
        "list_result.list_params."
    ),
    "apply_query_params": " Its arguments are (query, list_params).",
}


def _check_renamed_names(cls: type) -> None:
    """Reject a setting, a method or a route parameter that still uses a name
    from before the rename. Runs when the class is defined."""
    for klass in cls.__mro__:
        origin = "" if klass is cls else f" (from {klass.__name__})"
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
        for name, value in vars(klass).items():
            if not hasattr(value, "_api_route_args"):
                continue
            if "query_params" in inspect.signature(value).parameters:
                raise RestlyConfigurationError(
                    f"{cls.__name__}.{name}{origin} declares a query_params "
                    "parameter, which was renamed to list_params. Rename the "
                    "parameter, so the route still takes the filter, sort and "
                    "page parameters."
                )


class BaseRestView(View, Generic[ModelT, SchemaT, CreateSchemaT, UpdateSchemaT, IdT]):
    """
    Base class for RestView implementations.

    This class contains the common functionality shared between AsyncRestView
    and RestView, including schema definitions, model configuration, and
    common CRUD operation logic.
    """

    responses: ClassVar[dict[int | str, dict[str, Any]]] = {
        404: {"description": "Not found"}
    }

    schema: ClassVar[type[pydantic.BaseModel]]
    # If 'schema_create' is not defined it will be created from 'schema'
    # using `create_model_without_read_only_fields()`.
    schema_create: ClassVar[type[pydantic.BaseModel]]
    schema_update: ClassVar[type[pydantic.BaseModel]]
    model: ClassVar[type[Any]]
    #: The clause every read on this view applies: list, count, and retrieve
    #: (a row outside it is 404). ``None`` (the default) falls back to the
    #: model's declared ``default_scope``; ``fr.clauses.UNSCOPED`` reads
    #: unscoped despite that default. Declaring a scope replaces the default,
    #: it does not stack on it; compose the replacement from the same leaves
    #: (``ItemClauses.trashed`` containing the tenant clause ``visible``
    #: contains). A rule that must hold under every scope is a session-level
    #: ``with_loader_criteria``, not a view concern. See the Scopes guide.
    scope: ClassVar[WhereClause | Unscoped | None] = None
    #: The type of the ``{id}`` path parameter on the default routes. ``None``
    #: (the default) takes the Python type of the model's primary key, and a
    #: composite key gets ``int``. Set a type to override it. Built-in item
    #: routes use Starlette's ``int`` or ``uuid`` path converter for those
    #: types. Integer paths accept non-negative digits. An unmatched value
    #: falls through to another route or returns 404, instead of 422.
    id_type: ClassVar[type[Any] | None] = None
    exclude_routes: ClassVar[Iterable[str | ViewRoute]] = ()
    #: Extra query-parameter keys to allow on list routes beyond those
    #: derived from the view's schema. Use this when a view reads a custom
    #: parameter from ``self.request`` (e.g. ``?verbose=true``). Without this,
    #: the strict unknown-key guard rejects the request with 422. A key that the
    #: route declares, on the endpoint method or in a dependency, or an
    #: ``APIKeyQuery`` key, needs no entry.
    extra_query_params: ClassVar[Iterable[str]] = ()
    #: How list endpoints paginate. The default :class:`NumberedPagination`
    #: takes ``page`` / ``page_size`` query parameters, runs the count query,
    #: and wraps the list in its ``envelope`` (:class:`PaginatedEnvelope`:
    #: ``data`` plus ``total_count`` / ``page`` / ``page_size`` /
    #: ``total_pages``). A :class:`NoPagination` returns every matching row
    #: with no count, in its ``envelope``; ``None`` is short for
    #: ``NoPagination()``, a plain :class:`Envelope` (``data`` only). A view
    #: inherits it, so a project base view sets it once; a view that differs
    #: changes one setting with ``replace()`` on the shared settings. For a
    #: shape no envelope model can express, such as a header, replace
    #: ``get_many_endpoint`` with a matching ``response_model`` (see
    #: :class:`AsyncReactAdminView`).
    pagination: ClassVar[NumberedPagination | NoPagination | None] = _DEFAULT_PAGINATION
    #: The list params (filter, sort, page) as a pydantic model, generated
    #: from ``schema`` and ``model`` by
    #: :func:`~fastapi_restly.query.derive_schema_list_params`. Any route
    #: method on the view that declares a ``list_params`` parameter takes it:
    #: typed for FastAPI and OpenAPI, and guarded against unknown keys like
    #: ``GET /``, so a custom list route (a trash route naming its own scope)
    #: reads the same list params. The route can take other query parameters
    #: beside it.
    schema_list_params: ClassVar[type[pydantic.BaseModel]]

    request: fastapi.Request

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # validate at class definition, so a raw expression fails at
        # import instead of on the first read
        scope = cls.__dict__.get("scope")
        if not (scope is None or scope is UNSCOPED or isinstance(scope, WhereClause)):
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
                origin = "" if klass is cls else f" (from {klass.__name__})"
                raise RestlyConfigurationError(
                    f"{cls.__name__} defines build_query{origin}, which is "
                    "removed and no longer called. Declare visibility as a "
                    "clause: default_scope on the model's ClauseNamespace, or "
                    "the scope attribute on the view. Reshape a list in "
                    "apply_list_params, and relationship loading in "
                    "get_relationship_loader_options. See Migrating from "
                    "build_query in the Scopes guide."
                )
        # delete_object is removed; a stale soft-delete override would be
        # silently dead code, and a dead soft delete is a hard delete
        for klass in cls.__mro__:
            if "delete_object" in vars(klass):
                origin = "" if klass is cls else f" (from {klass.__name__})"
                raise RestlyConfigurationError(
                    f"{cls.__name__} defines delete_object{origin}, which is "
                    "removed and no longer called. Override delete instead: a "
                    "soft delete flips a timestamp there. A raw row delete is "
                    "fr.objects.delete_object / async_delete_object on "
                    "self.session."
                )
        # the domain utilities are final: a stamp hooked there only covered
        # the verbs, and a definition here would shadow the framework's
        for klass in cls.__mro__:
            if klass.__module__.startswith("fastapi_restly."):
                continue
            for name in _FINAL_DOMAIN_UTILITIES:
                if name in vars(klass):
                    origin = "" if klass is cls else f" (from {klass.__name__})"
                    raise RestlyConfigurationError(
                        f"{cls.__name__} defines {name}{origin}, which is a "
                        "final domain utility, not a seam. Stamp a "
                        "server-controlled field with a column default on the "
                        "model (it then covers every write path); put "
                        "payload-derived logic in the create / update verb; "
                        "put a per-write side effect in before_action_commit."
                    )
        # the handlers are final: an override re-implements the load,
        # authorize and commit order to reach something another seam gives
        # directly, and a generated route could then skip either
        for klass in cls.__mro__:
            if klass.__module__.startswith("fastapi_restly."):
                continue
            for name in _FINAL_HANDLERS:
                if name in vars(klass):
                    origin = "" if klass is cls else f" (from {klass.__name__})"
                    raise RestlyConfigurationError(
                        _final_handler_message(cls.__name__, name, origin)
                    )
        _check_pagination_setting(cls)
        _check_renamed_names(cls)

    def _apply_scope(self, query: Select[Any], scope: ReadScope) -> Select[Any]:
        # the one path every read takes, so retrieve, list and count cannot
        # disagree about which rows exist; not an override point
        if scope is None:
            scope = resolve_scope(self)
        else:
            # a per-read scope replaces the resolution rather than joining it
            scope = _checked_scope(scope, type(self).__name__, "a per-read scope")
        return apply_clauses(query, scope)

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

    def get_relationship_loader_options(self) -> list[Any]:
        """Loader options for the relationships the view's schema names.

        Returns recursive ``selectinload(...)`` options derived from
        ``self.schema``, applied on reads (``get_one`` / ``get_many``) and on
        the post-write reload in ``save_object``. Override to eager-load
        relationships the schema does not name on both paths; append to
        ``super().get_relationship_loader_options()`` to keep the schema-derived
        loads. See the "Relationship Loading and Async" how-to.
        """
        return _build_relationship_loader_options(self.model, self.schema)

    def _get_response_reload_statement(self, obj: Any) -> Any:
        """The SELECT that makes ``obj`` serializable, or ``None`` if it already is.

        Called by ``save_object`` *after* the flush and refresh, because the
        refresh is itself what leaves relationships unloaded; asking earlier
        would see a value the refresh is about to discard.
        """
        if _schema_relationships_are_loaded(obj, self.model, self.schema):
            return None
        options = self.get_relationship_loader_options()
        if not options:
            return None
        return _relationship_reload_statement(obj, options)

    def _reject_unknown_query_params(self) -> None:
        """Reject any query-string key that neither the list params nor the
        route reads.

        Runs before every route that declares ``list_params`` (see
        :attr:`schema_list_params`).

        The list params dependency (:func:`_list_params_dependency`) reads
        their keys and ignores any other, which would let typoed
        filters or unsupported operators (e.g.
        ``active__gte=true`` on a boolean column where the schema does not
        emit a range operator) widen the result set without telling the
        caller. We treat unknown keys as a validation error instead, mirroring
        FastAPI's 422 envelope shape so the response is consistent with
        bound-violation errors.

        No-op when there is no live request, as when code calls a route
        method directly, outside an HTTP request. Then there is no URL to
        check, and the caller is responsible for what it passes.
        """
        request = getattr(self, "request", None)
        if request is None:
            return
        schema_list_params = getattr(self, "schema_list_params", None)
        if schema_list_params is None:
            return
        list_keys = {
            _query_key(name, field)
            for name, field in schema_list_params.model_fields.items()
        }
        reject_unknown_query_keys(request, list_keys | set(self.extra_query_params))

    def to_single_response(self, obj: ModelT | SchemaT) -> SchemaT:
        """Serialize one ORM object to the view's schema.

        An override returns an instance of :attr:`schema`: the list envelopes
        take their items as schema instances.

        WriteOnly fields are stripped from responses by ``exclude=True`` on the
        marker itself (recursively, at serialization time), so a pre-built schema
        instance is safe to return as-is. The ORM path below still validates
        through the WriteOnly-omitting response schema, so a view's schema that
        declares a WriteOnly field the ORM object doesn't carry (e.g. ``password``
        backed by a ``password_hash`` column) doesn't fail response validation.
        """
        if isinstance(obj, self.schema):
            return cast(SchemaT, obj)

        # Build a payload of raw attribute values keyed by schema field name;
        # re-validating it below serializes each field through its own type. The
        # response schema's ``from_attributes`` config resolves nested schemas and
        # reference types (IDRef/IDSchema) straight from the ORM rows, with no
        # view-layer special-casing. Alias rendering happens when FastAPI
        # serializes the response model.
        payload: dict[str, Any] = {}
        for field_name, field_info in self.schema.model_fields.items():
            if is_writeonly_field(self.schema, field_name):
                continue
            if hasattr(obj, field_name):
                payload[field_name] = getattr(obj, field_name)
            elif field_info.alias and hasattr(obj, field_info.alias):
                payload[field_name] = getattr(obj, field_info.alias)

        # through the adapter, not model_validate: a schema may narrow that
        # classmethod's signature (SQLModel drops by_alias and by_name)
        adapter = _response_validation_adapter(self.schema)
        return cast(
            SchemaT, adapter.validate_python(payload, by_alias=False, by_name=True)
        )

    @staticmethod
    def _to_query_params(list_params: Any) -> QueryParams:
        if isinstance(list_params, QueryParams):
            return list_params
        if isinstance(list_params, pydantic.BaseModel):
            dumped = list_params.model_dump(
                exclude_none=True, by_alias=True, mode="json"
            )
            return QueryParams({k: str(v) for k, v in dumped.items()})
        if isinstance(list_params, dict):
            return QueryParams({k: str(v) for k, v in list_params.items()})
        return QueryParams(list_params)

    def to_list_response(self, list_result: ListResult[ModelT]) -> Any:
        """Build the list response body: an instance of the envelope model.

        The view fills its :attr:`pagination`'s ``envelope``: by default a
        :class:`PaginatedEnvelope` (``data`` plus ``total_count`` / ``page`` /
        ``page_size`` / ``total_pages``), or an :class:`Envelope` (``data``
        only) for a view without pagination. The route's ``response_model`` is
        the same envelope, fixed from :attr:`pagination` at registration. To
        change the shape, set the pagination's ``envelope``. For a
        ``Content-Range`` header, replace ``get_many_endpoint`` with a matching
        ``response_model`` (as :class:`AsyncReactAdminView` does), not by
        overriding this method alone, which would fail response validation.

        The page and page size come from ``list_result.list_params``.
        """
        data = [self.to_single_response(obj) for obj in list_result.objects]
        pagination = self.pagination
        envelope = _list_envelope(pagination, self.schema)
        if not isinstance(pagination, NumberedPagination):
            return _build_envelope(envelope, _unpaginated_page_info(data))
        params = self._to_query_params(list_result.list_params)
        page_info = _numbered_page_info(
            total_count=list_result.total_count or 0,
            page=int(params.get(pagination.page_query_param) or 1),
            page_size=int(
                params.get(pagination.page_size_query_param)
                or pagination.default_page_size
            ),
        )
        return _build_envelope(envelope, {"data": data, **page_info})

    def to_response(
        self, result: Any, shape: ResponseShape = ResponseShape.SINGLE
    ) -> Any:
        """Endpoint-method response boundary.

        ``shape`` selects the wire form, and with it what ``result`` is: one
        object for ``SINGLE`` (:meth:`to_single_response`), a
        :class:`ListResult` for ``LIST`` (:meth:`to_list_response`), and
        nothing for ``EMPTY``. A plain Python list is not a valid ``result``.
        ``shape`` is not the write-action name. Override for envelopes or
        shape-wide status behavior; per-endpoint projections belong in the
        endpoint method.
        """
        if shape is ResponseShape.EMPTY:
            return fastapi.Response(status_code=204)
        if shape is ResponseShape.LIST:
            return self.to_list_response(result)
        return self.to_single_response(result)

    def snapshot(self, obj: Any) -> dict[str, Any]:
        """Frozen capture of an object's already-loaded column values, passed as
        ``old`` to ``before_action_commit`` / ``after_action_commit`` for dirty detection.
        Override to change what ``old`` captures (e.g. include a relationship's
        prior state); the default delegates to
        :func:`~fastapi_restly.objects.snapshot`.
        """
        return _object_snapshot(obj)

    @classmethod
    def _check_declared_schema_list_params(cls) -> None:
        """Reject declared list params that ``derive_schema_list_params`` made
        for other pagination settings: they read other page parameters than
        the view applies, or none. Hand-written list params are the author's
        to keep in step."""
        created_with = _created_with(cls.schema_list_params)
        if created_with is _UNKNOWN:
            return
        if _query_settings(created_with) != _query_settings(cls.pagination):
            raise RestlyConfigurationError(
                f"{cls.__name__}.schema_list_params was created for other "
                "pagination settings than the view's. Create it with "
                f"pagination={cls.__name__}.pagination, or leave it out to "
                "have it generated."
            )

    @classmethod
    def before_include_view(cls):
        """
        Apply type annotations needed for FastAPI, before creating an APIRouter from
        this view and registering it.

        This function can be overridden to further tweak the endpoints before they
        are added to FastAPI.
        """
        _check_pagination_setting(cls)

        # A declared schema is inherited like any attribute. One that Restly
        # generated for a parent is rebuilt, since the subclass may change
        # what it was derived from.
        if _needs_generating(cls, "schema"):
            if not hasattr(cls, "model"):
                raise ValueError(
                    f"'{cls.__name__}.model' must be specified to auto-generate schema"
                )
            cls.schema = cast(type[SchemaT], derive_schema(cls.model))
            _mark_generated(cls, "schema")

        if _needs_generating(cls, "schema_list_params", derived_from="schema"):
            if not hasattr(cls, "model"):
                raise ValueError(
                    f"'{cls.__name__}.model' must be specified: it is needed to "
                    "generate the list params."
                )
            cls.schema_list_params = derive_schema_list_params(
                cls.schema, cls.model, pagination=cls.pagination
            )
            _mark_generated(cls, "schema_list_params")
        else:
            cls._check_declared_schema_list_params()
        if _needs_generating(cls, "schema_create", derived_from="schema"):
            cls.schema_create = cast(
                type[CreateSchemaT],
                create_model_without_read_only_fields(
                    cls.schema, omit=_generated_primary_key_fields(cls.model)
                ),
            )
            _mark_generated(cls, "schema_create")
        if _needs_generating(cls, "schema_update", derived_from="schema"):
            cls.schema_update = cast(
                type[UpdateSchemaT],
                create_model_with_optional_fields(
                    cls.schema, omit=_generated_primary_key_fields(cls.model)
                ),
            )
            _mark_generated(cls, "schema_update")

        # WriteOnly fields are excluded from responses by ``exclude=True`` on the
        # marker (recursively, and from the OpenAPI response schema, since FastAPI's
        # serialization-mode schema drops them), so the response_model can be the
        # full schema.
        response_schema = cls.schema
        id_type = _view_id_type(cls)
        if id_type is int:
            item_path = "/{id:int}"
        elif id_type is UUID:
            item_path = "/{id:uuid}"
        else:
            item_path = "/{id}"
        for endpoint in cls.__dict__.values():
            if getattr(endpoint, "_fr_typed_id_route", False):
                _, route_kwargs = endpoint._api_route_args
                endpoint._api_route_args = (item_path, route_kwargs.copy())

        # Only annotate if the methods exist (they will be overridden in subclasses)
        list_response_annotation: Any = _list_envelope(cls.pagination, response_schema)

        # Every route that declares ``list_params`` takes the view's list
        # params: ``GET /`` and any custom list route alike, guarded the same
        # way. The guard checks keys against ``schema_list_params`` whatever
        # the annotation says, so they replace an explicit annotation too. A
        # dependency reads them rather than a FastAPI query model, which
        # collapses next to any other query parameter (see
        # :func:`_list_params_dependency`).
        list_params = Annotated[
            cls.schema_list_params,
            fastapi.Depends(_list_params_dependency(cls.schema_list_params)),
        ]
        for name, route in list(cls.__dict__.items()):
            if not hasattr(route, "_api_route_args"):
                continue
            if "list_params" not in inspect.signature(route).parameters:
                continue
            if not getattr(route, "_fr_list_guard", False):
                route = _guard_list_params(route)
                setattr(cls, name, route)
            _annotate(route, overwrite=True, list_params=list_params)

        # The ``*_endpoint`` methods are defined on AsyncRestView/RestView
        # subclasses and may be excluded by ``exclude_routes``, so they aren't
        # visible on BaseRestView. ``getattr`` keeps pyright happy without
        # falsely advertising them on the base class.
        if (ep := getattr(cls, "get_many_endpoint", None)) is not None:
            _annotate(ep, return_annotation=list_response_annotation)
        if (ep := getattr(cls, "get_one_endpoint", None)) is not None:
            _annotate(ep, return_annotation=response_schema, id=id_type)
        if (ep := getattr(cls, "create_endpoint", None)) is not None:
            _annotate(
                ep, return_annotation=response_schema, schema_obj=cls.schema_create
            )
        if (ep := getattr(cls, "update_endpoint", None)) is not None:
            _annotate(
                ep,
                return_annotation=response_schema,
                schema_obj=cls.schema_update,
                id=id_type,
            )
        if (ep := getattr(cls, "delete_endpoint", None)) is not None:
            _annotate(ep, return_annotation=fastapi.Response, id=id_type)
        _exclude_routes(cls)


def _owner_index(cls: type, name: str) -> int | None:
    """Position in the MRO of the class that sets ``name``, or None."""
    for index, klass in enumerate(cls.__mro__):
        if name in klass.__dict__:
            return index
    return None


def _mark_generated(cls: type, name: str) -> None:
    generated = cls.__dict__.get("_fr_generated", frozenset())
    cls._fr_generated = generated | {name}  # type: ignore[attr-defined]


def _needs_generating(cls: type, name: str, *, derived_from: str | None = None) -> bool:
    """Whether registration must build ``name`` for this view class.

    A declared value is inherited. It is rebuilt when no class sets it, when a
    parent's value was generated, or when the attribute it derives from is set
    nearer to ``cls`` than the value itself.
    """
    index = _owner_index(cls, name)
    if index is None:
        return True
    owner = cls.__mro__[index]
    if name in owner.__dict__.get("_fr_generated", ()):
        return owner is not cls
    if derived_from is not None:
        source_index = _owner_index(cls, derived_from)
        return source_index is not None and source_index < index
    return False


#: A ``BaseRestView`` at any parameterization, for the signature below.
_AnyRestView = BaseRestView[Any, Any, Any, Any, Any]


def resolve_scope(target: type[Any] | _AnyRestView) -> WhereClause | Unscoped:
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

        query = fr.apply_clauses(
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
    if declared is UNSCOPED:
        return UNSCOPED
    # backstop for a post-definition assignment; the declared form is
    # validated in __init_subclass__
    return _checked_scope(declared, label, "the scope")


def _model_scope(model: type[Any]) -> WhereClause | Unscoped:
    declared = _default_scope(model)
    return UNSCOPED if declared is None else declared


def _checked_scope(scope: Any, label: str, what: str) -> WhereClause | Unscoped:
    if scope is UNSCOPED or isinstance(scope, WhereClause):
        return scope
    raise RestlyConfigurationError(
        f"{label}: {what} must be a WhereClause or fr.clauses.UNSCOPED, got "
        f"{type(scope).__name__}; wrap a raw expression with where_clause()"
    )


def _checked_where(where: Any, label: str) -> WhereClause | Unscoped:
    if where is UNSCOPED or isinstance(where, WhereClause):
        return where
    if isinstance(where, ColumnElement):
        return where_clause(where)
    if type(where) is bool:
        # ``obj.slug == slug`` on a loaded object is a Python bool, and
        # SQLAlchemy renders it as WHERE true, which narrows nothing
        raise TypeError(
            f"{label}: where= got a bool, not a SQL expression. A comparison "
            "on an instance attribute is a bool; compare the column instead "
            "(Model.slug == slug)."
        )
    raise TypeError(
        f"{label}: where= must be a SQLAlchemy boolean expression or a clause, "
        f"got {type(where).__name__}"
    )


def _identity_criterion(model_cls: type[Any], id: Any) -> ColumnElement[bool]:
    """The predicate that picks the one row ``get_one`` loads.

    The primary key is the default: ``get_one(5)`` means ``pk == 5``. A
    SQLAlchemy boolean expression replaces that default, so a natural-key
    route loads through the same scope, loader options and 404, and a
    composite key is addressable at all. The criterion narrows inside the
    scope; only ``scope=`` replaces what the view can see.
    """
    if isinstance(id, ColumnElement):
        return id
    if isinstance(id, WhereClause):
        raise TypeError(
            f"{model_cls.__name__}: a clause is a scope, not a row identity. "
            "Call it for its expression (ItemClauses.published()), or "
            "pass it as scope= to replace the view scope for this read."
        )
    if type(id) is bool:
        # ``obj.slug == slug`` on a loaded object is a Python bool, and
        # SQLAlchemy renders it as WHERE true, which matches the whole scope
        raise TypeError(
            f"{model_cls.__name__}: got a bool, not an id or a SQL expression. "
            "A comparison on an instance attribute is a bool; compare the "
            "column instead (Model.slug == slug)."
        )
    pk_cols = sa_inspect(model_cls).primary_key
    if len(pk_cols) != 1:
        raise NotImplementedError(
            f"{model_cls.__name__} has a composite primary key, so an id does "
            "not name one row; pass a predicate instead, e.g. "
            "get_one(sqlalchemy.and_(Model.a == a, Model.b == b))."
        )
    return pk_cols[0] == id


def _not_found_message(model_cls: type[Any], id: Any) -> str:
    # a predicate is not echoed: the 404 body is no place for SQL
    if isinstance(id, ColumnElement):
        return f"{model_cls.__name__} was not found"
    return f"{model_cls.__name__} with id {id!r} was not found"


def reject_unknown_query_keys(request: fastapi.Request, allowed: set[str]) -> None:
    """Raise a 422 naming every query key that is not in ``allowed``.

    The dialect decides what is allowed (the list params for the default
    one, ``sort``/``range``/``filter`` for react-admin); the envelope is the
    same either way, and mirrors FastAPI's own validation shape. A key that
    the route reads elsewhere is never unknown (see :func:`_route_query_keys`).
    """
    unknown = set(request.query_params.keys()) - allowed
    if unknown:
        unknown -= _route_query_keys(request)
    if not unknown:
        return
    detail = [
        {
            "type": "extra_forbidden",
            "loc": ["query", key],
            "msg": f"Unknown query parameter {key!r}",
            "input": request.query_params.get(key),
        }
        for key in sorted(unknown)
    ]
    raise fastapi.HTTPException(status_code=422, detail=detail)


def _route_query_keys(request: fastapi.Request) -> set[str]:
    """The query keys that the matched route reads.

    Every query parameter its dependency tree declares, at any level: the
    app or a router, the view's ``dependencies``, a class attribute, the
    endpoint. Also the key of a query API key scheme such as
    ``APIKeyQuery``, which reads the query string without declaring a
    parameter. The guard walks the tree only for a key that its dialect
    does not allow.
    """
    dependant = getattr(request.scope.get("route"), "dependant", None)
    pending = [dependant] if dependant is not None else []
    keys: set[str] = set()
    while pending:
        current = pending.pop()
        for field in current.query_params:
            # the name FastAPI reads the value from
            keys.add(getattr(field, "validation_alias", None) or field.alias)
        call = current.call
        if isinstance(call, APIKeyBase) and call.model.in_ == APIKeyIn.query:
            keys.add(call.model.name)
        pending.extend(current.dependencies)
    return keys


def _list_params_dependency(
    params_model: type[pydantic.BaseModel],
) -> Callable[..., pydantic.BaseModel]:
    """A dependency that validates ``params_model`` from the query string.

    FastAPI splits a query model into its fields only when it is the sole
    query parameter: at runtime per function, in OpenAPI across the route.
    Any other query parameter therefore collapses it into one required
    ``list_params`` value. This reads the fields itself, the way FastAPI
    hands a query model its input: every value of a repeated key for a
    sequence field, the last value otherwise, a default that is not None,
    and any other key as sent. :func:`_list_openapi_parameters`
    documents the fields.
    """
    fields = [
        (_query_key(name, field), field, _reads_every_value(field))
        for name, field in params_model.model_fields.items()
    ]
    field_keys = {key for key, _, _ in fields}

    def list_params(request: fastapi.Request, first: Any = None) -> pydantic.BaseModel:
        query = request.query_params
        data: dict[str, Any] = {}
        for key, field, every_value in fields:
            if key in query:
                data[key] = query.getlist(key) if every_value else query[key]
            elif not field.is_required():
                default = field.get_default()
                if default is not None:
                    data[key] = default
        for key in query.keys():
            if key not in field_keys:
                values = query.getlist(key)
                data[key] = values[0] if len(values) == 1 else values
        try:
            return params_model.model_validate(data)
        except pydantic.ValidationError as exc:
            errors = [
                {**error, "loc": ("query", *error["loc"])}
                for error in exc.errors(include_url=False)
            ]
            raise RequestValidationError(errors) from None

    parameters = [
        inspect.Parameter(
            "request", inspect.Parameter.KEYWORD_ONLY, annotation=fastapi.Request
        )
    ]
    if fields:
        # FastAPI documents a route's 422 only when it reads a parameter of
        # the route itself. It reads the first key here, undocumented and
        # unchecked; the model validates the value with the rest.
        marker = fastapi.Query(alias=fields[0][0], include_in_schema=False)
        parameters.append(
            inspect.Parameter(
                "first",
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=Annotated[Any, marker],
            )
        )
    list_params.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    return list_params


def _query_key(name: str, field: FieldInfo) -> str:
    """The query key of a list params field: its validation alias, its alias, or
    its name, as FastAPI reads a query model."""
    if isinstance(field.validation_alias, str):
        return field.validation_alias
    return field.alias or name


def _reads_every_value(field: FieldInfo) -> bool:
    """Whether a list params field reads every value of a repeated query key.

    FastAPI's rule for a query model: a sequence or set type, alone or in a
    union, unless the value is JSON.
    """
    if any(isinstance(item, pydantic.Json) for item in field.metadata):
        return False
    return _is_sequence_annotation(field.annotation)


def _is_sequence_annotation(annotation: Any) -> bool:
    if get_origin(annotation) in (Union, types.UnionType):
        return any(_is_sequence_annotation(arg) for arg in get_args(annotation))
    return any(
        isinstance(candidate, type)
        and get_origin(candidate) is None
        and issubclass(candidate, (Sequence, Set))
        and not issubclass(candidate, (str, bytes))
        for candidate in (annotation, get_origin(annotation))
    )


def _list_openapi_parameters(
    params_model: type[pydantic.BaseModel],
) -> list[dict[str, Any]]:
    """One OpenAPI query parameter per field of ``params_model``.

    The entries FastAPI writes for a query model it splits into fields, so
    the list route documents the same whatever else the route reads. Built from
    the model's JSON schema, with references inlined: the document's
    components do not hold the model's definitions.
    """
    schema = params_model.model_json_schema()
    definitions = schema.get("$defs", {})
    properties = schema.get("properties", {})
    required = set(schema.get("required", ()))
    parameters: list[dict[str, Any]] = []
    for name, field in params_model.model_fields.items():
        key = _query_key(name, field)
        field_schema = _inline_definitions(
            properties.get(key, properties.get(name, {})), definitions
        )
        field_schema["title"] = field.title or key.title().replace("_", " ")
        parameter: dict[str, Any] = {
            "name": key,
            "in": "query",
            "required": key in required,
            "schema": field_schema,
        }
        if field.description:
            parameter["description"] = field.description
        if field.deprecated:
            parameter["deprecated"] = True
        parameters.append(parameter)
    return parameters


def _inline_definitions(schema: Any, definitions: dict[str, Any]) -> Any:
    """``schema`` with each ``#/$defs/...`` reference replaced by its target."""
    if isinstance(schema, list):
        return [_inline_definitions(item, definitions) for item in schema]
    if not isinstance(schema, dict):
        return schema
    inlined = {
        key: _inline_definitions(value, definitions)
        for key, value in schema.items()
        if key != "$ref"
    }
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        target = definitions[reference.removeprefix("#/$defs/")]
        return {**_inline_definitions(target, definitions), **inlined}
    return inlined


def _guard_list_params(route: Callable) -> Callable:
    """Run the unknown-key guard before a route that takes ``list_params``.

    A copy, like the parent-endpoint copies: ``functools.wraps`` carries the
    route args and the signature, and the marker keeps a subclass's copy
    from being wrapped twice.
    """
    if inspect.iscoroutinefunction(route):

        @functools.wraps(route)
        async def _async_guarded(self, *args, **kwargs):
            self._reject_unknown_query_params()
            return await route(self, *args, **kwargs)

        guarded: Callable = _async_guarded
    else:

        @functools.wraps(route)
        def _sync_guarded(self, *args, **kwargs):
            self._reject_unknown_query_params()
            return route(self, *args, **kwargs)

        guarded = _sync_guarded

    guarded._fr_list_guard = True  # type: ignore[attr-defined]
    return guarded


def _exclude_routes(cls: type[BaseRestView[Any, Any, Any, Any, Any]]):
    for route_name in cls.exclude_routes:
        method_name = (
            route_name.value if isinstance(route_name, ViewRoute) else route_name
        )
        # @route decorator adds `_api_route_args` to a method to create the route later.
        # By removing it from the method, the method will no longer be added as a route.
        view_func = getattr(cls, method_name, None)
        if view_func is not None and hasattr(view_func, "_api_route_args"):
            del view_func._api_route_args
            continue
        # Not a live route on this class. Tolerate an exclusion that is *already*
        # satisfied: a subclass that inherits ``exclude_routes`` from a parent
        # which already excluded the route never receives a routable copy, so
        # there is nothing to strip. The name is still a genuine route elsewhere
        # in the lineage; only raise when it is no route at all (a typo, or the
        # business method name instead of the ``*_endpoint`` route name).
        if not _is_route_name_in_lineage(cls, method_name):
            raise AttributeError(f"{method_name!r} is not a route on {cls.__name__}")


def _is_route_name_in_lineage(
    cls: type[BaseRestView[Any, Any, Any, Any, Any]], method_name: str
) -> bool:
    """True if any class in ``cls``'s MRO defines a routable endpoint of this
    name, so the name is a real route that may merely be already-excluded here.
    """
    return any(
        hasattr(klass.__dict__.get(method_name), "_api_route_args")
        for klass in cls.mro()
    )


def _init_view_cls_and_add_to_router(
    view_cls: type[View], parent_router: fastapi.APIRouter | fastapi.FastAPI
):
    """
    FastAPI builds routes from callable signatures and annotations. View classes
    need a small adapter layer before registration; that preparation happens here.

    FastAPI does a lot with annotations. For example, accepted or returned JSON is
    often described with Pydantic classes like this:

        def my_endpoint(foo: FooRead) -> FooRead:

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
        if _fr_globals.warn_on_misuse:
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
    _register_for_resource_ref(parent_router, view_cls)
    parent_router.include_router(api_router)
    # Record only after the mount succeeded: a failed registration must not be
    # marked as mounted, or the next call would silently no-op a view whose
    # routes never made it onto the parent.
    mounted.add(view_cls)
    setattr(holder, "_fr_mounted_views", mounted)
    # Fallback registration for users who skip ``fr.configure(app=...)``.
    # ``register_default_exception_handlers`` is idempotent and only acts on
    # FastAPI apps (it ignores nested APIRouter parents).
    if isinstance(parent_router, fastapi.FastAPI):
        register_default_exception_handlers(parent_router)


#: Bare business-method names. A ``@route``-decorated method must not be
#: named like one of these: it would shadow the verb (which the ``handle_<verb>``
#: handlers call) and collide with ``<verb>_endpoint`` at the same path.
#: Override the business method *without* a decorator for domain logic; use
#: ``<verb>_endpoint`` or a distinct name for a custom route.
_BARE_VERB_NAMES = frozenset({"get_many", "get_one", "create", "update", "delete"})


def _reject_bare_verb_route_names(view_cls: type[View]) -> None:
    for name, value in view_cls.__dict__.items():
        if name in _BARE_VERB_NAMES and hasattr(value, "_api_route_args"):
            raise TypeError(
                f"{view_cls.__name__}.{name}() is a route method named like the "
                f"business method '{name}'. A route by that name shadows the "
                f"method and collides with the '{name}_endpoint' endpoint "
                f"method. Rename it to '{name}_endpoint' (to replace it) or "
                f"give the custom action its own name."
            )


#: The five endpoint methods defined by RestView / AsyncRestView.
_ENDPOINT_METHOD_NAMES = frozenset(
    {
        "get_many_endpoint",
        "get_one_endpoint",
        "create_endpoint",
        "update_endpoint",
        "delete_endpoint",
    }
)


def _warn_on_misuse(view_cls: type[View]) -> None:
    """Opt-in registration-time lint (``fr.configure(warn_on_misuse=True)``).

    Flags the dominant misuse patterns with the idiomatic fix named in
    each message. Heuristic, best-effort, and advisory: every pattern it flags
    has a legitimate use, so it warns (:class:`RestlyMisuseWarning`) rather
    than rejects. Must run *before* parent endpoints are copied into the
    subclass, while ``__dict__`` still holds only what the user wrote; only
    the registered class is linted, not user-defined intermediate bases.
    """
    own = view_cls.__dict__
    name = view_cls.__name__
    is_crud_view = issubclass(view_cls, BaseRestView)

    # 1. Endpoint-method override where a business-method override was likely meant.
    if is_crud_view:
        for endpoint in sorted(_ENDPOINT_METHOD_NAMES & own.keys()):
            verb = endpoint.removesuffix("_endpoint")
            warnings.warn(
                f"{name} overrides the endpoint method '{endpoint}'. Override "
                f"an endpoint method only to change the HTTP contract. For "
                f"domain logic override the business method '{verb}'; for a gate "
                f"'authorize'; for the response shape 'to_response'. Call "
                f"'handle_{verb}' from the replacement to keep the bracket.",
                RestlyMisuseWarning,
                stacklevel=5,
            )

    # 2. Manual session.commit() in a view method. The framework owns the
    # commit; methods that go through write_action / handle_<verb> are exempt.
    for attr, value in own.items():
        func = getattr(value, "__func__", value)
        if not isinstance(func, types.FunctionType):
            continue
        try:
            source = inspect.getsource(func)
        except (OSError, TypeError):
            continue
        if (
            ".commit(" in source
            and "write_action" not in source
            and "handle_" not in source
        ):
            warnings.warn(
                f"{name}.{attr} calls session.commit() directly. The framework "
                f"owns the commit: reuse handle_<verb>(), or bracket the "
                f"mutation with write_action('<action>', ...) so authorize / "
                f"before_action_commit / after_action_commit run.",
                RestlyMisuseWarning,
                stacklevel=5,
            )

    # 3. A CRUD route set hand-rolled on a bare View.
    if not is_crud_view:
        http_methods: set[str] = set()
        n_routes = 0
        for value in own.values():
            route_args = getattr(value, "_api_route_args", None)
            if route_args is None:
                continue
            _path, route_kwargs = route_args
            n_routes += 1
            http_methods.update(
                method.upper() for method in route_kwargs.get("methods", ["GET"])
            )
        if (
            n_routes >= 3
            and {"GET", "POST"} <= http_methods
            and http_methods & {"PATCH", "PUT", "DELETE"}
        ):
            warnings.warn(
                f"{name} hand-rolls a CRUD route set on a bare View. RestView / "
                f"AsyncRestView already define list/create/get/update/delete "
                f"endpoint methods. Subclass one and override the business methods "
                f"(create/update/delete), declare a scope, or override "
                f"authorize for custom behavior.",
                RestlyMisuseWarning,
                stacklevel=5,
            )

    # 4. A reference type (IDRef/IDSchema) whose name is a scalar FK column:
    # the ``post_id: IDRef[Post]`` mistake, where ``data.post_id`` becomes a
    # wrapper instead of the plain id.
    _warn_scalar_named_reference_fields(view_cls)


def _warn_scalar_named_reference_fields(view_cls: type[View]) -> None:
    """Flag an ``IDRef``/``IDSchema`` field whose name is a scalar foreign-key
    column: ``post_id: IDRef[Post]`` makes ``data.post_id`` a reference wrapper
    instead of the plain id. Steer to ``fr.MustExist[pk, Model]``. Best-effort:
    lints only an explicitly-declared ``schema`` against the view's ``model``,
    and never a relationship-named or list reference (those names aren't columns).
    """
    schema = view_cls.__dict__.get("schema")
    model = getattr(view_cls, "model", None)
    if schema is None or model is None:
        return
    try:
        mapper = sa_inspect(model)
    except Exception:
        return
    for field_name, field_info in schema.model_fields.items():
        origin_target = reference_origin_and_target(field_info.annotation)
        if origin_target is None:
            continue
        column = mapper.columns.get(field_name)
        if column is None or not column.foreign_keys:
            continue  # a relationship-named or non-FK column is legitimate
        origin, target = origin_target
        ref_name = origin.__name__
        target_name = target.__name__ if target is not None else "Model"
        try:
            pk_name = column.type.python_type.__name__
        except Exception:
            pk_name = "int"
        rel = _relationship_name_for_fk(model, field_name)
        rename = (
            f", or name the field `{rel}` for the {ref_name} relationship"
            if rel
            else ""
        )
        warnings.warn(
            f"{schema.__name__}.{field_name} is typed `{ref_name}[{target_name}]`, "
            f"but `{field_name}` maps to the scalar foreign-key column "
            f"`{model.__name__}.{field_name}`. An {ref_name} field resolves to an "
            f"ORM object, so `data.{field_name}` is a reference wrapper, not the "
            f"plain id. Use `{field_name}: fr.MustExist[{pk_name}, {target_name}]` "
            f"for a checked scalar FK{rename}.",
            RestlyMisuseWarning,
            stacklevel=6,
        )


def _reject_buried_markers_in_view_schemas(view_cls: type[View]) -> None:
    """Backstop the ``BaseSchema`` import-time check at view registration.

    ``BaseSchema.__pydantic_init_subclass__`` already rejects a buried
    ReadOnly/WriteOnly marker as the schema class is defined, but a view may use
    a schema (and derived create/update schemas) that does not subclass
    ``BaseSchema``. Re-check the schemas this view actually uses so those are
    covered too.
    """
    checked: set[type] = set()
    for attr in ("schema", "schema_create", "schema_update"):
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
    if _fr_globals.warn_on_misuse:
        _warn_on_misuse(view_cls)
    _copy_all_parent_class_endpoints_into_this_subclass(view_cls)
    _reject_bare_verb_route_names(view_cls)
    _init_all_endpoints(view_cls)
    view_cls.before_include_view()
    _reject_buried_markers_in_view_schemas(view_cls)
    _init_class_based_view(view_cls)
    view_cls._fr_initialised = True  # type: ignore[attr-defined]


def _copy_all_parent_class_endpoints_into_this_subclass(view_cls: type[View]):
    """
    Override all methods with a @route decorator of the parent classes of view_cls
    with a new copy directly on view_cls . This allows us to change the
    annotations on these endpoints without affecting the parent endpoints.

    For example, FooView.get() delegates to AsyncRestView.get() if it is not
    overridden (this is called implicit delegation through method resolution). And if
    we add the annotation that FooView.get() returns FooRead but do not make a copy
    then AsyncRestView.get() and all other subclasses will get the FooRead
    annotation as well.
    """
    for name, endpoint in _get_all_parent_endpoints(view_cls).items():
        # `name` is the attribute key (e.g. "get_many_endpoint"), which is stable
        # across copies; `endpoint.__name__` may have been mangled by a parent's
        # own registration (e.g. "parentview_get_many_endpoint"), so key off the
        # attribute name throughout.
        if name in view_cls.__dict__:
            # This endpoint is already overridden!
            continue

        # The original endpoint might be shared between subclasses.
        # So make a copy and put that on the view_cls.
        endpoint_wrapper = _make_copy(endpoint, view_cls)
        if getattr(endpoint, "__module__", "").startswith("fastapi_restly."):
            # The shells carry override-redirect docstrings for help()/source
            # readers. FastAPI reads endpoint.__doc__ as the OpenAPI operation
            # description, so strip the copy: framework guidance must not leak
            # into the user's API docs. User-defined endpoints keep theirs.
            endpoint_wrapper.__doc__ = None
        # Reset the copy's name to the endpoint attribute so downstream renaming
        # produces "<view>_<name>" even when the source was a parent's renamed
        # copy.
        endpoint_wrapper.__name__ = name
        # Set explicit __qualname__ for debugging purposes.
        endpoint_wrapper.__qualname__ = f"{view_cls.__name__}_{name}_wrapper"
        setattr(view_cls, name, endpoint_wrapper)


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
        async def _async_wrapper(self, *args, **kwargs):
            return await endpoint(self, *args, **kwargs)

        endpoint_wrapper: Callable = _async_wrapper
    else:

        @functools.wraps(endpoint)
        def _sync_wrapper(self, *args, **kwargs):
            return endpoint(self, *args, **kwargs)

        endpoint_wrapper = _sync_wrapper

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
        # This will give the FooView.create() endpoint the name "fooview_create"
        endpoint.__name__ = view_cls.__name__.lower() + "_" + endpoint.__name__
        _annotate_self(view_cls, endpoint)


def _view_id_type(view_cls: type) -> Any:
    """The ``{id}`` type: ``id_type`` when set, else the model's primary key's.

    Resolved per registration and never stored on the class, so a subclass
    with another model derives its own.
    """
    explicit = getattr(view_cls, "id_type", None)
    if explicit is not None:
        return explicit
    model = getattr(view_cls, "model", None)
    # a composite key has no scalar type; it keeps the int default
    if model is None or len(sa_inspect(model).primary_key) != 1:
        return int
    return _model_id_type(model) or int


_UNCHANGED: Any = object()
_ANY_STRINGS = frozenset({"Any", "typing.Any"})


def _annotate(
    func: Callable,
    return_annotation: Any = _UNCHANGED,
    *,
    overwrite: bool = False,
    **param_annotations: Any,
) -> None:
    """Fill the view's concrete types into ``func.__signature__``.

    The endpoint shells declare ``Any`` because the concrete types only exist
    per view class. A parameter or return is filled only where the author
    wrote no annotation or ``Any``, so a replaced endpoint method keeps its
    own types. ``overwrite`` fills regardless.
    """
    # Judge what the author wrote, not the signature: functools.wraps copies
    # a registered parent's filled-in signature onto a subclass's copy.
    written = inspect.get_annotations(func)

    def fillable(name: str) -> bool:
        annotation = written.get(name, Any)
        # under PEP 563 the default signature's ``Any`` is the string "Any"
        return (
            overwrite
            or annotation is Any
            or (isinstance(annotation, str) and annotation in _ANY_STRINGS)
        )

    sig = inspect.signature(func)
    params = [
        param.replace(annotation=param_annotations[param.name])
        if param.name in param_annotations and fillable(param.name)
        else param
        for param in sig.parameters.values()
    ]
    if return_annotation is not _UNCHANGED and fillable("return"):
        sig = sig.replace(return_annotation=return_annotation)
    func.__signature__ = sig.replace(parameters=params)  # type: ignore[attr-defined]


def _get_all_parent_endpoints(view_cls: type[View]) -> dict[str, Callable]:
    """Map parent route endpoints by attribute name.

    Registered intermediate parents copy base endpoints into their ``__dict__``.
    Keying by most-derived attribute name prevents duplicate logical endpoints
    while still respecting overrides.
    """
    endpoints: dict[str, Callable] = {}
    seen: set[str] = set()
    for cls in view_cls.mro():
        if cls is view_cls:
            continue
        for name, value in cls.__dict__.items():
            if name in seen:
                # A more-derived class already defined this name; it shadows the
                # base regardless of whether that override is itself a route.
                continue
            seen.add(name)
            if hasattr(value, "_api_route_args"):
                endpoints[name] = value
    return endpoints


def _init_api_router(view_cls: type[View]) -> fastapi.APIRouter:
    for name in view_cls.route_options:
        if not _is_route_name_in_lineage(view_cls, name):
            raise RestlyConfigurationError(
                f"{name!r} is not a route in {view_cls.__name__}.route_options"
            )

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
    for name, attr in view_cls.__dict__.items():
        if not hasattr(attr, "_api_route_args"):
            continue
        endpoint = attr
        path, route_kwargs = endpoint._api_route_args
        defaults = {}
        if issubclass(view_cls, BaseRestView) and name in _CRUD_ROUTE_METADATA:
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
        route_kwargs = {
            **defaults,
            **route_kwargs,
            **view_cls.route_options.get(name, {}),
        }
        if getattr(endpoint, "_fr_list_guard", False):
            if list_parameters is None:
                list_parameters = _list_openapi_parameters(
                    cast(Any, view_cls).schema_list_params
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

# Markers for the request inputs FastAPI reads into an endpoint parameter:
# ``Path``/``Query``/``Header``/``Cookie`` (``Param``) and ``Body``/``Form``/
# ``File`` (``Body``). FastAPI fills a class attribute only through ``Depends``
# or a special type, never through one of these, so the view is rejected.
_REQUEST_PARAMETER_MARKERS: tuple[type, ...] = (_ParamMarker, _BodyMarker)


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
    request_markers: dict[str, Any] = {}
    for cls in reversed(view_cls.__mro__):
        try:
            cls_hints = get_type_hints(cls, include_extras=True)
        except Exception:
            continue
        for name, annotation in cls_hints.items():
            if get_origin(annotation) is ClassVar:
                continue
            metadata = getattr(annotation, "__metadata__", ())
            has_depends_marker = any(isinstance(m, _DependsMarker) for m in metadata)
            for marker in metadata:
                if isinstance(marker, _REQUEST_PARAMETER_MARKERS):
                    request_markers.setdefault(name, marker)
            underlying = (
                annotation
                if get_origin(annotation) is not Annotated
                else (get_args(annotation)[0] if get_args(annotation) else annotation)
            )
            # On Python 3.10, a parameterized generic such as list[str] passes
            # isclass() but makes issubclass() raise; it is never special.
            is_special_type = (
                inspect.isclass(underlying)
                and get_origin(underlying) is None
                and issubclass(underlying, _FASTAPI_SPECIAL_INJECTABLE)
            )
            if has_depends_marker or is_special_type:
                # Marker-bearing annotation wins, regardless of MRO position.
                di_annotations[name] = annotation
            # Plain annotations are silently ignored — they neither set
            # nor clear an entry in di_annotations.

    # FastAPI never fills an attribute with a request-parameter marker: it
    # would keep its class default, with no error and no OpenAPI entry. A
    # Depends marker on the same name wires it, as above.
    for name, marker in request_markers.items():
        if name not in di_annotations:
            raise RestlyConfigurationError(
                f"{view_cls.__name__}.{name} has a {type(marker).__name__}() "
                "marker, but a view class attribute is filled only through "
                "Depends(...) or Security(...), so FastAPI would never set it. "
                "Declare the parameter on the endpoint method that reads it, "
                f"or annotate {name} with Annotated[..., Depends(...)] and read "
                "the parameter in that dependency."
            )

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
