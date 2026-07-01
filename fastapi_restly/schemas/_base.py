import functools
import inspect
import types
from datetime import datetime
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Generic,
    Optional,
    Union,
    final,
    get_args,
    get_origin,
)

import pydantic
from pydantic.fields import Field, FieldInfo
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio.session import AsyncSession as SA_AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm.session import Session as SA_Session
from typing_extensions import TypeAliasType, TypeVar

from ..exc import NotFound, RestlyConfigurationError


class BaseSchema(pydantic.BaseModel):
    """Thin Pydantic base for ORM-facing Restly schemas.

    Equivalent to::

        class BaseSchema(pydantic.BaseModel):
            model_config = pydantic.ConfigDict(from_attributes=True)

    ``from_attributes=True`` lets Pydantic/FastAPI validate objects by
    attribute when the schema is used directly. Generated Restly routes still
    serialize through ``to_response_schema()`` so Restly-specific behavior such
    as ``WriteOnly`` filtering and relationship-id normalization is applied.
    """

    model_config = pydantic.ConfigDict(from_attributes=True)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        # Reject a ReadOnly/WriteOnly marker buried inside a union member, where
        # it silently no-ops (see ``_reject_buried_markers``). This fires as the
        # schema class is defined, so the mistake surfaces at import time. Views
        # also re-check the schemas they use, to cover schemas that do not derive
        # from ``BaseSchema``.
        _reject_buried_markers(cls)


class _Marker:
    def __init__(self, name: str):
        self.name = name

    def __repr__(self):
        return f"fr.{self.name}"


readonly_marker = _Marker("ReadOnly")
writeonly_marker = _Marker("WriteOnly")

_T = TypeVar("_T")

ReadOnly = Annotated[_T, readonly_marker, Field(json_schema_extra={"readOnly": True})]
# ``exclude=True`` strips the field from serialization at the field level, so it
# is dropped from every response -- recursively, including in nested schemas, and
# from the OpenAPI response schema -- while staying a writable request input
# (exclude does not affect validation). Prefer ``WriteOnly[Optional[T]]`` over
# ``Optional[WriteOnly[T]]`` / ``WriteOnly[T] | None``: when the marker is only a
# union member the exclude rides on the inner type and does NOT apply, so the
# field would leak.
WriteOnly = Annotated[
    _T,
    writeonly_marker,
    Field(json_schema_extra={"writeOnly": True}, exclude=True),
]


# A ReadOnly/WriteOnly marker only takes effect as the OUTER annotation of a
# field: its ``Annotated`` metadata has to sit at the field's top level. Nested
# anywhere inside the field's type -- a union member (``Optional[ReadOnly[T]]``,
# ``WriteOnly[T] | None``) or a container element (``list[WriteOnly[T]]``) -- the
# marker silently no-ops: ReadOnly fails to drop the field from create/update (it
# stays writable) and WriteOnly fails to exclude it from responses (it leaks).
# The guards below detect that misuse and reject it loudly.
#
# Coverage boundary: detection reads ``field_info.annotation``, so a marker
# hidden behind an unresolved forward reference is not seen until the annotation
# resolves (the view-registration backstop re-checks resolved schemas), and that
# backstop only inspects a view's own read/create/update schemas -- not a custom
# ``response_model=`` or a non-``BaseSchema`` nested model. Those narrow cases are
# left uncovered by design.
def _annotation_buries_marker(annotation: Any, marker: _Marker) -> bool:
    """True if ``marker`` appears anywhere below the top level of ``annotation``
    -- inside a union member, ``Annotated`` inner type, or container arg.

    Only descends type arguments (``get_args``); it does not recurse into the
    fields of a nested model, so a nested schema carrying its own top-level
    marker is not flagged.
    """
    if marker in getattr(annotation, "__metadata__", ()):
        return True
    return any(_annotation_buries_marker(arg, marker) for arg in get_args(annotation))


def _find_buried_marker_fields(
    model_cls: type[pydantic.BaseModel],
) -> list[tuple[str, _Marker]]:
    """Return ``(field_name, marker)`` for each field whose ReadOnly/WriteOnly
    marker is nested inside the field's type instead of wrapping it, where it
    does not take effect."""
    buried: list[tuple[str, _Marker]] = []
    for name, field_info in model_cls.model_fields.items():
        top_level = getattr(field_info, "metadata", None) or ()
        for marker in (readonly_marker, writeonly_marker):
            if marker in top_level:
                continue
            if _annotation_buries_marker(field_info.annotation, marker):
                buried.append((name, marker))
    return buried


