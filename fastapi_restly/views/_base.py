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
import sys
import types
import warnings
import weakref
from collections.abc import Collection, Iterable, Mapping, Set
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
    Literal,
    Protocol,
    Sequence,
    Union,
    cast,
    final,
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
from sqlalchemy import Boolean, ColumnElement, Select, func, tuple_
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select as sa_select
from sqlalchemy.orm import QueryableAttribute, selectinload
from sqlalchemy.orm.attributes import del_attribute
from starlette.datastructures import QueryParams
from typing_extensions import TypeVar

if sys.version_info >= (3, 14):
    import annotationlib
from ..clauses import UNSCOPED, WhereClause, all_of, apply_clauses, where_clause
from ..clauses._runtime import (
    _display_table_name,
    _expression_tables,
    _guard_statement_tables,
    _statement_tables,
)
from ..clauses._scopes import _default_scope

#: A per-read scope: ``None`` for the view's own, a clause that replaces it
#: for that read, or ``fr.clauses.UNSCOPED``.
ReadScope = WhereClause | None

# A per-read narrowing filter: a SQLAlchemy boolean expression, or anything
# all_of takes. Private: only the final list handler accepts one, so no
# override spells it.
_ReadWhere = ColumnElement[bool] | WhereClause | None
from .._mapping import is_mapped_class, is_mapped_instance, is_to_many_relationship
from .._pagination import (
    _DEFAULT_PAGINATION,
    _SORT_QUERY_PARAM,
    DEFAULT_PAGE_SIZE,
    NoPagination,
    NumberedPagination,
    _build_envelope,
    _check_query_param,
    _numbered_page_info,
    _unpaginated_page_info,
)
from .._routes import matched_route
from ..db._config import _fr_config
from ..exc import (
    NotFound,
    RestlyConfigurationError,
    RestlyMisuseWarning,
    RestlySkippedFilterMethodsWarning,
)
from ..objects import snapshot as _object_snapshot
from ..query._impl import (
    _KEY_SETTING_DEFAULTS,
    _KEY_SETTINGS,
    _NO_DEFAULT,
    _check_default_sort,
    _check_key_setting,
    _derive_list_params,
    _OwnFilter,
    _query_settings,
    _record_of,
)
from ..query._shared import SortNulls
from ..schemas import (
    BaseSchema,
    IDSchema,
    derive_schema_list_response,
    derive_schema_response,
)
from ..schemas._base import (
    _SECRET_TYPES,
    _as_response,
    _client_name,
    _collection_item_type,
    _is_readonly,
    _model_id_type,
    _reject_buried_markers,
    _response_adapter,
    _schema_resource_name,
    _unwrap_type,
    create_model_with_optional_fields,
    create_model_without_read_only_fields,
    get_writable_inputs,
    is_readonly_field,
    is_reference_field,
    reference_origin_and_target,
)
from ..schemas._generator import _required_init_params, derive_schema
from ._openapi import _register_for_resource_ref
from ._view_types import _ViewType

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
    #: page and the page size from them, and without them it reports page 1
    #: on every page.
    list_params: Any = None


class ViewRoute(str, Enum):
    """Default CRUD route names that can be referenced by view options.

    Values are the endpoint method names so ``exclude_routes`` can drop them.
    """

    #: ``GET /``: the list.
    GET_MANY = "get_many_endpoint"
    #: ``GET /{id}``: one row.
    GET_ONE = "get_one_endpoint"
    #: ``POST /``: create a row.
    CREATE = "create_endpoint"
    #: ``PATCH /{id}``: update a row.
    UPDATE = "update_endpoint"
    #: ``DELETE /{id}``: delete a row.
    DELETE = "delete_endpoint"


class ResponseShape(str, Enum):
    """The response shape an endpoint method asks :meth:`BaseRestView.to_response` to
    produce.

    This is separate from write-action names such as ``"publish"``. Endpoint
    methods choose one of these three response shapes; custom actions remain an
    open string namespace.
    """

    #: One object: ``result`` is a row, and the response is an instance of
    #: the response class, from :meth:`BaseRestView.to_single_response`.
    SINGLE = "single"
    #: A list: ``result`` is a :class:`ListResult`, and the response is the
    #: list envelope, from :meth:`BaseRestView.to_list_response`.
    LIST = "list"
    #: No body: ``result`` is not used, and the response is ``204 No Content``.
    EMPTY = "empty"


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
    keys are named ``id``; ``IDMixin`` declares one but isn't required."""

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


def _collection_ready(model_cls: type[Any], field_name: str, value: Any) -> Any:
    """A tuple for a to-many relationship, as a list.

    A field such as ``tuple[fr.IDRef[Tag], ...]`` validates to a tuple, also
    an empty one, and SQLAlchemy takes a list for a list collection, not a
    tuple.
    """
    if isinstance(value, tuple) and is_to_many_relationship(model_cls, field_name):
        return list(value)
    return value


def _revealed(value: Any) -> Any:
    """The secret of a Pydantic secret type, such as ``SecretStr``, for the
    column. A response masks it again."""
    return value.get_secret_value() if isinstance(value, _SECRET_TYPES) else value


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

        fields = schema_cls.model_fields
        raise fastapi.HTTPException(
            status_code=422,
            detail=(
                f"Conflicting references for "
                f"{_client_name(fk_field, fields[fk_field])} and "
                f"{_client_name(relation_field, fields[relation_field])}: "
                f"{_reference_identity_detail(fk_identity)} != "
                f"{_reference_identity_detail(relation_identity)}"
            ),
        )


