"""Copy the fields of a schema object onto a model object.

The CRUD views and the functions in ``fastapi_restly.objects`` use this code
to create an object and to update one. For each field it finds the column or
the relationship that the field sets. It also checks that a foreign key and
its relationship in one body name the same row.
"""

import dataclasses
from typing import Any, Iterator, Protocol, cast

import fastapi
import pydantic
from fastapi.exceptions import RequestValidationError
from sqlalchemy import JSON as _JSONType
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm.attributes import del_attribute

from .._columns import column_value
from .._mapping import is_mapped_instance, is_to_many_relationship
from .._times import value_error_reason
from ..schemas import IDSchema
from ..schemas._base import (
    _SECRET_TYPES,
    _client_name,
    _json_data,
    get_writable_inputs,
    is_readonly_field,
    is_reference_field,
)
from ..schemas._generator import _required_init_params


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
    """Dump a value as JSON data on its way into a JSON column.

    A schema field typed as a nested model validates to a model instance, and
    one typed ``list[datetime]`` to datetimes. A JSON column binds through
    ``json.dumps``, which cannot take them. Restly owns the schema-to-model
    translation, so it does the dump here rather than letting the driver fail
    at flush with the value in the bind parameters.
    """
    if not _is_json_column(model_cls, field_name):
        return value
    return _json_data(value, f"{model_cls.__name__}.{field_name}")


def _column_ready(
    model_cls: type[Any],
    schema_cls: type[pydantic.BaseModel],
    field_name: str,
    value: Any,
) -> Any:
    """A value in the form of its column, by the rule of ``column_value``: a
    text column takes text, a time gets the form of its column, and a number
    or a text must fit it. The column stores the value, so text must also fit
    its length.

    A field without a column keeps its value, and so does ``None``.

    :raises RequestValidationError: the value does not fit the column, such as
        a time out of range in UTC, or a number out of the range of an
        ``Integer``. The error has the form of a field's validation error, as
        ``fr.UTCDatetime`` gives it.
    """
    if value is None:
        return value
    mapper = sa_inspect(model_cls, raiseerr=False)
    column = None if mapper is None else mapper.columns.get(field_name)
    if column is None:
        return value
    try:
        return column_value(column.type, value, stored=True)
    except ValueError as exc:
        field = schema_cls.model_fields.get(field_name)
        key = field_name if field is None else _client_name(field_name, field)
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("body", key),
                    "msg": value_error_reason(exc),
                    "input": value,
                    "ctx": {"error": exc},
                }
            ]
        ) from None


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

        value = _column_ready(model_cls, schema_cls, field_name, _revealed(value))
        value = _collection_ready(
            model_cls, field_name, _json_ready(model_cls, field_name, value)
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
        value = _column_ready(type(obj), schema_cls, field_name, _revealed(value))
        value = _json_ready(type(obj), field_name, value)
        setattr(obj, field_name, _collection_ready(type(obj), field_name, value))