def _reject_buried_markers(model_cls: type[pydantic.BaseModel]) -> None:
    """Raise if any field nests a ReadOnly/WriteOnly marker inside its type."""
    for name, marker in _find_buried_marker_fields(model_cls):
        raise RestlyConfigurationError(
            f"{model_cls.__name__}.{name} nests the {marker.name} marker inside "
            f"its type (e.g. Optional[{marker.name}[T]], {marker.name}[T] | None, "
            f"or list[{marker.name}[T]]). The marker only takes effect as the "
            f"outer annotation, so it is silently ignored here and {marker.name} "
            f"is not applied. Wrap the whole field type instead, e.g. "
            f"{marker.name}[Optional[T]]."
        )


class TimestampsSchemaMixin(pydantic.BaseModel):
    created_at: ReadOnly[datetime]
    updated_at: ReadOnly[datetime]


SQLAlchemyModel = TypeVar(
    "SQLAlchemyModel", bound=DeclarativeBase, default=DeclarativeBase
)
_IDREF_UNSET = object()
_SCHEMA_RESOURCE_SUFFIX = "Read"


@functools.cache
def _id_type_adapter(id_type: Any) -> pydantic.TypeAdapter[Any]:
    return pydantic.TypeAdapter(id_type)


def _schema_resource_name(model_cls: type[pydantic.BaseModel]) -> str:
    """Return the resource name used to derive role-specific API schemas."""
    name = model_cls.__name__
    if name.endswith(_SCHEMA_RESOURCE_SUFFIX) and len(name) > len(
        _SCHEMA_RESOURCE_SUFFIX
    ):
        return name[: -len(_SCHEMA_RESOURCE_SUFFIX)]
    return name


def _schema_role_name(model_cls: type[pydantic.BaseModel], role: str) -> str:
    return f"{_schema_resource_name(model_cls)}{role}"


def _model_id_type(sql_model: type[DeclarativeBase]) -> Any:
    """Return the Python type of ``sql_model``'s ``id`` primary key, or ``None``.

    Reads the ``id`` annotation off the model's MRO (works before the mapper is
    configured), then falls back to the SA mapper -- which also covers a PEP 563
    / ``from __future__ import annotations`` model, whose ``id`` annotation is a
    string the mapper resolves authoritatively. Shared by ``IDSchema``/``IDRef``
    (via ``_get_sql_model_id_type``) and ``MustExist``.
    """
    for model_cls in sql_model.mro():
        annotation = getattr(model_cls, "__annotations__", {}).get("id")
        if annotation is None:
            continue
        if isinstance(annotation, str):
            # PEP 563 stringized annotation -- don't return the raw string; the
            # mapper below resolves the real column type without eval'ing it.
            break
        origin = get_origin(annotation)
        if origin is not None:
            args = get_args(annotation)
            if args:
                return args[0]
        return annotation

    # Fallback: ask the SA mapper. `python_type` raises NotImplementedError for
    # column types without a Python equivalent (e.g. some user types), and
    # accessing `__mapper__` may fail with AttributeError if the class has not
    # been mapped yet.
    try:
        return sql_model.__mapper__.primary_key[0].type.python_type
    except (AttributeError, NotImplementedError, IndexError):
        return None