def iter_creatable_fields(schema_obj: pydantic.BaseModel) -> Iterator[tuple[str, Any]]:
    """Iterate over (field_name, value) pairs that should be used to construct a new
    ORM object from ``schema_obj``.

    Fields marked as ``ReadOnly`` are skipped. Unlike :func:`get_writable_inputs`,
    this also includes fields that were not explicitly provided, so that
    schema-level defaults end up on the new object. :func:`build_create_plan`
    does not pass a field that the client left out with the value ``None``,
    unless the constructor requires it, so the model's default applies.
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
    request's ``IDRef`` that is still on ``schema_obj``.
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
        left_out = value is None and field_name not in schema_obj.model_fields_set
        if (
            value is None
            and is_reference_field(schema_cls, field_name)
            # A left-out reference on a column is left to the model's default.
            and not (left_out and _is_mapped_column(model_cls, field_name))
        ):
            _add_null_reference_to_create_plan(plan, model_cls, field_name)
            continue
        if is_mapped_instance(value) and is_reference_field(schema_cls, field_name):
            _add_resolved_reference_to_create_plan(plan, model_cls, field_name, value)
            continue
        if left_out and field_name not in _required_init_params(model_cls):
            # Not passed, as user code would not pass it: the model's own
            # default applies. A JSON column would store a None as JSON null.
            continue

        value = _collection_ready(
            model_cls, field_name, _json_ready(model_cls, field_name, _revealed(value))
        )
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
    request's ``IDRef`` that is still on ``schema_obj``.
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
        value = _json_ready(type(obj), field_name, _revealed(value))
        setattr(obj, field_name, _collection_ready(type(obj), field_name, value))


def _get_nested_schema_annotation(
    annotation: Any, seen: set[Any] | None = None
) -> type[pydantic.BaseModel] | None:
    seen = set() if seen is None else set(seen)
    annotation, _ = _unwrap_type(annotation, seen)

    try:
        if inspect.isclass(annotation) and issubclass(annotation, pydantic.BaseModel):
            return annotation
    except TypeError:
        pass

    item = _collection_item_type(annotation, seen)
    if item is not None:
        return _get_nested_schema_annotation(item, seen)

    return None


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


#: The most rows that one reload SELECT names, below the parameter limits of
#: the databases (SQLite allows 32766 in all, older versions 999).
_RELOAD_CHUNK = 500


def _rows_reload_statements(
    model: Any, rows: list[Any], options: list[Any]
) -> list[Any]:
    """The SELECTs that reload saved ``rows`` of ``model``, in chunks.

    The save expires the relationships of the rows first, so a changed
    foreign key is never served from a stale relationship. The SELECT then
    loads the relationships and the expressions of the response through
    ``options``, and fills only what is unloaded: not ``populate_existing``,
    which would expire a deferred column that the route loaded, the columns
    of a subclass, and a change made after the flush.
    """
    mapper = sa_inspect(model)
    statements = []
    for start in range(0, len(rows), _RELOAD_CHUNK):
        identities = [
            sa_inspect(row).identity for row in rows[start : start + _RELOAD_CHUNK]
        ]
        if len(mapper.primary_key) == 1:
            where = mapper.primary_key[0].in_([identity[0] for identity in identities])
        else:
            where = tuple_(*mapper.primary_key).in_(identities)
        statements.append(sa_select(model).where(where).options(*options))
    return statements


def _count_statement(stmt: Select[Any]) -> Select[Any]:
    """The SELECT that counts the rows of the list statement ``stmt``.

    A list has one object for each row of the entity that ``stmt`` selects
    first. So the count counts the distinct values of the columns that name
    such a row (``_identity_columns``), and a join that repeats a row does
    not count it twice. The other columns are left out. PostgreSQL cannot
    compare ``json`` values, so ``DISTINCT`` over a ``json`` column fails.
    And the subquery of a ``column_property`` would run for every row of the
    list. A statement that does not select an entity first counts its
    distinct rows.
    """
    source = stmt.order_by(None).limit(None).offset(None)
    columns = _identity_columns(stmt)
    if columns:
        source = source.with_only_columns(*columns, maintain_column_froms=True)
    return sa_select(func.count()).select_from(source.distinct().subquery())


def _identity_columns(stmt: Select[Any]) -> list[Any]:
    """The columns that name a row of the entity that ``stmt`` selects first.

    These are the primary key columns, as attributes of the entity, so the
    ORM puts them on the selectable the entity loads from: an alias, or the
    union of a concrete inheritance. Each table of such a union numbers its
    own rows, so the discriminator is added too. Empty when the first column
    of ``stmt`` is not an entity.
    """
    descriptions = stmt.column_descriptions
    entity = descriptions[0]["entity"] if descriptions else None
    if entity is None or descriptions[0]["expr"] is not entity:
        return []
    info = sa_inspect(entity)
    mapper = info.mapper
    columns = [
        getattr(entity, mapper.get_property_by_column(column).key)
        for column in mapper.primary_key
    ]
    polymorphic = mapper.with_polymorphic
    if polymorphic and polymorphic[1] is not None and mapper.polymorphic_on is not None:
        # the discriminator as a column of what the entity selects from
        discriminator = info.selectable.corresponding_column(mapper.polymorphic_on)
        if discriminator is not None:
            columns.append(discriminator)
    return columns


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
    #: adds its own, and a subclass's entry for a status code wins.
    responses: ClassVar[dict[int | str, dict[str, Any]]] = {}

    if TYPE_CHECKING:
        # include_view replaces __init__: each injected attribute becomes a
        # keyword, so code that builds a view by hand passes them by name.
        def __init__(self, **dependencies: Any) -> None: ...

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _reject_route_names_of_settings(cls)

    @classmethod
    def before_include_view(cls) -> None:
        """Run by :func:`include_view` once per class, before its routes are
        registered. A no-op here; override to adjust route methods first."""


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


_F = TypeVar("_F", bound=Callable[..., Any])


def list_filter(key: str) -> Callable[[_F], _F]:
    """Make a view method the filter for the query key ``key``.

    For a filter that the generated keys do not give::

        @fr.list_filter("popular")
        def filter_popular(self, value: bool) -> ColumnElement[bool]:
            popular = Item.view_count > 1000
            return popular if value else ~popular

    The method takes one value after ``self``. Its type becomes the type of
    the key in the list params, so OpenAPI shows it and a bad value gets
    ``422``. The docstring becomes the description of the key. Without a
    default, the view calls the method when the key has a value. With a
    default, it calls the method on every list request, with the default
    when the key is missing.

    The method returns a SQLAlchemy condition, a clause or ``None`` for no
    condition. The view adds it to the statement with AND, inside the scope.
    The condition may use only the tables that are in the statement; use a
    relationship (``.has()``/``.any()``) for another table. On an async view
    the method can be ``async def``.

    The key is a name of your own, such as ``popular``, or a field and an
    operator, such as ``name__length``. The operator may be one that Restly
    generates for other fields, or a new one. Before the last ``__`` comes a
    field with filters, as in the generated keys, such as ``vendor.name``.

    The key is always on: ``include_filters`` and ``exclude_filters`` choose
    among the generated keys only. A subclass drops the key by setting the
    method name to ``None``.

    :raises RestlyConfigurationError: ``key`` is not a non-empty string,
        starts with ``_``, is the name of a Pydantic model attribute, or has
        ``__`` without one field before it and one operator without ``.``
        after it. A key that is a page parameter, the
        sort parameter or a generated key that is on fails when the view
        registers, and so does a key with ``__`` or ``.`` that does not start
        with a field with filters.
    """
    _check_own_key_name(key)

    def mark(method: _F) -> _F:
        method._fr_list_filter = key  # type: ignore[attr-defined]
        return method

    return mark


def _check_own_key_name(key: Any) -> None:
    """Reject a key that cannot be a query parameter of the list params.

    The last ``__`` splits a field from an operator, as in the generated
    keys. The view checks the field when it registers.
    """
    if not isinstance(key, str) or not key:
        raise RestlyConfigurationError(
            f"@fr.list_filter needs a key as a non-empty string, got {key!r}."
        )
    field, separator, operator = key.rpartition("__")
    if separator and "." in operator:
        raise RestlyConfigurationError(
            f"@fr.list_filter({key!r}): an operator has no '.'. A '.' goes in "
            "the field, as in 'vendor.name__startswith'."
        )
    if separator and (not field or not operator or "__" in field):
        raise RestlyConfigurationError(
            f"@fr.list_filter({key!r}): '__' splits a field from an operator, "
            "as in 'name__startswith'. Write it once, with a field before it "
            "and an operator after it."
        )
    # the list params are a Pydantic model with one field per key
    if key.startswith("_") or hasattr(pydantic.BaseModel, key):
        raise RestlyConfigurationError(
            f"@fr.list_filter({key!r}): a key cannot start with '_' or be "
            "the name of a Pydantic model attribute."
        )


def _list_filter_methods(cls: type) -> dict[str, Any]:
    """The methods of ``cls`` with ``@fr.list_filter``, by name, in the
    order that the classes define them.

    A name set to ``None`` drops the filter of a base class.

    :raises RestlyConfigurationError: a method overrides a filter method
        without the decorator.
    """
    names: dict[str, None] = {}
    for klass in reversed(cls.__mro__):
        names.update(dict.fromkeys(vars(klass)))
    methods: dict[str, Any] = {}
    for name in names:
        marked = [
            klass
            for klass in cls.__mro__
            if hasattr(vars(klass).get(name), "_fr_list_filter")
        ]
        if not marked:
            continue
        value = inspect.getattr_static(cls, name)
        if value is None:
            continue
        if not hasattr(value, "_fr_list_filter"):
            key = getattr(vars(marked[0])[name], "_fr_list_filter")
            raise RestlyConfigurationError(
                f"{cls.__name__}.{name}{_set_by(cls, name)} overrides the "
                f"filter method of {marked[0].__name__} without "
                f"@fr.list_filter. Add @fr.list_filter({key!r}) to keep the "
                f"key, or set {name} = None to drop it."
            )
        methods[name] = value
    return methods


def _own_filters(cls: type) -> list[_OwnFilter]:
    """The own filter keys of a view, with the types of their values.

    :raises RestlyConfigurationError: two methods have one key, or a method
        does not take one value with a type.
    """
    filters: list[_OwnFilter] = []
    method_of: dict[str, str] = {}
    for name, method in _list_filter_methods(cls).items():
        label = f"{cls.__name__}.{name}"
        key = method._fr_list_filter
        if key in method_of:
            raise RestlyConfigurationError(
                f"{cls.__name__}: {method_of[key]} and {name} are both the "
                f"filter for the key {key!r}."
            )
        method_of[key] = name
        parameters = list(_route_parameters(method).values())[1:]
        if len(parameters) != 1 or parameters[0].kind not in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            raise RestlyConfigurationError(
                f"{label} must take one value after self, as in "
                f"'def {name}(self, value: bool)'."
            )
        parameter = parameters[0]
        if parameter.annotation is inspect.Parameter.empty:
            raise RestlyConfigurationError(
                f"{label} needs a type on {parameter.name!r}, such as "
                f"'{parameter.name}: bool'. The type checks the value from the "
                "URL, and OpenAPI shows it."
            )
        # Only the value's type is evaluated: the return type may name a
        # class that the module imports under TYPE_CHECKING.
        holder = types.SimpleNamespace(
            __annotations__={parameter.name: parameter.annotation}
        )
        try:
            annotation = get_type_hints(
                holder,
                globalns=getattr(inspect.unwrap(method), "__globals__", {}),
                include_extras=True,
            )[parameter.name]
        except NameError as exc:
            raise RestlyConfigurationError(
                f"{label}: the type of {parameter.name!r} cannot be resolved: {exc}."
            ) from exc
        filters.append(
            _OwnFilter(
                key=key,
                method=name,
                annotation=annotation,
                default=(
                    _NO_DEFAULT
                    if parameter.default is inspect.Parameter.empty
                    else parameter.default
                ),
                description=inspect.getdoc(method),
            )
        )
    return filters


def _apply_own_filters(
    stmt: Select[Any], results: Iterable[tuple[str, Any]]
) -> Select[Any]:
    """Add what the filter methods returned to ``stmt``, with AND.

    :param results: The label of each method, such as
        ``ItemView.filter_popular``, and what the method returned.

    :raises TypeError: a method returned anything other than a condition,
        a clause or ``None``, or a condition on a table that is not in the
        statement.
    """
    conditions: list[ColumnElement[bool]] = []
    for label, result in results:
        if result is None or result is UNSCOPED:
            continue
        result = _clause_element(result)
        if isinstance(result, ColumnElement):
            # applying it as a clause fills members such as Current.user_id
            result = where_clause(result)
        if type(result) is bool:
            raise TypeError(
                f"{label} returned a bool, not a SQL expression. A comparison "
                "on an instance attribute is a bool; compare the column "
                "instead (Item.name == value)."
            )
        if not isinstance(result, WhereClause):
            raise TypeError(
                f"{label} must return a SQLAlchemy boolean expression, a "
                f"clause or None, got {type(result).__name__}."
            )
        condition = result()
        # a table outside the statement would be a cross join that the scope
        # does not reach
        try:
            _guard_statement_tables(stmt, [condition])
        except TypeError as exc:
            raise TypeError(f"{label}: {exc}") from None
        conditions.append(condition)
    return stmt.where(*conditions) if conditions else stmt


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


#: The routes that load one row by the id in the path.
_ITEM_ROUTES = ("get_one_endpoint", "update_endpoint", "delete_endpoint", "put")


def _check_composite_key(cls: Any) -> None:
    """Refuse item routes on a model with a composite primary key and the
    default ``where_id``: an id from the path cannot name one row."""
    if len(sa_inspect(cls.model).primary_key) < 2:
        return
    if getattr(cls.where_id, "__module__", "").startswith("fastapi_restly."):
        excluded = _excluded_route_names(cls)
        routes = [
            name
            for name in _ITEM_ROUTES
            if name not in excluded
            and hasattr(getattr(cls, name, None), "_api_route_args")
        ]
        if routes:
            raise RestlyConfigurationError(
                f"{cls.__name__}: {cls.model.__name__} has a composite primary "
                f"key, so an id from the path cannot name one row, and the "
                f"item routes ({', '.join(routes)}) need it. Set "
                "id_type = str, and override where_id(id): split the id, and "
                "return sqlalchemy.and_(Model.a == a, Model.b == b). Or leave "
                "the item routes out with exclude_routes."
            )


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


#: The view settings that Restly generates the list params with, besides
#: the pagination, and their defaults.
_LIST_SETTING_DEFAULTS: dict[str, Any] = {
    **_KEY_SETTING_DEFAULTS,
    "sort_query_param": _SORT_QUERY_PARAM,
    "default_sort": None,
}


def _list_setting(cls: type, name: str) -> Any:
    """The value of a list setting of a view, as it is recorded.

    :raises RestlyConfigurationError: a key setting is not keys, or a
        ``default_sort`` is not sort keys, such as one that a mixin sets,
        which is not checked when it is defined.
    """
    value = getattr(cls, name)
    label = f"{cls.__name__}.{name}{_set_by(cls, name)}"
    if name == "sort_query_param":
        return value
    if name == "default_sort":
        _check_default_sort(label, value)
        return value
    return _check_key_setting(label, value)


#: The list methods that an async view awaits, and a sync view calls.
_LIST_METHODS = ("apply_list_params", "apply_filters")


def _check_list_methods(cls: type, *, is_async: bool) -> None:
    """Reject a list method of the wrong kind when the class is defined.

    An async view awaits :meth:`apply_list_params` and :meth:`apply_filters`,
    so a plain ``def`` fails there on the first request, and a sync view
    would get a coroutine instead of a statement. A sync view also calls its
    filter methods without await. A filter method that is overridden
    without ``@fr.list_filter`` fails here too.
    """
    filter_methods = _list_filter_methods(cls)
    if not is_async:
        for name, method in filter_methods.items():
            if inspect.iscoroutinefunction(method):
                raise RestlyConfigurationError(
                    f"{cls.__name__}.{name}{_set_by(cls, name)} is 'async "
                    "def', but a sync view calls it without await. Write "
                    f"'def {name}', or use fr.AsyncRestView."
                )
    for name in _LIST_METHODS:
        method = getattr(cls, name, None)
        if method is None or inspect.iscoroutinefunction(method) == is_async:
            continue
        origin = _set_by(cls, name)
        if is_async:
            raise RestlyConfigurationError(
                f"{cls.__name__}.{name}{origin} must be 'async def' on an "
                f"async view, which awaits it. Write 'async def {name}', and "
                f"'await super().{name}(...)'."
            )
        raise RestlyConfigurationError(
            f"{cls.__name__}.{name}{origin} is 'async def', but a sync view "
            f"calls it without await. Write 'def {name}', or use "
            "fr.AsyncRestView."
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


def _how_set(cls: type, name: str) -> str:
    """How ``cls`` has the list setting ``name``, for a message, such as
    ``inherits include_filters = ('*', '*.*') from AppView``."""
    value = _list_setting(cls, name)
    setter = _owner(cls, name)
    if value == _LIST_SETTING_DEFAULTS[name]:
        return f"has {name} = {value!r}"
    if setter is cls:
        return f"sets {name} = {value!r}"
    return f"inherits {name} = {value!r} from {setter.__name__}"


# Renamed before 1.0. Restly no longer reads the old names, so a definition
# would be silently dead, or would fail only on a request.
_RENAMED_SETTINGS = {"listing_param_schema": "schema_list_params"}
_RENAMED_METHODS = {
    "to_listing_response": "to_list_response",
    "to_react_admin_listing_response": "to_list_response",
    "to_response_schema": "to_single_response",
    "apply_query_params": "apply_list_params",
    "get_relationship_loader_options": "get_loader_options",
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
    if name in _RENAMED_METHODS:
        return (
            f"{view_name}.{name} is now {_RENAMED_METHODS[name]}."
            f"{_RENAMED_METHOD_HINTS.get(name, '')}"
        )
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

    responses: ClassVar[dict[int | str, dict[str, Any]]] = {
        404: {"description": "Not found"}
    }

    # The view's actions: the names that authorize and the transaction hooks
    # get. A view with actions of its own declares a subclass of fr.Action
    # with this name. (The docs show fr.Action, not this attribute.)
    Action = Action

    #: The view's schema: the Pydantic class that lists the fields of the
    #: resource. Restly builds :attr:`schema_response`, :attr:`schema_create`
    #: and :attr:`schema_update` from it. When a view sets none, Restly
    #: generates it from :attr:`model` with
    #: :func:`~fastapi_restly.schemas.derive_schema`.
    schema: ClassVar[type[pydantic.BaseModel]]
    #: The class of everything that goes out: the single responses, the
    #: items of the list response, and the fields the list params filter and
    #: sort on. The relationships it names are the ones the view loads. When
    #: a view sets none, Restly builds it with
    #: :func:`~fastapi_restly.schemas.derive_schema_response`: the view's
    #: schema without its ``WriteOnly`` fields, named ``<Resource>Response``.
    #: A class that a view sets is used as it is.
    schema_response: ClassVar[type[pydantic.BaseModel]]
    #: The class of the ``POST`` body. When a view sets none, Restly builds
    #: it from the view's schema without its ``ReadOnly`` fields and without
    #: a primary key that the server generates. It is named
    #: ``<Resource>Create``. A class that a view sets is used as it is.
    schema_create: ClassVar[type[pydantic.BaseModel]]
    #: The class of the ``PATCH`` body. When a view sets none, Restly builds
    #: it from the view's schema without its ``ReadOnly`` fields and without
    #: a primary key that the server generates, and makes every other field
    #: optional. It is named ``<Resource>Update``. A class that a view sets
    #: is used as it is.
    schema_update: ClassVar[type[pydantic.BaseModel]]
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
    #: The routes to leave out of the view, such as
    #: ``(fr.ViewRoute.DELETE,)``: :class:`ViewRoute` values for the CRUD
    #: routes, or the names of endpoint methods. A subclass that sets its own
    #: value replaces the list of its base. A name that is not a route
    #: raises ``AttributeError`` when the view is registered.
    exclude_routes: ClassVar[Collection[str | ViewRoute]] = ()
    #: What a list route does with a query key that it does not read.
    #: ``"forbid"`` (the default) rejects the request with 422, so a typo in
    #: a filter key does not return more rows than the client asked for.
    #: ``"ignore"`` leaves the key out, as FastAPI does. The values are those of
    #: Pydantic's ``extra`` setting. The same rule applies to a ``QueryParams``
    #: that a method gets as list params.
    unknown_query_params: ClassVar[Literal["forbid", "ignore"]] = "forbid"
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
    #: The filter keys that the list params keep, from the keys that Restly
    #: generates from :attr:`schema_response`. An entry is a key as the
    #: client sends it, such as ``"name__icontains"``, or a pattern. ``*``
    #: matches any characters except ``.``, so ``"created_at__*"`` is every
    #: filter on ``created_at`` with a suffix, and ``"vendor.*"`` is every
    #: key on the fields of ``vendor``. The default, ``("*",)``, keeps the
    #: keys of the resource's own fields, and no dotted keys. ``()`` keeps
    #: none. An entry that the view sets and that matches no key fails when
    #: the view registers.
    include_filters: ClassVar[tuple[str, ...]] = ("*",)
    #: The filter keys that the list params leave out, after
    #: :attr:`include_filters`. The entries are the same as there.
    exclude_filters: ClassVar[tuple[str, ...]] = ()
    #: The keys that ``sort`` accepts: a field as the client writes it in
    #: ``?sort=``, without the ``-``, such as ``"name"`` or
    #: ``"vendor.name"``, or a pattern as in :attr:`include_filters`. The
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
    #: The sort when the request has none, as a client writes it in
    #: ``?sort=``, such as ``"-created_at"``. It becomes the default of the
    #: sort parameter, so OpenAPI shows it. A key must be a field of
    #: :attr:`schema_response` that a sort key could have, but
    #: :attr:`include_sort_keys` does not have to keep it. Use each key only
    #: once. ``None`` (the default) sorts by the primary key. The primary key
    #: is always the last term. A view that inherits it and does not have all
    #: its keys sorts by the primary key.
    default_sort: ClassVar[str | None] = None
    #: Where a sort puts NULLs: ``"last"`` (the default) or ``"first"`` in
    #: both directions, on every database. ``None`` leaves them where the
    #: database puts them, which differs: PostgreSQL puts them last in an
    #: ascending sort, SQLite and MySQL first.
    sort_nulls: ClassVar[SortNulls] = "last"
    #: The list params (filter, sort, page) as a pydantic model, generated
    #: from ``schema_response`` and ``model`` by
    #: :func:`~fastapi_restly.query.derive_schema_list_params`. Any route
    #: method on the view that declares a ``list_params`` parameter takes it:
    #: typed for FastAPI and OpenAPI, and guarded against unknown keys like
    #: ``GET /``, so a custom list route (a trash route naming its own scope)
    #: reads the same list params. The route can take other query parameters
    #: beside it.
    schema_list_params: ClassVar[type[pydantic.BaseModel]]

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
        view's schema, as in the names of the classes that Restly generates.

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
        relationship that :attr:`schema_response` names. :meth:`select`
        applies them, so ``get_one``, ``get_many`` and your routes load the
        same, and so does the reload after the save of a write. Override to
        load more: a relationship that the response class does not name, or
        a ``query_expression`` through ``with_expression(...)``. Add to
        ``super().get_loader_options()`` to keep the default loads. See
        "Return related objects in a response" on the Relationships guide
        page.
        """
        return _build_relationship_loader_options(
            self.model, _schema_response(type(self))
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
        :attr:`schema_list_params`).

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
        schema_list_params = getattr(self, "schema_list_params", None)
        if schema_list_params is None:
            return
        list_keys = {key for key, _, _ in _list_params_fields(schema_list_params)}
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

        A ``QueryParams`` or a mapping goes through :attr:`schema_list_params`
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
        params_model = _schema_list_params(type(self))
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
        params_model = _schema_list_params(type(self))
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
                    "its schema_list_params runs the filter methods. Pass one, "
                    "or pass a dict or a QueryParams, which Restly validates "
                    "through schema_list_params.",
                    RestlySkippedFilterMethodsWarning,
                    stacklevel=3,
                )
        return record.own_filter_values(list_params) if record else []

    def to_single_response(self, obj: ModelT | ResponseSchemaT) -> ResponseSchemaT:
        """Serialize one ORM object to the view's :attr:`schema_response`.

        By default the response class is the view's schema without its
        WriteOnly fields, named like ``UserResponse``. An override may return
        an instance of another model, such as :attr:`schema`. Only the fields
        of the response class go out. Restly copies the values of an instance
        of a subclass of the response class, and of :attr:`schema` when
        Restly derived the response class, without validating them again. It
        validates any other model into the response class from its
        attributes.

        The view loads only the relationships that the response class names.
        An override that builds :attr:`schema` from the ORM object also reads
        the relationships of its WriteOnly fields. On an async view that fails
        with ``MissingGreenlet``: add them in
        :meth:`get_loader_options`, or build the response class
        instead.

        The ORM path below validates through the response class, so a view's
        schema that declares a WriteOnly field the ORM object doesn't carry
        (e.g. ``password`` backed by a ``password_hash`` column) doesn't fail
        response validation.
        """
        schema_response = _schema_response(type(self))
        response = _as_response(schema_response, obj)
        if isinstance(response, schema_response):
            return cast(ResponseSchemaT, response)

        # Build a payload of raw attribute values keyed by schema field name;
        # re-validating it below serializes each field through its own type. The
        # response schema's ``from_attributes`` config resolves nested schemas and
        # reference types (IDRef/IDSchema) straight from the ORM rows, with no
        # view-layer special-casing. Alias rendering happens when FastAPI
        # serializes the response model.
        payload: dict[str, Any] = {}
        for field_name, field_info in schema_response.model_fields.items():
            if hasattr(obj, field_name):
                payload[field_name] = getattr(obj, field_name)
            elif field_info.alias and hasattr(obj, field_info.alias):
                payload[field_name] = getattr(obj, field_info.alias)

        adapter = _response_adapter(schema_response)
        return cast(
            ResponseSchemaT,
            adapter.validate_python(payload, by_alias=False, by_name=True),
        )

    def _single_response(self, obj: Any) -> pydantic.BaseModel:
        """One object as an instance of the response class. An override of
        :meth:`to_single_response` may return an instance of another model."""
        return _as_response(_schema_response(type(self)), self.to_single_response(obj))

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
        data = [self._single_response(obj) for obj in list_result.objects]
        pagination = self.pagination
        envelope = derive_schema_list_response(
            _schema_response(type(self)), pagination=pagination
        )
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

        ``shape`` selects the response form, and with it what ``result`` is: one
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
        return self._single_response(result)

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
    def _check_declared_schema_list_params(cls) -> None:
        """Reject declared list params that do not follow the view's settings.

        Restly generates list params from the pagination, the list settings
        and the filter methods. A subclass that changes one of them gets new
        list params only when the declared ones are what Restly would
        generate for the class that declares them (see
        ``_generated_for``). Otherwise the declared list params stay, and
        must fit the view. List params from ``derive_schema_list_params``
        must have the view's settings: other settings give other keys than
        the view says. Hand-written list params take no settings and no
        filter methods.
        """
        declared = _DeclaredListParams(cls, _owner(cls, "schema_list_params"))
        record = _record_of(cls.schema_list_params)
        if record is None:
            declared.check_hand_written()
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

        # A declared schema is inherited like any attribute. One that Restly
        # generated for a parent is rebuilt, since the subclass may change
        # what it was derived from.
        if _needs_generating(cls, "schema"):
            if not hasattr(cls, "model"):
                raise ValueError(
                    f"'{cls.__name__}.model' must be specified to auto-generate schema"
                )
            cls.schema = derive_schema(cls.model)
            _mark_generated(cls, "schema")

        if _needs_generating(cls, "schema_response"):
            cls.schema_response = derive_schema_response(cls.schema)
            _mark_generated(cls, "schema_response")

        if _needs_generating(cls, "schema_list_params"):
            cls.schema_list_params = _derive_view_list_params(cls)
            _mark_generated(cls, "schema_list_params")
        else:
            cls._check_declared_schema_list_params()
        if _needs_generating(cls, "schema_create"):
            cls.schema_create = cast(
                type[CreateSchemaT],
                create_model_without_read_only_fields(
                    cls.schema, omit=_generated_primary_key_fields(cls.model)
                ),
            )
            _mark_generated(cls, "schema_create")
        if _needs_generating(cls, "schema_update"):
            cls.schema_update = cast(
                type[UpdateSchemaT],
                create_model_with_optional_fields(
                    cls.schema, omit=_generated_primary_key_fields(cls.model)
                ),
            )
            _mark_generated(cls, "schema_update")

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
        view_types = _view_types(cls)
        for name, route in list(cls.__dict__.items()):
            if not hasattr(route, "_api_route_args"):
                continue
            if "list_params" in _route_parameters(route) and not getattr(
                route, "_fr_list_guard", False
            ):
                route = _guard_list_params(route)
                setattr(cls, name, route)
            _fill_view_types(route, view_types, list_params)
        _check_exclude_routes(cls)
        _check_composite_key(cls)