class IDSchema(BaseSchema, Generic[SQLAlchemyModel]):
    """Response-schema base that adds a read-only ``id``; parametrized as a
    field type, a nested-object relationship reference.

    - As a BASE CLASS (``class UserRead(IDSchema): ...``) it adds the resource's
      own read-only ``id`` -- the common use.
    - As a FIELD TYPE on a RELATIONSHIP-named field (``author: IDSchema[User]``)
      the wire format is ``{"id": N}`` (JSON-API / React-Admin); Restly resolves
      the id to the related ORM object, so ``data.author`` is a wrapper (read
      ``.id``). Use ``IDRef[User]`` for flat-id wire (``5``) instead.

    For a scalar foreign-key COLUMN (``author_id``), use ``fr.MustExist[int, T]``
    rather than ``IDSchema``/``IDRef``: it keeps the field a plain checked id
    instead of turning a ``*_id`` field into an object wrapper.
    """

    # Keep this broad so relation-id payloads can target non-int primary keys.
    id: ReadOnly[Any]

    @classmethod
    def _get_sql_model_annotation(cls) -> type[DeclarativeBase] | None:
        # `__pydantic_generic_metadata__` is set on parameterised subclasses;
        # on the bare `IDSchema` class the "args" tuple may be missing or empty.
        try:
            sql_model = cls.__pydantic_generic_metadata__["args"][0]
        except (KeyError, IndexError, TypeError):
            return None
        return sql_model if isinstance(sql_model, type) else None

    @classmethod
    def _get_sql_model_id_type(cls) -> Any:
        sql_model = cls._get_sql_model_annotation()
        if sql_model is None:
            return None
        return _model_id_type(sql_model)

    @pydantic.field_validator("id", mode="before", check_fields=False)
    @classmethod
    def _coerce_id_to_model_primary_key_type(cls, value: Any) -> Any:
        id_type = cls._get_sql_model_id_type()
        if id_type in (None, Any):
            return value
        return _id_type_adapter(id_type).validate_python(value)

    @pydantic.model_validator(mode="before")
    @classmethod
    def _coerce_scalar(cls, v: Any) -> Any:
        # A pure id reference (``IDSchema[Model]`` / ``IDRef[Model]``, whose only
        # field is ``id``) accepts a bare scalar id or a related ORM row where the
        # ``{"id": ...}`` mapping is expected, so the reference type is
        # self-sufficient under plain ``from_attributes``: a scalar FK column read
        # straight off the row in ``to_response_schema``, a related row reached
        # through a relationship, or a ``{"id": N}`` payload all validate without
        # any view-layer pre-extraction. A subclass that adds fields (a nested
        # response schema) is NOT a pure reference, so it validates normally and
        # its row is never collapsed to just the id.
        if set(cls.model_fields) != {"id"}:
            return v
        if isinstance(v, dict):
            return v
        if isinstance(v, DeclarativeBase):
            return {"id": getattr(v, "id")}
        return {"id": v}

    def get_sql_model_annotation(self) -> type[SQLAlchemyModel] | None:
        """
        Return the annotation on IDSchema when used as:

        foo: IDSchema[Foo]

        This property will return "Foo".
        """
        # The runtime introspection returns the bound type; cast through the
        # generic parameter so callers see the concrete model class.
        return self._get_sql_model_annotation()  # type: ignore[return-value]


class IDRef(IDSchema[SQLAlchemyModel], Generic[SQLAlchemyModel]):
    """Flat-id reference to a related row, for a RELATIONSHIP-named field.

    Use ``IDRef[T]`` when the field is named after a relationship
    (``author: IDRef[User]``, ``products: list[IDRef[Product]]``), not after a
    ``*_id`` column. The wire format is the raw id (``5``); input also accepts
    ``{"id": N}``. Restly resolves the id to the related ORM object, so
    ``data.author`` is an ``IDRef`` wrapper (read ``.id``), not a plain scalar.

    For a scalar foreign-key COLUMN (``author_id``, ``task_id``) use
    ``fr.MustExist[int, T]`` instead -- it keeps the field a plain checked id
    (``data.author_id == 1``), rather than making a ``*_id`` field an object
    wrapper. For nested ``{"id": N}`` wire on a relationship, use ``IDSchema[T]``.

        author: IDRef[User]             # to-one relationship, flat id
        products: list[IDRef[Product]]  # serializes as [1, 2, 3]

    Resolution is an UNSCOPED existence check: the row is fetched by primary key
    only, with no view ``build_query`` scoping (tenant, soft-delete, row-level
    visibility), so a reference to a row the caller cannot otherwise see still
    resolves. Gate visibility in ``authorize`` (``data.<field>.id`` is the
    requested id, before resolution) or ``before_commit`` (the resolved row is
    on the built object). See the Foreign Keys and Relationships how-to,
    "Visibility and Multi-Tenancy".
    """

    def __init__(self, value: Any = _IDREF_UNSET, **data: Any) -> None:
        if value is not _IDREF_UNSET:
            if data:
                raise TypeError(
                    "IDRef accepts either a positional id or keyword fields"
                )
            data = value if isinstance(value, dict) else {"id": value}
        super().__init__(**data)

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: Any, handler: pydantic.GetJsonSchemaHandler
    ) -> dict[str, Any]:
        id_type = cls._get_sql_model_id_type()
        if id_type in (None, Any):
            return {}
        return pydantic.TypeAdapter(id_type).json_schema(
            mode=getattr(handler, "mode", "validation")
        )

    @pydantic.model_serializer
    def _serialize_flat(self) -> Any:
        return self.id if hasattr(self, "id") else self


@final
class _Infer:
    """Static-only sentinel: the default ``MustExist`` model argument.

    ``MustExist[int]`` supplies no target model, so the second type parameter
    defaults to ``_Infer`` (a PEP 696 default that keeps Pyright happy about
    "supply all params"). It carries no runtime meaning of its own: one type
    argument is already unambiguous, and the runtime reads it as "resolve the
    target model from the column's ``ForeignKey``".
    """


class RefExists:
    """Marker for an existence-checked scalar foreign key.

    Built by ``MustExist[pk]`` / ``MustExist[pk, Model]`` (or written directly as
    ``Annotated[pk, RefExists(Model)]``). On write, Restly batch-checks that each
    marked id exists in the target table and raises ``NotFound`` (404) on a miss.
    The field stays a plain scalar -- this only adds the check; it does not wrap
    the value or do any relationship routing. The check is UNSCOPED (a bare
    primary-key lookup, no view ``build_query`` scoping), exactly like
    ``IDRef``/``IDSchema`` resolution.

    ``model`` is the target ORM model, or ``_Infer`` when it should be resolved
    from the marked column's ``ForeignKey`` (the ``MustExist[pk]`` form).
    """

    def __init__(self, model: type[DeclarativeBase] | type[_Infer]) -> None:
        self.model = model

    def __repr__(self) -> str:
        name = (
            "infer" if self.model is _Infer else getattr(self.model, "__name__", self.model)
        )
        return f"RefExists({name})"


if TYPE_CHECKING:
    _MustExistPK = TypeVar("_MustExistPK")
    _MustExistModel = TypeVar("_MustExistModel", default=_Infer)

    # Static view: ``MustExist[pk]`` and ``MustExist[pk, Model]`` both read as the
    # pk scalar (the alias value is the first param), so a field stays a plain
    # scalar -- ``data.post_id`` is an ``int``, not a wrapper. The target model is
    # a runtime-only concern (second param, defaulting to ``_Infer``); the runtime
    # ``MustExist`` is defined below.
    MustExist = TypeAliasType(
        "MustExist", _MustExistPK, type_params=(_MustExistPK, _MustExistModel)
    )
else:

    class MustExist:
        """Existence-checked scalar foreign key.

        ``MustExist[int]`` is a checked ``int`` foreign key; the target model is
        inferred from the column's ``ForeignKey``. Spell the model out with a
        second argument when you want it explicit (``MustExist[int, Post]``), and
        use the first for a non-int primary key (``MustExist[UUID, Account]``).

        Unlike ``IDRef``/``IDSchema`` this is **not** a wrapper: the field stays
        the pk scalar everywhere (wire, column, ``data.<field>``), plus a batched
        existence check on write (404 on a miss). It is exactly the marker form
        ``Annotated[<pk>, RefExists(Model)]`` -- use that directly if you prefer.
        """

        def __class_getitem__(cls, params: Any) -> Any:
            pk_type, model = (
                params if isinstance(params, tuple) else (params, _Infer)
            )
            return Annotated[pk_type, RefExists(model)]


def _unwrap_optional_annotation(annotation: Any) -> Any:
    """Unwrap Optional[X] or X | None to X. Returns annotation unchanged otherwise."""
    origin = get_origin(annotation)
    if origin not in (types.UnionType, Union):
        return annotation

    non_none_args = [arg for arg in get_args(annotation) if arg is not type(None)]
    if len(non_none_args) == 1:
        return non_none_args[0]
    return annotation


def is_reference_annotation(annotation: Any) -> bool:
    """True if ``annotation`` is an ``IDRef``/``IDSchema`` reference type.

    The canonical reference-type check, kept beside the types so callers (views,
    query) don't re-introspect Pydantic generics themselves. Unwraps ``Optional``
    first, then matches the bare types or a parametrization whose generic origin
    is ``IDSchema``/``IDRef`` (``IDRef[Post]``, ``IDSchema[Post]``) -- not a user
    subclass that merely inherits from them.
    """
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


def is_reference_field(schema_cls: type[pydantic.BaseModel], field_name: str) -> bool:
    """True if ``schema_cls``'s ``field_name`` is typed as an ``IDRef``/``IDSchema``."""
    field_info = schema_cls.model_fields.get(field_name)
    if field_info is None:
        return False
    return is_reference_annotation(field_info.annotation)


def reference_origin_and_target(annotation: Any) -> tuple[type, type | None] | None:
    """For an ``IDRef``/``IDSchema`` field annotation (``Optional`` unwrapped),
    return ``(origin, target)``: the ``IDRef``/``IDSchema`` class and the
    referenced model (``None`` for a bare, unparametrized reference), or ``None``
    when the annotation is not a reference type. Lets the scalar-named-reference
    lint name both the type and its target in the ``MustExist`` hint.
    """
    annotation = _unwrap_optional_annotation(annotation)
    if annotation in (IDSchema, IDRef):
        return (annotation, None)
    if not inspect.isclass(annotation):
        return None
    try:
        if not issubclass(annotation, IDSchema):
            return None
    except TypeError:
        return None
    metadata = getattr(annotation, "__pydantic_generic_metadata__", {})
    origin = metadata.get("origin")
    if origin not in (IDSchema, IDRef):
        return None
    args = metadata.get("args") or ()
    target = args[0] if args and isinstance(args[0], type) else None
    return (origin, target)