#: What registration builds each class of a view from.
_BUILT_FROM = {
    "schema": "model",
    "schema_response": "schema",
    "schema_list_params": "schema_response",
    "schema_create": "schema",
    "schema_update": "schema",
}


def _mark_generated(cls: type, name: str) -> None:
    generated = cls.__dict__.get("_fr_generated", frozenset())
    cls._fr_generated = generated | {name}  # type: ignore[attr-defined]


def _declared_at(cls: type, name: str) -> int | None:
    """Position in the MRO of the class that declares ``name``, or None.

    A value that registration generated is not a declaration.
    """
    for index, klass in enumerate(cls.__mro__):
        if name in klass.__dict__ and name not in klass.__dict__.get(
            "_fr_generated", ()
        ):
            return index
    return None


def _decided_at(cls: type, name: str) -> int | None:
    """Position in the MRO of the class that decides the value of ``name``.

    A declared value is decided where it is declared, unless what it is built
    from is decided nearer to ``cls``: then registration rebuilds it. A
    generated value is decided where its source is decided. So a copy that
    registration wrote into a subclass does not count as a change. Declared
    list params that Restly would generate for the declaring class are also
    built from the list settings and the filter methods.
    """
    declared = _declared_at(cls, name)
    source = _BUILT_FROM.get(name)
    if source is None:
        return declared
    source_at = _decided_at(cls, source)
    if name == "schema_list_params" and declared is not None:
        # Declared list params that are the generated ones also follow the
        # settings and the filter methods of a subclass.
        settings_at = _list_settings_at(cls)
        if settings_at < declared and _generated_for(cls.__mro__[declared]):
            if source_at is None or settings_at < source_at:
                source_at = settings_at
    if declared is None:
        return source_at
    # A declared schema stays, whatever model a subclass sets.
    if name != "schema" and source_at is not None and source_at < declared:
        return source_at
    return declared


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
        return f"its schema_list_params{self.origin}"

    @property
    def out(self) -> str:
        """What to leave out to have the list params generated, after
        "Leave" or "leave"."""
        of = "" if self.declarer is self.cls else f" of {self.declarer.__name__}"
        return f"schema_list_params out{of} to have it generated"

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

    def check_derived(self, record: Any) -> None:
        """List params from ``derive_schema_list_params`` have the view's
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
                f"{self.cls.__name__}.schema_list_params{self.origin} has the "
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
                    f"schema_list_params on {setter.__name__} as well, created "
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
                f"{self.cls.__name__}.schema_list_params{self.origin} was "
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
                f"{self.remove(name, setter)}, or declare schema_list_params on "
                f"{setter.__name__} as well, created by "
                f"fr.query.derive_schema_list_params() with {name}={value!r}"
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


_GENERATED_FOR: "weakref.WeakKeyDictionary[type, bool]" = weakref.WeakKeyDictionary()


def _generated_for(declarer: type) -> bool:
    """Whether the list params that ``declarer`` declares are the ones that
    Restly would generate for it.

    Only then does a subclass with other settings get new list params.
    List params from a narrower schema, or with settings that the class does
    not have, would get wider keys.
    """
    if declarer in _GENERATED_FOR:
        return _GENERATED_FOR[declarer]
    declared = vars(declarer)["schema_list_params"]
    generated: type[pydantic.BaseModel] | None = None
    if _record_of(declared) is not None:
        try:
            generated = _derive_view_list_params(declarer)
        except (AttributeError, ValueError, RestlyConfigurationError):
            pass  # the class cannot generate list params, such as a mixin
    result = (
        generated is not None
        and list(generated.model_fields) == list(declared.model_fields)
        and _record_of(generated) == _record_of(declared)
    )
    _GENERATED_FOR[declarer] = result
    return result


def _list_settings_at(cls: type) -> int:
    """Position in the MRO of the nearest class that sets the pagination, a
    list setting or a filter method, or drops a filter method with None."""
    names = {"pagination", *_LIST_SETTING_DEFAULTS}
    for klass in cls.__mro__:
        names.update(
            name
            for name, value in vars(klass).items()
            if hasattr(value, "_fr_list_filter")
        )
    return next(
        index for index, klass in enumerate(cls.__mro__) if names & vars(klass).keys()
    )


def _needs_generating(cls: type, name: str) -> bool:
    """Whether registration must build ``name`` for this view class.

    A declared value is inherited. Registration builds it when no class
    declares it, or when what it is built from is decided nearer to ``cls``,
    as in a subclass that declares a new ``schema``.
    """
    if name in cls.__dict__.get("_fr_generated", ()):
        return False
    declared = _declared_at(cls, name)
    return declared is None or _decided_at(cls, name) != declared


def _schema_response(view_cls: type) -> type[pydantic.BaseModel]:
    """The response class of a view.

    Registration sets ``schema_response``. A view that is not registered
    yet, or a subclass that changes ``schema``, gets the class that
    registration would set.
    """
    if _needs_generating(view_cls, "schema_response"):
        return derive_schema_response(view_cls.schema)
    return view_cls.schema_response


def _derive_view_list_params(view_cls: type) -> type[pydantic.BaseModel]:
    """The list params that registration generates for a view: from its
    response class, its pagination, its list settings and its filter
    methods."""
    if not hasattr(view_cls, "model"):
        raise ValueError(
            f"'{view_cls.__name__}.model' must be specified: it is needed to "
            "generate the list params."
        )
    return _derive_list_params(
        _schema_response(view_cls),
        view_cls.model,
        pagination=view_cls.pagination,
        settings={name: getattr(view_cls, name) for name in _KEY_SETTINGS},
        owner=view_cls.__name__,
        # a base view serves many schemas
        unchecked={name for name in _KEY_SETTINGS if name not in vars(view_cls)},
        own_filters=_own_filters(view_cls),
        sort_query_param=view_cls.sort_query_param,
        default_sort=_list_setting(view_cls, "default_sort"),
        default_sort_from=(
            None
            if "default_sort" in vars(view_cls)
            else _owner(view_cls, "default_sort").__name__
        ),
    )


def _schema_list_params(view_cls: type) -> type[pydantic.BaseModel]:
    """The list params of a view.

    Registration sets ``schema_list_params``. A view that is not registered
    yet, such as a subclass of a registered view, gets the class that
    registration would set.
    """
    if _needs_generating(view_cls, "schema_list_params"):
        return _derive_view_list_params(view_cls)
    return view_cls.schema_list_params


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


def _model_scope(model: type[Any]) -> WhereClause:
    declared = _default_scope(model)
    return UNSCOPED if declared is None else declared


def _checked_scope(scope: Any, label: str, what: str) -> WhereClause:
    if isinstance(scope, WhereClause):
        return scope
    raise RestlyConfigurationError(
        f"{label}: {what} must be a WhereClause or fr.clauses.UNSCOPED, got "
        f"{type(scope).__name__}; wrap a raw expression with where_clause()"
    )


def _clause_element(value: Any) -> Any:
    """The SQL expression behind a mapped column or a hybrid property, such
    as ``Item.archived``, which ``.where()`` accepts too. Other values are
    returned as they are."""
    if isinstance(value, QueryableAttribute):
        return value.__clause_element__()
    return value


def _checked_where(where: Any, label: str) -> WhereClause:
    if isinstance(where, WhereClause):
        return where
    where = _clause_element(where)
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


def _check_identity_tables(
    model_cls: type[Any], id: ColumnElement[bool], label: str
) -> None:
    """Raise when the expression that picks the row names another table.

    SQLAlchemy adds such a table to the FROM of the query, which joins every
    row of it, so ``Shelf.id == 2`` in a view of books picks a book that has
    nothing to do with shelf 2.
    """
    missing = _expression_tables(id) - _statement_tables(sa_select(model_cls))
    if missing:
        name = model_cls.__name__
        shown = ", ".join(sorted(_display_table_name(key) for key in missing))
        raise TypeError(
            f"{label}: the expression that picks the row names a table that is "
            f"not in the query: {shown}. It would join every row of that "
            f"table, so a wrong row could be loaded. Compare a column of "
            f"{name}, such as {name}.slug == slug, or use .has() or .any() "
            "for a related row."
        )


def _reject_bad_row_identity(label: str, id: Any) -> None:
    """Raise for a value that looks like an id but cannot pick a row."""
    if isinstance(id, WhereClause):
        raise TypeError(
            f"{label}: a clause is a scope, not a row identity. "
            "Call it for its expression (ItemClauses.published()), or "
            "pass it as scope= to replace the view scope for this read."
        )
    if type(id) is bool:
        # ``obj.slug == slug`` on a loaded object is a Python bool, and
        # SQLAlchemy renders it as WHERE true, which matches the whole scope
        raise TypeError(
            f"{label}: got a bool, not an id or a SQL expression. "
            "A comparison on an instance attribute is a bool; compare the "
            "column instead (Model.slug == slug)."
        )


def _primary_key_criterion(model_cls: type[Any], id: Any) -> ColumnElement[bool]:
    """The default :meth:`BaseRestView.where_id`: the primary key is ``id``."""
    pk_cols = sa_inspect(model_cls).primary_key
    if len(pk_cols) != 1:
        raise NotImplementedError(
            f"{model_cls.__name__} has a composite primary key, so an id does "
            "not name one row. Override where_id(id): split the id from the "
            "path, and return sqlalchemy.and_(Model.a == a, Model.b == b)."
        )
    return pk_cols[0] == id


def _not_found_message(resource: str, id: Any) -> str:
    # ``id`` is None for a route that loaded by an expression: the 404 body
    # is no place for SQL
    if id is None:
        return f"{resource} was not found"
    return f"{resource} with id {id} was not found"


def reject_unknown_query_keys(
    query: QueryParams, allowed: set[str], request: fastapi.Request | None
) -> None:
    """Raise a 422 naming every key of ``query`` that is not in ``allowed``.

    The list params decide what is allowed: their keys for the default list
    params, ``sort``/``range``/``filter`` for react-admin. A key that the
    route of ``request`` reads elsewhere is never unknown (see
    :func:`_route_query_keys`).

    :raises RequestValidationError: ``query`` has an unknown key.
    """
    unknown = set(query.keys()) - allowed
    if unknown and request is not None:
        unknown -= _route_query_keys(request)
    if unknown:
        raise _unknown_keys_error(query, unknown)


def _unknown_keys_error(query: QueryParams, keys: set[str]) -> RequestValidationError:
    """The error for unknown query keys, in the shape of FastAPI's own
    validation errors."""
    return RequestValidationError(
        [
            {
                "type": "extra_forbidden",
                "loc": ("query", key),
                "msg": f"Unknown query parameter {key!r}",
                "input": query.get(key),
            }
            for key in sorted(keys)
        ]
    )


def _route_query_keys(request: fastapi.Request) -> set[str]:
    """The query keys that the matched route reads.

    Every query parameter its dependency tree declares, at any level: the
    app or a router, the view's ``dependencies``, a class attribute, the
    endpoint. Also the key of a query API key scheme such as
    ``APIKeyQuery``, which reads the query string without declaring a
    parameter. The guard walks the tree only for a key that the list
    params do not have.
    """
    dependant = getattr(matched_route(request.scope), "dependant", None)
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
    fields = _list_params_fields(params_model)

    def list_params(request: fastapi.Request, first: Any = None) -> pydantic.BaseModel:
        return _validate_list_params(params_model, fields, request.query_params)

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


#: The query key of each field of a list params model, the field, and
#: whether the field reads every value of a repeated key.
_ListParamsFields = tuple[tuple[str, FieldInfo, bool], ...]

_LIST_PARAMS_FIELDS: "weakref.WeakKeyDictionary[type, _ListParamsFields]" = (
    weakref.WeakKeyDictionary()
)


def _list_params_fields(params_model: type[pydantic.BaseModel]) -> _ListParamsFields:
    # once per model: a raw QueryParams or mapping reads them on every call
    fields = _LIST_PARAMS_FIELDS.get(params_model)
    if fields is None:
        fields = tuple(
            (_query_key(name, field), field, _reads_every_value(field))
            for name, field in params_model.model_fields.items()
        )
        _LIST_PARAMS_FIELDS[params_model] = fields
    return fields


def _validate_list_params(
    params_model: type[pydantic.BaseModel],
    fields: _ListParamsFields,
    query: QueryParams,
) -> pydantic.BaseModel:
    """Validate ``params_model`` from ``query``, as FastAPI validates a
    query model.

    :raises RequestValidationError: a value fails the validation of its
        field. The location of each error starts with ``"query"``.
    """
    field_keys = {key for key, _, _ in fields}
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


def _validate_raw_list_params(
    params_model: type[pydantic.BaseModel], raw: Any
) -> pydantic.BaseModel:
    """Validate query parameters that a caller passes instead of list params.

    The values are validated as on the list route. In a mapping, a list
    value is a repeated key.

    :raises RequestValidationError: a key is not a key of ``params_model``,
        or a value fails the validation of its field.
    """
    fields = _list_params_fields(params_model)
    if isinstance(raw, QueryParams):
        query = raw
    else:
        query = QueryParams(
            [
                (key, item)
                for key, value in dict(raw).items()
                for item in (value if isinstance(value, (list, tuple)) else [value])
            ]
        )
    unknown = set(query.keys()) - {key for key, _, _ in fields}
    if unknown:
        raise _unknown_keys_error(query, unknown)
    return _validate_list_params(params_model, fields, query)


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

    A copy, like the parent-endpoint copies: it keeps the route args and the
    signature. The marker keeps a subclass's copy from being wrapped twice.
    """
    if inspect.iscoroutinefunction(route):

        async def _async_guarded(self, *args, **kwargs):
            self._reject_unknown_query_params()
            return await route(self, *args, **kwargs)

        guarded: Callable = _async_guarded
    else:

        def _sync_guarded(self, *args, **kwargs):
            self._reject_unknown_query_params()
            return route(self, *args, **kwargs)

        guarded = _sync_guarded

    guarded = _wrap_endpoint(guarded, route)
    guarded._fr_list_guard = True  # type: ignore[attr-defined]
    return guarded