async def _async_resolve_ids_to_sqlalchemy_objects(
    session: SA_AsyncSession, schema_obj: pydantic.BaseModel
) -> dict[str, Any]:
    """
    Resolve any IDSchema reference fields on ``schema_obj`` to SQLAlchemy rows.
    A database request is made for each IDSchema to look up the related row in the database.
    If an id is not found in the database `sqlalchemy.orm.exc.NoResultFound` is raised.

    Returns a ``{field_name: resolved_object_or_list}`` mapping for the fields
    that referenced a model; ``schema_obj`` itself is left unmodified, so it
    keeps its validated wire shape (``IDRef[T]`` values, not ORM rows). The
    write path consumes the returned mapping.

    This is an UNSCOPED existence check: the lookup is a bare primary-key fetch
    with no view ``build_query`` scoping. Tenant / row-level visibility of
    references is the caller's responsibility (gate in ``authorize`` /
    ``before_commit``); see the ``IDRef`` docstring.
    """
    # Go over all Pydantic fields and check if any of them are an IDSchema object or
    # a list of IDSchema objects.
    resolved: dict[str, Any] = {}
    for field in schema_obj.model_fields_set:
        value = getattr(schema_obj, field, None)

        if isinstance(value, IDSchema):
            sql_model = value.get_sql_model_annotation()
            if not sql_model:
                continue

            try:
                sql_model_obj = await session.get_one(sql_model, value.id)
            except NoResultFound as e:
                raise NotFound(f"Id not found for {field}: {value.id}") from e
            resolved[field] = sql_model_obj

        elif isinstance(value, list) and any(isinstance(i, IDSchema) for i in value):
            # Assume all IdSchemas are for the same model
            sql_model = value[0].get_sql_model_annotation()
            if not sql_model:
                continue

            # Resolve via an id -> row map: keeps the client's order (first
            # appearance, deduped) and makes a missing id one absent from the map,
            # so a repeated id can't spuriously 404 (``IN`` dedups and reorders).
            ids = [obj.id for obj in value]
            unique_ids = list(dict.fromkeys(ids))
            query = select(sql_model).where(sql_model.id.in_(unique_ids))
            by_id = {o.id: o for o in await session.scalars(query)}

            missing = [i for i in unique_ids if i not in by_id]
            if missing:
                raise NotFound(f"Id not found for {field}: {missing}")

            resolved[field] = [by_id[i] for i in unique_ids]

    return resolved


def _resolve_ids_to_sqlalchemy_objects(
    session: SA_Session, schema_obj: pydantic.BaseModel
) -> dict[str, Any]:
    """
    Resolve any IDSchema reference fields on ``schema_obj`` to SQLAlchemy rows.
    A database request is made for each IDSchema to look up the related row in the database.
    If an id is not found in the database `sqlalchemy.orm.exc.NoResultFound` is raised.

    Returns a ``{field_name: resolved_object_or_list}`` mapping for the fields
    that referenced a model; ``schema_obj`` itself is left unmodified, so it
    keeps its validated wire shape (``IDRef[T]`` values, not ORM rows). The
    write path consumes the returned mapping.

    This is an UNSCOPED existence check: the lookup is a bare primary-key fetch
    with no view ``build_query`` scoping. Tenant / row-level visibility of
    references is the caller's responsibility (gate in ``authorize`` /
    ``before_commit``); see the ``IDRef`` docstring.
    """
    # Go over all Pydantic fields and check if any of them are an IDSchema object or
    # a list of IDSchema objects.
    resolved: dict[str, Any] = {}
    for field in schema_obj.model_fields_set:
        value = getattr(schema_obj, field, None)

        if isinstance(value, IDSchema):
            sql_model = value.get_sql_model_annotation()
            if not sql_model:
                continue

            try:
                sql_model_obj = session.get_one(sql_model, value.id)
            except NoResultFound as e:
                raise NotFound(f"Id not found for {field}: {value.id}") from e
            resolved[field] = sql_model_obj

        elif isinstance(value, list) and any(isinstance(i, IDSchema) for i in value):
            # Assume all IdSchemas are for the same model
            sql_model = value[0].get_sql_model_annotation()
            if not sql_model:
                continue

            # Resolve via an id -> row map: keeps the client's order (first
            # appearance, deduped) and makes a missing id one absent from the map,
            # so a repeated id can't spuriously 404 (``IN`` dedups and reorders).
            ids = [obj.id for obj in value]
            unique_ids = list(dict.fromkeys(ids))
            query = select(sql_model).where(sql_model.id.in_(unique_ids))
            by_id = {o.id: o for o in session.scalars(query)}

            missing = [i for i in unique_ids if i not in by_id]
            if missing:
                raise NotFound(f"Id not found for {field}: {missing}")

            resolved[field] = [by_id[i] for i in unique_ids]

    return resolved


def _ref_exists_marker(field_info: FieldInfo) -> RefExists | None:
    """Return the ``RefExists`` marker on a field, or ``None``.

    Found at the field's top level (``MustExist[pk, M]`` / ``Annotated[pk,
    RefExists(M)]``), or recovered from the union member for an optional field
    (``MustExist[pk, M] | None``), where Pydantic keeps the marker on the inner
    annotation rather than ``field_info.metadata`` -- so an optional checked FK
    is checked like the plain form (parity with ``Optional[IDRef]``).
    """
    for m in field_info.metadata:
        if isinstance(m, RefExists):
            return m
    unwrapped = _unwrap_optional_annotation(field_info.annotation)
    for m in getattr(unwrapped, "__metadata__", ()):
        if isinstance(m, RefExists):
            return m
    return None


def _infer_ref_model(
    model_cls: type[DeclarativeBase], field_name: str
) -> type[DeclarativeBase]:
    """Resolve the target model for a ``MustExist[pk]`` field from its FK column.

    ``MustExist[pk]`` (one argument) leaves the model to be inferred: the column
    named by the field must carry exactly one ``ForeignKey``, whose table maps to
    the target model. Raises ``RestlyConfigurationError`` when the field does not
    map to a single-FK column -- name the model explicitly (``MustExist[pk,
    Model]``) there.
    """
    column = model_cls.__mapper__.columns.get(field_name)
    foreign_keys = list(column.foreign_keys) if column is not None else []
    if len(foreign_keys) == 1:
        target_table = foreign_keys[0].column.table
        for mapper in model_cls.registry.mappers:
            if mapper.local_table is target_table:
                return mapper.class_
        raise RestlyConfigurationError(
            f"{model_cls.__name__}.{field_name}: MustExist[...] with one argument "
            f"infers the target model from the column's ForeignKey, but its FK "
            f"target table '{target_table.name}' has no mapped model. "
            f"Name it explicitly: MustExist[<pk>, <Model>]."
        )
    raise RestlyConfigurationError(
        f"{model_cls.__name__}.{field_name}: MustExist[...] with one argument "
        f"infers the target model from the column's ForeignKey, but "
        f"'{field_name}' does not map to a column with exactly one foreign key. "
        f"Name it explicitly: MustExist[<pk>, <Model>]."
    )


def _ref_exists_fields(
    model_cls: type[DeclarativeBase],
    schema_obj: pydantic.BaseModel,
) -> dict[type[DeclarativeBase], list[tuple[str, Any]]]:
    """Group provided, non-``None`` ``RefExists``-marked scalar fields by target model.

    Returns ``{model: [(field_name, id), ...]}`` for fields whose annotation
    carries a ``RefExists`` marker (``MustExist[pk]`` / ``MustExist[pk, Model]``,
    or an explicit ``Annotated[pk, RefExists(Model)]``). A marker left to infer
    (``MustExist[pk]``) is resolved to the model behind the column's ``ForeignKey``
    on ``model_cls``. Only fields actually supplied (``model_fields_set``) with a
    non-``None`` value are included.
    """
    by_model: dict[type[DeclarativeBase], list[tuple[str, Any]]] = {}
    model_fields = type(schema_obj).model_fields
    for field_name in schema_obj.model_fields_set:
        field_info = model_fields.get(field_name)
        if field_info is None:
            continue
        marker = _ref_exists_marker(field_info)
        if marker is None:
            continue
        value = getattr(schema_obj, field_name, None)
        if value is None:
            continue
        model = marker.model
        if model is _Infer:
            model = _infer_ref_model(model_cls, field_name)
        by_model.setdefault(model, []).append((field_name, value))
    return by_model


def _raise_for_missing_refs(items: list[tuple[str, Any]], found: set[Any]) -> None:
    for field_name, value in items:
        if value not in found:
            raise NotFound(f"Id not found for {field_name}: {value}")