def _excluded_route_names(view_cls: type) -> frozenset[str]:
    """The endpoint method names that the view's ``exclude_routes`` names."""
    return frozenset(
        route.value if isinstance(route, ViewRoute) else route
        for route in getattr(view_cls, "exclude_routes", ())
    )


def _check_exclude_routes(cls: type[BaseRestView[Any, Any, Any, Any, Any]]):
    """Raise for a name in ``exclude_routes`` that is no route at all: a typo,
    or the business method name instead of the ``*_endpoint`` route name.

    The excluded endpoints stay routes on the class. The router leaves them
    out. So a subclass with its own ``exclude_routes`` gets them back, also
    when the base registered first and copied them into its own namespace.
    """
    for method_name in sorted(_excluded_route_names(cls)):
        if not _is_route_name_in_lineage(cls, method_name):
            raise AttributeError(f"{method_name!r} is not a route on {cls.__name__}")


def _is_route_name_in_lineage(
    cls: type[BaseRestView[Any, Any, Any, Any, Any]], method_name: str
) -> bool:
    """True if any class in ``cls``'s MRO defines a routable endpoint of this
    name."""
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
    _register_for_resource_ref(parent_router, view_cls)
    parent_router.include_router(api_router)
    # Record only after the mount succeeded: a failed registration must not be
    # marked as mounted, or the next call would silently no-op a view whose
    # routes never made it onto the parent.
    mounted.add(view_cls)
    setattr(holder, "_fr_mounted_views", mounted)


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