def _check_ref_exists(
    session: SA_Session,
    model_cls: type[DeclarativeBase],
    schema_obj: pydantic.BaseModel,
) -> None:
    """Batch-validate that every ``RefExists``-marked id exists (sync).

    One ``SELECT pk WHERE pk IN (...)`` per referenced model (no N+1); raises
    ``NotFound`` (404) naming the field and id on a miss. UNSCOPED -- a bare
    primary-key lookup, like ``IDRef``/``IDSchema`` resolution. ``model_cls`` is
    the model being written, used to infer the target of a ``MustExist[pk]`` field.
    """
    for model, items in _ref_exists_fields(model_cls, schema_obj).items():
        unique = list(dict.fromkeys(value for _, value in items))
        pk_col = model.__mapper__.primary_key[0]
        found = set(session.scalars(select(pk_col).where(pk_col.in_(unique))))
        _raise_for_missing_refs(items, found)


async def _async_check_ref_exists(
    session: SA_AsyncSession,
    model_cls: type[DeclarativeBase],
    schema_obj: pydantic.BaseModel,
) -> None:
    """Async twin of :func:`_check_ref_exists`."""
    for model, items in _ref_exists_fields(model_cls, schema_obj).items():
        unique = list(dict.fromkeys(value for _, value in items))
        pk_col = model.__mapper__.primary_key[0]
        found = set(await session.scalars(select(pk_col).where(pk_col.in_(unique))))
        _raise_for_missing_refs(items, found)


def get_read_only_fields(model_cls: type[pydantic.BaseModel]) -> list[str]:
    """Get all fields from a model annotated as ReadOnly[]"""
    read_only_fields: list[str] = []
    # Get read-only fields from Annotated metadata
    for field_name, field_info in model_cls.model_fields.items():
        metadata = getattr(field_info, "metadata", None)
        if metadata and readonly_marker in metadata:
            read_only_fields.append(field_name)
    return read_only_fields


def is_readonly_field(
    model: pydantic.BaseModel | type[pydantic.BaseModel], field_name: str
) -> bool:
    """Check if a specific field is marked as readonly."""
    if isinstance(model, pydantic.BaseModel):
        model = model.__class__
    field_info = model.model_fields.get(field_name)
    return _is_readonly(field_info)


def _is_readonly(field_info: FieldInfo | None) -> bool:
    if field_info is None:
        return False
    metadata = getattr(field_info, "metadata", None)
    if not metadata:
        return False
    return readonly_marker in metadata


def _is_writeonly(field_info: FieldInfo | None) -> bool:
    if field_info is None:
        return False
    metadata = getattr(field_info, "metadata", None)
    if not metadata:
        return False
    return writeonly_marker in metadata


def get_write_only_fields(model_cls: type[pydantic.BaseModel]) -> list[str]:
    """Get all fields from a model annotated as WriteOnly[]"""
    write_only_fields: list[str] = []
    # Get write-only fields from Annotated metadata
    for field_name, field_info in model_cls.model_fields.items():
        if _is_writeonly(field_info):
            write_only_fields.append(field_name)
    return write_only_fields


def is_writeonly_field(
    model_cls: pydantic.BaseModel | type[pydantic.BaseModel], field_name: str
) -> bool:
    """Check if a specific field is marked as writeonly."""
    if isinstance(model_cls, pydantic.BaseModel):
        model_cls = model_cls.__class__
    field_info = model_cls.model_fields.get(field_name)
    return _is_writeonly(field_info)


def create_model_without_read_only_fields(
    model_cls: type[pydantic.BaseModel],
) -> type[pydantic.BaseModel]:
    """
    Create a subclass of the given pydantic model class with a new name.
    """
    new_model_name = _schema_role_name(model_cls, "Create")
    new_doc = (model_cls.__doc__ or "") + "\nRead-only fields have been removed."

    # Create a subclass that mixes in OmitReadOnlyMixin
    new_model_cls = type(
        new_model_name,
        (OmitReadOnlyMixin, model_cls),
        {"__module__": model_cls.__module__, "__doc__": new_doc},
    )

    return new_model_cls


class OmitReadOnlyMixin(pydantic.BaseModel):
    """
    Mixin for pydantic models that removes all fields marked as ReadOnly.

    Implementation note: this mutates ``cls.model_fields`` in place and then
    calls ``model_rebuild(force=True)`` to regenerate the validator/serializer.
    Pydantic v2 does not officially document mutation of ``model_fields`` as
    a supported customisation hook, but this approach has been stable since
    pydantic 2.0 and works on the pinned minimum (``pydantic>=2.11.0``). If
    a future pydantic release freezes the dict, switch to constructing a new
    model via ``pydantic.create_model(...)`` over the kept fields. The
    regression test ``tests/test_pydantic_model_fields_mutation.py`` exercises
    this contract on the currently-installed pydantic.
    """

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)

        # Collect readonly fields to delete first
        readonly_fields = []
        for name, field_info in cls.model_fields.items():
            if _is_readonly(field_info):
                readonly_fields.append(name)

        # Delete readonly fields after iteration is complete
        for name in readonly_fields:
            del cls.model_fields[name]

        cls.model_rebuild(force=True)


def rebase_with_model_config(
    base: tuple[type, ...], model_cls: type[pydantic.BaseModel]
) -> type[pydantic.BaseModel]:
    def class_body(ns: dict[str, Any]) -> None:
        ns["model_config"] = model_cls.model_config.copy()

    return types.new_class(
        f"{model_cls.__name__}ModelConfig", base, exec_body=class_body
    )


def create_model_with_optional_fields(
    model_cls: type[pydantic.BaseModel],
) -> type[pydantic.BaseModel]:
    """
    Create a subclass of the given pydantic model class with a new name.
    Read-only fields are removed and all writable fields are made optional with None as default.
    """
    new_model_name = _schema_role_name(model_cls, "Update")
    new_doc = (
        model_cls.__doc__ or ""
    ) + "\nRead-only fields have been removed and all fields are optional."

    # Create a subclass that mixes in both OmitReadOnlyMixin and PatchMixin
    new_model_cls = type(
        new_model_name,
        (PatchMixin, OmitReadOnlyMixin, model_cls),
        {"__module__": model_cls.__module__, "__doc__": new_doc},
    )

    return new_model_cls


class PatchMixin(pydantic.BaseModel):
    """
    A mixin for pydantic classes that makes all fields optional and replaces defaults
    with None.

    Implementation note: like :class:`OmitReadOnlyMixin` this mutates
    ``cls.model_fields`` (specifically ``FieldInfo.default`` and
    ``FieldInfo.annotation``) and then calls ``model_rebuild(force=True)``.
    This relies on pydantic v2 keeping ``FieldInfo`` mutable; verified on the
    pinned ``pydantic>=2.11.0`` minimum and exercised by
    ``tests/test_pydantic_model_fields_mutation.py``.
    """

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)

        for field in cls.model_fields.values():
            field.default = None
            # Only wrap if not already Optional, to avoid Optional[Optional[T]]
            annotation = field.annotation
            if isinstance(annotation, types.UnionType):
                # Python 3.10+ `X | Y` syntax - check if None is already a member.
                # Convert to typing.Optional form so FieldInfo.annotation stays compatible.
                union_args = get_args(annotation)
                if type(None) not in union_args:
                    non_none = [a for a in union_args if a is not type(None)]
                    inner = (
                        non_none[0] if len(non_none) == 1 else Union[tuple(non_none)]
                    )
                    field.annotation = Optional[inner]  # type: ignore[assignment]
            else:
                origin = getattr(annotation, "__origin__", None)
                if origin is not Union or type(None) not in get_args(annotation):
                    field.annotation = Optional[annotation]  # type: ignore[assignment]

        cls.model_rebuild(force=True)


def getattrs(obj: Any, *attrs: str, default: Any = None) -> Any:
    """
    Try access a chain of attributes and return the default if any of the attrs is not defined.
    """
    for attr in attrs:
        if not hasattr(obj, attr):
            return default
        obj = getattr(obj, attr)
    return obj


def set_schema_title(schema_cls: type[pydantic.BaseModel]) -> None:
    """Set the title of a schema class to its name.
    This is used to make the schema title match the model name in the OpenAPI schema.
    """
    schema_cls.model_config["title"] = schema_cls.__name__


def get_writable_inputs(
    schema_obj: pydantic.BaseModel, schema_cls: type[pydantic.BaseModel] | None = None
) -> dict[str, Any]:
    """
    Return a dictionary of field_name: value pairs for writable input fields.

    Filters out:
    - ReadOnly fields
    - fields not provided with input (using Pydantic model_fields_set)

    Args:
        schema_obj: The schema object to extract writable fields from
        schema_cls: The schema class to check for readonly fields. If None, uses schema_obj.__class__

    Returns:
        Dictionary mapping field names to their values for writable input fields only
    """
    if schema_cls is None:
        schema_cls = schema_obj.__class__

    updated_fields: dict[str, Any] = {}
    for field_name, value in schema_obj:
        if field_name not in schema_obj.model_fields_set:
            continue
        # Skip readonly fields
        if is_readonly_field(schema_cls, field_name):
            continue
        updated_fields[field_name] = value

    return updated_fields