def _source_of(value: Any) -> str:
    """The source code of a function on a view class, or ``""`` when Python
    has none, such as for a function that was built at run time."""
    func = getattr(value, "__func__", value)
    if not isinstance(func, types.FunctionType):
        return ""
    try:
        return inspect.getsource(func)
    except (OSError, TypeError):
        return ""


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

    # 1. Endpoint-method override where a business-method override was likely
    # meant. A replacement that calls the handler, or the endpoint method of
    # its base, keeps the steps: that is how a view changes the HTTP contract.
    if issubclass(view_cls, BaseRestView):
        for endpoint in sorted(_ENDPOINT_METHOD_NAMES & own.keys()):
            verb = endpoint.removesuffix("_endpoint")
            source = _source_of(own[endpoint])
            if f"handle_{verb}(" in source or f"super().{endpoint}(" in source:
                continue
            if verb in _WRITE_VERBS:
                seams = f"For domain logic override the business method '{verb}'"
                keep = "the bracket"
            else:
                lookup = "where_id" if verb == "get_one" else "apply_list_params"
                seams = f"For the rows override 'scope', for the lookup '{lookup}'"
                keep = "the gate"
            warnings.warn(
                f"{name} overrides the endpoint method '{endpoint}'. Override "
                f"an endpoint method only to change the HTTP contract. {seams}; "
                f"for a gate 'authorize'; for the response shape 'to_response'. "
                f"Call 'handle_{verb}' from the replacement to keep {keep}.",
                RestlyMisuseWarning,
                stacklevel=5,
            )

    # 2. Manual session.commit() in a view method. The framework owns the
    # commit; methods that go through write_action / handle_<verb> are exempt.
    for attr, value in own.items():
        source = _source_of(value)
        if (
            ".commit(" in source
            and "write_action" not in source
            and "handle_" not in source
        ):
            warnings.warn(
                f"{name}.{attr} calls session.commit() directly. The framework "
                f"owns the commit: reuse handle_<verb>(), or bracket the "
                f"mutation with write_action(self.Action.<NAME>, ...) so authorize / "
                f"before_action_commit / after_action_commit run.",
                RestlyMisuseWarning,
                stacklevel=5,
            )

    # 3. A reference type (IDRef/IDSchema) whose name is a scalar FK column:
    # the ``post_id: IDRef[Post]`` mistake, where ``data.post_id`` becomes a
    # wrapper instead of the plain id.
    _warn_scalar_named_reference_fields(view_cls)

    # 4. A writable field on a column that the model sets, such as a tenant
    # or an audit column: the request body replaces the column default.
    _warn_writable_model_set_fields(view_cls)


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


def _model_set_fields(model: type[Any]) -> dict[str, str]:
    """Map each attribute whose write sets a column that the model sets to
    that column.

    The model sets a column that the constructor does not take
    (``init=False``) and that has a default: ``default`` or
    ``default_factory`` on the field, or ``insert_default`` or
    ``server_default`` on the column. A many-to-one relationship maps to its
    foreign key column, because a write to the relationship sets it. A
    primary key that the server generates is left out, because Restly already
    leaves it out of the request bodies.
    """
    if not dataclasses.is_dataclass(model):
        return {}
    mapper = sa_inspect(model)
    generated_keys = _generated_primary_key_fields(model)
    columns: dict[str, str] = {}
    for field in dataclasses.fields(model):
        if field.init or field.name in generated_keys:
            continue
        column = mapper.columns.get(field.name)
        if column is None:
            continue
        if (
            field.default is not dataclasses.MISSING
            or field.default_factory is not dataclasses.MISSING
            or column.default is not None
            or column.server_default is not None
        ):
            columns[field.name] = field.name
    for relationship in mapper.relationships:
        for column in relationship.local_columns:
            key = mapper.get_property_by_column(column).key
            if key in columns:
                columns[relationship.key] = key
    return columns


def _warn_writable_model_set_fields(view_cls: type[View]) -> None:
    """Flag a writable field that sets a column that the model sets.

    A column with ``init=False`` and a default, such as a tenant column with
    ``insert_default=Current.org_id``, gets its value from the model. Restly
    sets each writable field that the constructor does not take after it
    builds the object, so a value in the request body replaces the default:
    a client can pick the tenant. Lints the write classes of the routes that
    the view has: each class that the view declares, and the declared
    ``schema`` when Restly derives a write class from it. A generated
    ``schema`` already marks these columns ReadOnly.
    """
    if not issubclass(view_cls, BaseRestView):
        return
    model = getattr(view_cls, "model", None)
    if model is None:
        return
    try:
        model_set = _model_set_fields(model)
    except Exception:
        return
    if not model_set:
        return
    excluded = _excluded_route_names(view_cls)
    # Each write class, and whether Restly derives it from ``schema``.
    write_classes: dict[type[pydantic.BaseModel], bool] = {}
    for attr, route in (
        ("schema_create", ViewRoute.CREATE),
        ("schema_update", ViewRoute.UPDATE),
    ):
        if route.value in excluded:
            continue
        if not _needs_generating(view_cls, attr):
            write_classes.setdefault(getattr(view_cls, attr), False)
        elif _declared_at(view_cls, "schema") is not None:
            write_classes.setdefault(view_cls.schema, True)
    for cls, derived in write_classes.items():
        for field_name, field_info in cls.model_fields.items():
            column = model_set.get(field_name)
            if column is None or _is_readonly(field_info):
                continue
            through = (
                ""
                if column == field_name
                else f" and sets `{model.__name__}.{column}` through the relationship"
            )
            fix = (
                "Mark the field `fr.ReadOnly[...]`"
                if derived
                else f"Leave the field out of {cls.__name__}"
            )
            warnings.warn(
                f"{cls.__name__}.{field_name} is writable{through}, but "
                f"`{model.__name__}.{column}` has `init=False` and a default, "
                f"so the model sets it. A value in the request body replaces "
                f"the default. {fix}, so the value always comes from the "
                f"model. If the client may set it, remove `init=False` from "
                f"the column, so that the constructor takes it.",
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
    for attr in ("schema", "schema_response", "schema_create", "schema_update"):
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
    if _fr_config.warn_on_misuse:
        _warn_on_misuse(view_cls)
    _copy_all_parent_class_endpoints_into_this_subclass(view_cls)
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
    we add the annotation that FooView.get() returns FooSchema but do not make a copy
    then AsyncRestView.get() and all other subclasses will get the FooSchema
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

        async def _async_wrapper(self, *args, **kwargs):
            return await endpoint(self, *args, **kwargs)

        endpoint_wrapper: Callable = _async_wrapper
    else:

        def _sync_wrapper(self, *args, **kwargs):
            return endpoint(self, *args, **kwargs)

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


def _init_all_endpoints(view_cls: type[View]):
    """
    Ensure every endpoint has a unique name and update the 'self' annotation.
    """
    for name, attr in view_cls.__dict__.items():
        if not hasattr(attr, "_api_route_args"):
            continue
        endpoint = attr
        # Give every endpoint a unique name: FooView.create_endpoint gets
        # "fooview_create_endpoint". From the attribute, as for a copied
        # endpoint, not from __name__: a registration that failed has renamed
        # the endpoint already, and a retry must not add the prefix again.
        endpoint.__name__ = view_cls.__name__.lower() + "_" + name
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


def _view_types(view_cls: type) -> dict[str, Any]:
    """The class that each marker of ``fastapi_restly.views`` stands for."""
    view = cast(Any, view_cls)
    return {
        "ViewID": _view_id_type(view_cls),
        "ViewResponse": view.schema_response,
        "ViewListResponse": derive_schema_list_response(
            view.schema_response, pagination=view.pagination
        ),
        "ViewCreate": view.schema_create,
        "ViewUpdate": view.schema_update,
    }


def _fill_view_types(
    func: Callable, view_types: Mapping[str, Any], list_params: Any
) -> None:
    """Put the view's classes in place of the markers in ``func.__signature__``.

    A parameter named ``list_params`` always takes the view's list params,
    whatever its annotation says: the unknown-key guard checks the keys
    against them.
    """
    # Read what the author wrote, not the signature: functools.update_wrapper
    # copies a registered parent's filled-in signature onto a subclass's copy.
    written = inspect.get_annotations(func)
    sig = inspect.signature(func)
    changed = False
    params = []
    for param in sig.parameters.values():
        if param.name == "list_params":
            annotation = list_params
        elif param.name in written:
            annotation = _with_view_types(written[param.name], func, view_types)
        else:
            annotation = param.annotation
        changed = changed or annotation is not param.annotation
        params.append(param.replace(annotation=annotation))
    returns = sig.return_annotation
    if "return" in written:
        returns = _with_view_types(written["return"], func, view_types)
    if changed or returns is not sig.return_annotation:
        func.__signature__ = sig.replace(  # type: ignore[attr-defined]
            parameters=params, return_annotation=returns
        )


def _with_view_types(
    annotation: Any, func: Callable, view_types: Mapping[str, Any]
) -> Any:
    """``annotation`` with the view's classes in place of its markers.

    It is ``annotation`` itself when it holds no marker. So an annotation
    that is a string, under ``from __future__ import annotations``, stays a
    string for FastAPI to resolve.
    """
    evaluated = _evaluated(annotation, func)
    filled = _replace_view_types(evaluated, view_types)
    return annotation if filled is evaluated else filled


def _evaluated(annotation: Any, func: Callable) -> Any:
    """A string annotation, evaluated in the module of the route's author.

    A string that does not evaluate there, such as one that names a class of
    a function body, stays a string.
    """
    if not isinstance(annotation, str):
        return annotation
    try:
        return eval(annotation, inspect.unwrap(func).__globals__)  # noqa: S307
    except Exception:
        return annotation


def _replace_view_types(annotation: Any, view_types: Mapping[str, Any]) -> Any:
    """Replace each marker in ``annotation``, also inside a type such as
    ``list[ViewResponse]``. Returns ``annotation`` itself when it holds no
    marker."""
    origin = get_origin(annotation)
    if origin is Annotated:
        inner, *metadata = get_args(annotation)
        marker = next((m for m in metadata if isinstance(m, _ViewType)), None)
        if marker is not None:
            # Annotated[ViewID, Path(ge=1)] keeps the Path(ge=1)
            rest = [m for m in metadata if not isinstance(m, _ViewType)]
            filled = view_types[marker.name]
            return Annotated[(filled, *rest)] if rest else filled
        filled_inner = _replace_view_types(inner, view_types)
        if filled_inner is inner:
            return annotation
        return Annotated[(filled_inner, *metadata)]
    args = get_args(annotation)
    if not args or origin is Literal:
        return annotation
    filled_args = tuple(_replace_view_types(arg, view_types) for arg in args)
    if all(new is old for new, old in zip(filled_args, args)):
        return annotation
    if origin in (Union, types.UnionType):
        return Union[filled_args]
    if isinstance(annotation, types.GenericAlias):
        return types.GenericAlias(origin, filled_args)
    return annotation.copy_with(filled_args)


def _view_type_in(annotation: Any) -> _ViewType | None:
    """The first marker in ``annotation``, or ``None``."""
    if get_origin(annotation) is Annotated:
        for metadata in get_args(annotation)[1:]:
            if isinstance(metadata, _ViewType):
                return metadata
    if get_origin(annotation) is Literal:
        return None
    for arg in get_args(annotation):
        if (found := _view_type_in(arg)) is not None:
            return found
    return None


def _view_route_args(
    endpoint: Callable, view_types: Mapping[str, Any]
) -> tuple[str, dict[str, Any]]:
    """The path and the route arguments of ``endpoint``, for this view.

    ``response_model`` can hold a marker. A path parameter annotated
    ``ViewID`` gets the converter of the view's id type, unless the path
    gives one already. The decorator's arguments stay as written, so a
    subclass with another id type gets its own path.
    """
    path, route_kwargs = endpoint._api_route_args  # type: ignore[attr-defined]
    response_model = route_kwargs.get("response_model")
    filled = _replace_view_types(response_model, view_types)
    if filled is not response_model:
        route_kwargs = {**route_kwargs, "response_model": filled}
    converter = {int: "int", UUID: "uuid"}.get(view_types["ViewID"])
    if converter is not None:
        for name, annotation in inspect.get_annotations(endpoint).items():
            annotation = _evaluated(annotation, endpoint)
            is_id = get_origin(annotation) is Annotated and any(
                isinstance(m, _ViewType) and m.name == "ViewID"
                for m in get_args(annotation)[1:]
            )
            if is_id:
                path = path.replace(f"{{{name}}}", f"{{{name}:{converter}}}")
    return path, route_kwargs


def _marker_name(annotation: Any) -> str | None:
    """The name of the marker that ``annotation`` is, such as
    ``"ViewResponse"``, or ``None`` when it is not a marker."""
    if get_origin(annotation) is Annotated:
        for metadata in get_args(annotation)[1:]:
            if isinstance(metadata, _ViewType):
                return metadata.name
    return None


def _without_none(annotation: Any) -> tuple[Any, bool]:
    """``annotation`` without ``None``, and whether it allowed ``None``:
    ``ViewResponse | None`` gives ``(ViewResponse, True)``."""
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
    :func:`~fastapi_restly.schemas.derive_schema_response`: each call with
    the same schema returns that same class.
    """
    own = _view_types(view_cls)
    name = _marker_name(declared)
    if name == "ViewResponse" or declared is own["ViewResponse"]:
        return "single", None
    if name == "ViewListResponse" or declared is own["ViewListResponse"]:
        return "list", None
    if get_origin(declared) is list:
        (item,) = get_args(declared) or (None,)
        if _marker_name(item) == "ViewResponse" or item is own["ViewResponse"]:
            return "many", None
        if not in_decorator and item is view_cls.model:
            return "many", list[view_cls.schema_response]  # type: ignore[name-defined]
    if not in_decorator and declared is view_cls.model:
        return "single", view_cls.schema_response
    return None, None


def _with_view_response(
    view_cls: Any, endpoint: Callable, route_kwargs: dict[str, Any]
) -> tuple[Callable, dict[str, Any]]:
    """The endpoint that FastAPI calls, and its route arguments.

    A route that declares the view's response answers through the view's
    response step, also when it returns the row: a row goes through
    :meth:`~BaseRestView.to_response`, each row of a list, or of another
    iterable such as the result of ``session.scalars()``, through
    :meth:`~BaseRestView.to_single_response`, and a ``ListResult`` through
    ``to_response(result, ResponseShape.LIST)``. Anything else, such as
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
            return view.to_response(result)
        if kind == "many" and _is_rows(result):
            rows = list(result)
            if all(isinstance(row, view.model) for row in rows):
                return [view.to_single_response(row) for row in rows]
            # the iterable is used up: send the items that it gave
            return rows
        if kind == "list" and isinstance(result, ListResult):
            return view.to_response(result, ResponseShape.LIST)
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


def _reject_view_types_on_a_plain_view(
    view_cls: type, name: str, endpoint: Callable, route_kwargs: Mapping[str, Any]
) -> None:
    """Raise when a route of a plain View uses a marker: there is no type to
    put in its place."""
    sig = inspect.signature(endpoint)
    annotations = [p.annotation for p in sig.parameters.values()]
    annotations += [sig.return_annotation, route_kwargs.get("response_model")]
    for annotation in annotations:
        marker = _view_type_in(_evaluated(annotation, endpoint))
        if marker is not None:
            raise RestlyConfigurationError(
                f"{view_cls.__name__}.{name} uses {marker!r}, which stands for "
                f"a type of a CRUD view. {view_cls.__name__} is a plain View, "
                "so it has no such type. Write the type, or base the view on "
                "fr.AsyncRestView or fr.RestView."
            )


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
    view_types = _view_types(view_cls) if issubclass(view_cls, BaseRestView) else None
    # exclude_routes is a setting of the CRUD views only
    excluded = _excluded_route_names(view_cls) if view_types is not None else ()
    for name, attr in view_cls.__dict__.items():
        if not hasattr(attr, "_api_route_args") or name in excluded:
            continue
        endpoint = attr
        if view_types is not None:
            path, route_kwargs = _view_route_args(endpoint, view_types)
        else:
            path, route_kwargs = endpoint._api_route_args
            _reject_view_types_on_a_plain_view(view_cls, name, endpoint, route_kwargs)
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
        route_kwargs = {**defaults, **route_kwargs}
        if view_types is not None:
            endpoint, route_kwargs = _with_view_response(
                view_cls, endpoint, route_kwargs
            )
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
# marked annotations as dependency class attributes; everything else is
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
    # framework keeps the dependency over the most-derived hint.
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
            is_special_type = inspect.isclass(underlying) and issubclass(
                underlying, _FASTAPI_SPECIAL_INJECTABLE
            )
            if has_depends_marker or is_special_type:
                # Marker-bearing annotation wins, regardless of MRO position.
                di_annotations[name] = annotation
            # Plain annotations are silently ignored — they neither set
            # nor clear an entry in di_annotations.

    # FastAPI never fills an attribute with a request-parameter marker: it
    # would keep its class default, with no error and no OpenAPI entry. A
    # Depends marker on the same name makes it a dependency, as above.
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
