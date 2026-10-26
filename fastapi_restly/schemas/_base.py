import contextlib
import dataclasses
import functools
import inspect
import sys
import types
import typing
import weakref
from collections.abc import Iterable, Iterator, Mapping
from collections.abc import Sequence as _AbcSequence
from collections.abc import Set as _AbcSet
from datetime import datetime
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    ForwardRef,
    Generic,
    Literal,
    Union,
    cast,
    get_args,
    get_origin,
)

import pydantic
from pydantic.fields import Field, FieldInfo
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio.session import AsyncSession as SA_AsyncSession
from sqlalchemy.orm.session import Session as SA_Session
from typing_extensions import TypeAliasType, TypeVar

from .._mapping import is_mapped_instance, is_to_many_relationship
from .._pagination import (
    _DEFAULT_PAGINATION,
    Envelope,
    NoPagination,
    NumberedPagination,
)
from ..clauses import apply_clauses
from ..clauses._scopes import _default_scope
from ..exc import NotFound, RestlyConfigurationError

if sys.version_info >= (3, 14):
    import annotationlib


class BaseSchema(pydantic.BaseModel):
    """The base of a Restly schema: a Pydantic model with
    ``from_attributes=True``, so it can read a SQLAlchemy object by its
    attributes.

    It also checks where the ``ReadOnly``, ``WriteOnly`` and ``MustExist``
    markers are placed. A marker deep inside the type of a field, such as
    ``ReadOnly[int] | None``, would have no effect. So the class raises
    :class:`~fastapi_restly.exc.RestlyConfigurationError` when Python defines
    it. Put the marker around the whole type: ``ReadOnly[int | None]``.
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
    _T, writeonly_marker, Field(json_schema_extra={"writeOnly": True}, exclude=True)
]


#: The classes of a type alias. ``type X = ...`` makes a
#: ``typing.TypeAliasType``, and typing_extensions has a class of its own on
#: some Python versions.
_TYPE_ALIAS_TYPES: tuple[type, ...] = tuple(
    {TypeAliasType, getattr(typing, "TypeAliasType", TypeAliasType)}
)


def _alias_value(annotation: Any, seen: set[Any] | None = None) -> Any:
    """The type that a type alias stands for: ``int`` for
    ``type UserId = int``, and ``list[int]`` for ``Many[int]`` with
    ``type Many[T] = list[T]``. Any other annotation comes back unchanged.

    ``seen`` collects the aliases that a check has expanded, each with its
    arguments. An alias that comes back inside its own value, as in
    ``type Json = list[Json] | str``, is not expanded again, so the check
    ends. A check that follows a type into its arguments passes its ``seen``
    on.
    """
    if seen is None:
        seen = set()
    while True:
        origin = get_origin(annotation)
        if type(origin) in _TYPE_ALIAS_TYPES:
            alias, args = origin, get_args(annotation)
        elif type(annotation) in _TYPE_ALIAS_TYPES:
            alias, args = annotation, ()
        else:
            return annotation
        key = _seen_key(alias, args)
        if key in seen:
            return annotation
        seen.add(key)
        try:
            annotation = _alias_body(alias, args)
        except NameError:
            # A type statement is lazy, and may name a class that Python has
            # not created yet. The view checks the schema again when it
            # registers.
            return annotation


def _seen_key(alias: Any, args: tuple[Any, ...]) -> Any:
    """``alias`` with its ``args``, as a key for the ``seen`` of
    :func:`_alias_value`. An argument that cannot be hashed counts by its
    identity."""
    try:
        hash(args)
    except TypeError:
        return (alias, tuple(id(arg) for arg in args))
    return (alias, args)


def _alias_body(alias: Any, args: tuple[Any, ...]) -> Any:
    """The value of ``alias``, with ``args`` in place of its type parameters.
    A parameter without an argument takes its default, if it has one."""
    value = alias.__value__
    replacements = dict(zip(alias.__type_params__, args))
    for param in alias.__type_params__[len(args) :]:
        if getattr(param, "has_default", lambda: False)():
            replacements[param] = param.__default__
    if not replacements:
        return value
    if isinstance(value, typing.TypeVar):
        return replacements.get(value, value)
    # A generic Pydantic model keeps its parameters in its own metadata.
    params = getattr(value, "__parameters__", ()) or getattr(
        value, "__pydantic_generic_metadata__", {}
    ).get("parameters", ())
    if not params:
        return value
    try:
        return value[tuple(replacements.get(p, p) for p in params)]
    except TypeError:
        return value


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
def _annotation_buries_marker(
    annotation: Any, marker: _Marker, seen: set[Any] | None = None
) -> bool:
    """True if ``marker`` appears anywhere below the top level of ``annotation``
    -- inside a union member, ``Annotated`` inner type, container arg, or type
    alias. Pydantic does not apply the field settings of an ``Annotated``
    inside a type alias either.

    Only descends type arguments (``get_args``); it does not recurse into the
    fields of a nested model, so a nested schema carrying its own top-level
    marker is not flagged.
    """
    seen = set() if seen is None else set(seen)
    annotation = _alias_value(annotation, seen)
    if marker in getattr(annotation, "__metadata__", ()):
        return True
    return any(
        _annotation_buries_marker(arg, marker, seen) for arg in get_args(annotation)
    )


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
    """Raise if any field nests a ReadOnly/WriteOnly/RefExists marker inside
    its type, where the marker machinery cannot see it."""
    for name, marker in _find_buried_marker_fields(model_cls):
        raise RestlyConfigurationError(
            f"{model_cls.__name__}.{name} nests the {marker.name} marker inside "
            f"its type, such as Optional[{marker.name}[T]], "
            f"{marker.name}[T] | None, list[{marker.name}[T]] or a type alias. "
            f"The marker only takes effect as the outer annotation, so it is "
            f"silently ignored here and {marker.name} is not applied. Wrap the "
            f"whole field type instead, such as {marker.name}[Optional[T]], or "
            f"move the marker out of the type alias."
        )
    _reject_buried_ref_exists(model_cls)


#: The message for code that still uses the removed public ``RefExists``.
_REF_EXISTS_REMOVED = (
    "fr.RefExists was removed. Declare a reference as "
    "fr.MustExist[<id type>, <Model>], such as fr.MustExist[int, Post]. It "
    "finds the rows of the model's default_scope, under the rules of the "
    "session. A tenant rule goes on the session. A rule for one caller or one "
    "action goes in authorize."
)


class RefExists:
    """Marker for an existence-checked scalar foreign key.

    Built by ``MustExist[pk, Model]``. On write, Restly checks that each
    marked id is a row of ``model`` and raises ``NotFound`` (404) on a miss.
    The field stays a plain scalar: the marker only adds the check.

    The check finds the rows of the model's ``default_scope``, under the
    rules of the session, as ``IDRef`` and ``IDSchema`` do. A model without a
    ``default_scope`` is checked by primary key alone.
    """

    def __init__(self, model: type[Any]) -> None:
        self.model = model

    def __repr__(self) -> str:
        return f"RefExists({getattr(self.model, '__name__', self.model)})"


def _annotation_contains_ref_exists(
    annotation: Any, seen: set[Any] | None = None
) -> bool:
    seen = set() if seen is None else set(seen)
    annotation = _alias_value(annotation, seen)
    if any(isinstance(m, RefExists) for m in getattr(annotation, "__metadata__", ())):
        return True
    return any(
        _annotation_contains_ref_exists(arg, seen) for arg in get_args(annotation)
    )


def _reject_buried_ref_exists(model_cls: type[pydantic.BaseModel]) -> None:
    """Raise for a RefExists marker the check machinery cannot see.

    The existence check reads the field's type, or the item type of a list
    (``list[MustExist[int, T]]``), each also with ``| None``. A marker deeper
    in the type, such as in ``dict[str, MustExist[int, T]]``, is invisible
    there, so the field would validate and write with no check at all -- a
    silent no-op of a check the schema explicitly asks for.
    """
    for name, field_info in model_cls.model_fields.items():
        if not _annotation_contains_ref_exists(field_info.annotation):
            continue
        if _ref_exists_marker(field_info) is not None:
            continue
        if _listed_ref_exists_marker(field_info) is not None:
            continue
        raise RestlyConfigurationError(
            f"{model_cls.__name__}.{name} buries a MustExist marker "
            f"inside its type, such as in dict[str, MustExist[int, T]], where "
            f"the existence check cannot see it, so the reference would go "
            f"unchecked. Use MustExist as the type of the field, or as the "
            f"item type of a list: list[MustExist[int, T]]."
        )


class TimestampsSchemaMixin(pydantic.BaseModel):
    """Schema mixin that adds two fields: ``created_at`` and ``updated_at``.

    It is the schema side of :class:`~fastapi_restly.models.TimestampsMixin`.
    Both fields are ``ReadOnly[datetime]``, so the responses contain them, and
    the create and update bodies that Restly generates leave them out. When a
    client sends them in a body, Restly ignores them. The fields have no
    default, so the object that a response reads must have both attributes.

    The mixin adds nothing else, so combine it with :class:`IDSchema` or
    :class:`BaseSchema`. Put it before the base class, so that ``id`` stays
    the first field in the responses::

        class UserSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
            name: str

    A schema that Restly generates for a model with ``created_at`` and
    ``updated_at`` columns uses this mixin.
    """

    #: When the row was created. It is ``ReadOnly``: the responses show it,
    #: and the create and update bodies that Restly generates leave it out.
    created_at: ReadOnly[datetime]
    #: When the row last changed. It is ``ReadOnly``: the responses show it,
    #: and the create and update bodies that Restly generates leave it out.
    updated_at: ReadOnly[datetime]


# Unbound: a mapped class need not subclass DeclarativeBase.
SQLAlchemyModel = TypeVar("SQLAlchemyModel", default=Any)
_IDREF_UNSET = object()
_SCHEMA_RESOURCE_SUFFIXES = ("Schema", "Response")


@functools.cache
def _id_type_adapter(id_type: Any) -> pydantic.TypeAdapter[Any]:
    return pydantic.TypeAdapter(id_type)


def _schema_resource_name(model_cls: type[pydantic.BaseModel]) -> str:
    """The resource part of a schema's class name: the name without a final
    ``Schema`` or ``Response``. ``UserSchema`` and ``UserResponse`` both give
    ``User``."""
    name = model_cls.__name__
    for suffix in _SCHEMA_RESOURCE_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def _schema_role_name(model_cls: type[pydantic.BaseModel], role: str) -> str:
    """The name of a class that Restly generates from a schema:
    ``<Resource><Role>``, as in ``UserCreate``."""
    return f"{_schema_resource_name(model_cls)}{role}"


def _own_annotations(cls: type) -> dict[str, Any]:
    """Return the annotations ``cls`` itself declares, without failing on them.

    Under PEP 563 the values are strings. On 3.14 a name that is undefined at
    runtime, such as a ``TYPE_CHECKING`` import, comes back as a ``ForwardRef``
    where reading ``__annotations__`` would raise ``NameError``.
    """
    if sys.version_info >= (3, 14):
        return annotationlib.get_annotations(
            cls, format=annotationlib.Format.FORWARDREF
        )
    return inspect.get_annotations(cls)


def _is_unresolved(annotation: Any) -> bool:
    """Whether ``annotation`` still holds a string or ``ForwardRef`` anywhere."""
    if isinstance(annotation, (str, ForwardRef)):
        return True
    origin = get_origin(annotation)
    if origin is Literal:
        return False
    args = get_args(annotation)
    if origin is Annotated:
        # Metadata may be any object, strings included.
        args = args[:1]
    return any(_is_unresolved(arg) for arg in args)


def _model_id_type(sql_model: type[Any]) -> Any:
    """Return the Python type of ``sql_model``'s ``id`` primary key, or ``None``.

    Reads the ``id`` annotation off the model's MRO (works before the mapper is
    configured), then falls back to the SA mapper -- which also covers a PEP 563
    / ``from __future__ import annotations`` model, whose ``id`` annotation is a
    string the mapper resolves authoritatively. Shared by ``IDSchema``/``IDRef``
    (via ``_get_sql_model_id_type``) and ``MustExist``.
    """
    for model_cls in sql_model.mro():
        annotation = _own_annotations(model_cls).get("id")
        if annotation is None:
            continue
        if get_origin(annotation) is not None:
            args = get_args(annotation)
            if args:
                annotation = args[0]
        if _is_unresolved(annotation):
            # Don't return a raw string; the mapper below resolves the real
            # column type without eval'ing it.
            break
        return annotation

    # Fallback: ask the SA mapper. `python_type` raises NotImplementedError for
    # column types without a Python equivalent (e.g. some user types), and
    # a class that is not mapped has no mapper to ask.
    try:
        return sa_inspect(sql_model, raiseerr=False).primary_key[0].type.python_type
    except (AttributeError, NotImplementedError, IndexError):
        return None


class IDSchema(BaseSchema, Generic[SQLAlchemyModel]):
    """The base of a view's schema, which adds a read-only ``id``. As the
    type of a field, ``IDSchema[Model]`` is a reference to a related row.

    - As a BASE CLASS (``class UserSchema(IDSchema): ...``) it adds the resource's
      own read-only ``id`` -- the common use.
    - As a FIELD TYPE on a RELATIONSHIP-named field (``author: IDSchema[User]``)
      the JSON value is ``{"id": N}`` (JSON-API / React-Admin); Restly resolves
      the id to the related ORM object, so ``data.author`` is a wrapper (read
      ``.id``). Use ``IDRef[User]`` for a flat id (``5``) instead.

    For a scalar foreign-key COLUMN (``author_id``), use ``fr.MustExist[int, T]``
    rather than ``IDSchema``/``IDRef``: it keeps the field a plain checked id
    instead of turning a ``*_id`` field into an object wrapper.
    """

    # Keep this broad so relation-id payloads can target non-int primary keys.
    #: The id of the row. In a view's schema, it is ``ReadOnly``: the
    #: responses show it, and the create and update bodies that Restly
    #: generates leave it out. In a reference field, such as
    #: ``author: fr.IDSchema[Author]``, the client sends it.
    id: ReadOnly[Any]

    @classmethod
    def _get_sql_model_annotation(cls) -> type[Any] | None:
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

    @pydantic.field_validator("id", mode="before")
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
        # straight off the row in ``to_single_response``, a related row reached
        # through a relationship, or a ``{"id": N}`` payload all validate without
        # any view-layer pre-extraction. A subclass that adds fields (a nested
        # response schema) is NOT a pure reference, so it validates normally and
        # its row is never collapsed to just the id.
        if set(cls.model_fields) != {"id"}:
            return v
        if isinstance(v, dict):
            return v
        if is_mapped_instance(v):
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
    ``*_id`` column. The JSON value is the raw id (``5``); input also accepts
    ``{"id": N}``. Restly resolves the id to the related ORM object, so
    ``data.author`` is an ``IDRef`` wrapper (read ``.id``), not a plain scalar.

    For a scalar foreign-key COLUMN (``author_id``, ``task_id``) use
    ``fr.MustExist[int, T]`` instead -- it keeps the field a plain checked id
    (``data.author_id == 1``), rather than making a ``*_id`` field an object
    wrapper. For a nested ``{"id": N}`` value on a relationship, use ``IDSchema[T]``.

        author: IDRef[User]             # to-one relationship, flat id
        products: list[IDRef[Product]]  # serializes as [1, 2, 3]

    Resolution applies the referenced model's ``default_scope``: a
    reference to a row the scope hides raises ``NotFound``, as a missing row
    does. A model without a ``default_scope`` resolves by primary key alone.
    A session rule, such as a tenant rule added with
    ``with_loader_criteria``, reaches the lookup too, except for an object
    that is already in the session. A rule that is neither goes in
    ``authorize``, where ``data.<field>.id`` is the requested id. See "Which
    rows can a reference reach?" on the Relationships guide page.
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


if TYPE_CHECKING:
    _MustExistPK = TypeVar("_MustExistPK")
    _MustExistModel = TypeVar("_MustExistModel")

    # Static view: ``MustExist[pk, Model]`` reads as the pk scalar (the alias
    # value is the first param), so a field stays a plain scalar --
    # ``data.post_id`` is an ``int``, not a wrapper. The model is a runtime
    # concern; the runtime ``MustExist`` is defined below.
    MustExist = TypeAliasType(
        "MustExist", _MustExistPK, type_params=(_MustExistPK, _MustExistModel)
    )
else:

    class MustExist:
        """Existence-checked scalar foreign key: ``MustExist[int, Post]``.

        The first argument is the type of the id, the second the model of the
        row. The field stays the id everywhere (JSON, column,
        ``data.<field>``). On write, Restly checks that the row exists (404 on
        a miss), among the rows of the model's ``default_scope``.
        """

        def __class_getitem__(cls, params: Any) -> Any:
            if not isinstance(params, tuple) or len(params) != 2:
                raise TypeError(
                    "MustExist takes the type of the id and the model of the "
                    "row: MustExist[int, Post]. Restly does not take the model "
                    "from the foreign key, because a schema can also write "
                    "another model, and then the check would use the wrong "
                    "table."
                )
            pk_type, model = params
            if not isinstance(model, type):
                raise TypeError(
                    f"MustExist[{getattr(pk_type, '__name__', pk_type)}, "
                    f"{model!r}]: the second argument must be the model class, "
                    "such as MustExist[int, Post]."
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


def _unwrap_type(
    annotation: Any, seen: set[Any] | None = None
) -> tuple[Any, list[Any]]:
    """Return ``annotation`` without its ``Optional``, ``Annotated`` and type
    alias layers, and the metadata of each ``Annotated`` layer that it removed.

    Pydantic moves the metadata of an outer ``Annotated`` to
    ``field.metadata``, but it keeps an ``Annotated`` inside ``Optional`` or
    inside a type alias. So ``Annotated[IDRef[User], Field(...)] | None``,
    ``constr(...) | None`` and ``type AuthorId = MustExist[int, User]`` keep
    their layers, and every check of a field's type must remove them.
    ``seen`` is as in :func:`_alias_value`.
    """
    if seen is None:
        seen = set()
    metadata: list[Any] = []
    while True:
        unwrapped = _alias_value(_unwrap_optional_annotation(annotation), seen)
        if get_origin(unwrapped) is Annotated:
            unwrapped, *extra = get_args(unwrapped)
            metadata.extend(extra)
        if unwrapped is annotation:
            return annotation, metadata
        annotation = unwrapped


def is_reference_annotation(annotation: Any) -> bool:
    """True if ``annotation`` is an ``IDRef``/``IDSchema`` reference type.

    The one check for a reference type, kept beside the types so callers
    (views, query) don't inspect Pydantic generics themselves. Removes
    ``Optional`` and ``Annotated`` first, then matches the bare types or a
    parametrization whose generic origin is ``IDSchema``/``IDRef``
    (``IDRef[Post]``, ``IDSchema[Post]``) -- not a user subclass that merely
    inherits from them.
    """
    annotation, _ = _unwrap_type(annotation)
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
    """For an ``IDRef``/``IDSchema`` field annotation (``Optional`` and
    ``Annotated`` removed), return ``(origin, target)``: the
    ``IDRef``/``IDSchema`` class and the referenced model (``None`` for a bare,
    unparametrized reference), or ``None`` when the annotation is not a
    reference type. Lets the scalar-named-reference
    lint name both the type and its target in the ``MustExist`` hint.
    """
    annotation, _ = _unwrap_type(annotation)
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


_CHECKED_REFERENCES_KEY = "_fr_checked_references"

#: The value in the record for a reference whose row was not loaded: the
#: check of a ``MustExist`` field reads only the primary key.
_NO_ROW: Any = object()

#: The most ids in one ``IN`` list, below the parameter limits of the
#: databases (SQLite allows 32766 in all, older versions 999).
_IN_CHUNK = 500


def _checked_references(
    session: SA_Session | SA_AsyncSession,
) -> dict[tuple[type[Any], Any], Any]:
    """The references that the running write action has checked, as
    ``{(model, id): row}``. Empty outside a write action."""
    return session.info.get(_CHECKED_REFERENCES_KEY) or {}


@contextlib.contextmanager
def _remember_checked_references(
    session: SA_Session | SA_AsyncSession, checked: dict[tuple[type[Any], Any], Any]
) -> Iterator[None]:
    """Keep ``checked`` on the session until the block ends.

    The builders of :mod:`fastapi_restly.objects` read it, so a reference that
    the write action checked is not queried again. An id that code changes
    after the check is not in the record, so the builder checks it.
    """
    previous = session.info.get(_CHECKED_REFERENCES_KEY)
    session.info[_CHECKED_REFERENCES_KEY] = checked
    try:
        yield
    finally:
        if previous is None:
            session.info.pop(_CHECKED_REFERENCES_KEY, None)
        else:
            session.info[_CHECKED_REFERENCES_KEY] = previous


def _recorded_rows(
    checked: Mapping[tuple[type[Any], Any], Any], model: type[Any], ids: list[Any]
) -> list[Any] | None:
    """The rows that the write action loaded for ``ids``, or ``None`` when one
    of them is not in the record."""
    rows = [checked.get((model, id_), _NO_ROW) for id_ in ids]
    return None if any(row is _NO_ROW for row in rows) else rows


@dataclasses.dataclass(frozen=True)
class _BodyReference:
    """One reference in the data of a write action."""

    #: Where the id is, such as ``post_id`` or ``lines[2].product_id``.
    path: str
    model: type[Any]
    id: Any
    #: An ``IDRef`` or ``IDSchema``: the builder needs the row itself.
    row: bool
    #: One id of a list of references, such as ``tags: list[IDRef[Tag]]``.
    listed: bool = False


def _body_references(data: Any) -> list[_BodyReference]:
    """Every reference in ``data``, in the order of the fields.

    ``data`` is a schema object, or a list, tuple or dict of them. The walk
    goes into the fields that hold other schema objects, so a reference in
    an order line is found too. It reads only the fields that were set, and
    skips ``None``.
    """
    references: list[_BodyReference] = []
    seen: set[int] = set()
    if isinstance(data, pydantic.BaseModel):
        _collect_model_references(data, "", references, seen)
    elif isinstance(data, (list, tuple)):
        for index, item in enumerate(data):
            _collect_value_references(item, f"[{index}]", references, seen)
    elif isinstance(data, dict):
        for key, item in data.items():
            _collect_value_references(item, f"{key}", references, seen)
    return references


#: The classes of the data whose ``MustExist`` markers are known to be
#: where the check can see them.
_MARKERS_CHECKED: "weakref.WeakSet[type[pydantic.BaseModel]]" = weakref.WeakSet()


def _collect_model_references(
    obj: pydantic.BaseModel, path: str, references: list[_BodyReference], seen: set[int]
) -> None:
    if id(obj) in seen:
        return
    seen.add(id(obj))
    cls = type(obj)
    if cls not in _MARKERS_CHECKED:
        # fr.BaseSchema raises this when the class is defined; a plain
        # pydantic model only gets here
        _reject_buried_ref_exists(cls)
        _MARKERS_CHECKED.add(cls)
    for name, field_info in type(obj).model_fields.items():
        if name not in obj.model_fields_set:
            continue
        value = getattr(obj, name, None)
        if value is None:
            continue
        public_name = _client_name(name, field_info)
        here = f"{path}.{public_name}" if path else public_name
        marker = _ref_exists_marker(field_info)
        listed = _listed_ref_exists_marker(field_info) if marker is None else None
        if marker is not None:
            references.append(_BodyReference(here, marker.model, value, row=False))
        elif listed is not None:
            references.extend(
                _BodyReference(here, listed.model, item, row=False, listed=True)
                for item in value
                if item is not None
            )
        else:
            _collect_value_references(value, here, references, seen)


def _reference_model(value: Any) -> type[Any] | None:
    """The model that ``value`` refers to, when it is an ``IDRef`` or
    ``IDSchema`` reference."""
    if isinstance(value, IDSchema):
        return value.get_sql_model_annotation()
    return None


def _collect_value_references(
    value: Any, path: str, references: list[_BodyReference], seen: set[int]
) -> None:
    model = _reference_model(value)
    if model is not None:
        references.append(_BodyReference(path, model, value.id, row=True))
    elif isinstance(value, pydantic.BaseModel):
        _collect_model_references(value, path, references, seen)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            item_model = _reference_model(item)
            if item_model is not None:
                references.append(
                    _BodyReference(path, item_model, item.id, row=True, listed=True)
                )
            else:
                _collect_value_references(item, f"{path}[{index}]", references, seen)
    elif isinstance(value, dict):
        for key, item in value.items():
            _collect_value_references(item, f"{path}.{key}", references, seen)


def _reference_lookups(
    references: list[_BodyReference],
) -> dict[type[Any], tuple[list[Any], bool]]:
    """``{model: (ids, rows)}``: the ids of each model, without repeats, and
    whether a reference to it needs the rows."""
    lookups: dict[type[Any], tuple[dict[Any, None], bool]] = {}
    for reference in references:
        ids, rows = lookups.get(reference.model, ({}, False))
        ids[reference.id] = None
        lookups[reference.model] = (ids, rows or reference.row)
    return {model: (list(ids), rows) for model, (ids, rows) in lookups.items()}


def _reference_statements(
    model: type[Any], ids: list[Any], rows: bool
) -> Iterator[tuple[Any, str]]:
    """The statements that find the rows of ``model`` for ``ids``, in chunks,
    and the name of its primary key attribute.

    The statements are ORM statements over the mapped attribute, so a rule of
    the session (``with_loader_criteria``) reaches them too.
    """
    mapper = sa_inspect(model)
    key = mapper.get_property_by_column(mapper.primary_key[0]).key
    pk = getattr(model, key)
    scope = _default_scope(model)
    for start in range(0, len(ids), _IN_CHUNK):
        stmt = select(model if rows else pk).where(
            pk.in_(ids[start : start + _IN_CHUNK])
        )
        if scope is not None:
            stmt = apply_clauses(stmt, scope)
        yield stmt, key


def _found_references(
    model: type[Any], ids: list[Any], rows: bool, key: str, results: Iterable[Any]
) -> dict[tuple[type[Any], Any], Any]:
    """``{(model, id): row}`` for what one statement found.

    A row is keyed by the id that the client sent when the statement asked
    for one id: the database matched it, and its key can differ in Python,
    as with a case-insensitive key.
    """
    found = list(results)
    if len(ids) == 1 and len(found) == 1:
        return {(model, ids[0]): found[0] if rows else _NO_ROW}
    if rows:
        return {(model, getattr(row, key)): row for row in found}
    return {(model, id_): _NO_ROW for id_ in found}


def _raise_for_missing_references(
    references: list[_BodyReference], found: Mapping[tuple[type[Any], Any], Any]
) -> None:
    """Raise ``NotFound`` for the first field that names a row that was not
    found. A list of references names all its missing ids."""
    missing: dict[str, list[Any]] = {}
    listed: set[str] = set()
    for reference in references:
        if (reference.model, reference.id) in found:
            continue
        ids = missing.setdefault(reference.path, [])
        if reference.id not in ids:
            ids.append(reference.id)
        if reference.listed:
            listed.add(reference.path)
    for path, ids in missing.items():
        shown = _id_list(ids) if path in listed else ids[0]
        raise NotFound(f"Id not found for {path}: {shown}")


def _check_body_references(
    session: SA_Session, data: Any
) -> dict[tuple[type[Any], Any], Any]:
    """Check every reference in ``data``, and return what was found as
    ``{(model, id): row}``.

    One query per referenced model (in chunks for a long list). A reference
    to a row that the model's ``default_scope`` or the session hides is a
    miss, and raises ``NotFound`` (404) with the field and the id.
    """
    references = _body_references(data)
    found: dict[tuple[type[Any], Any], Any] = {}
    # the query must not write an object that the code is still building
    with session.no_autoflush:
        for model, (ids, rows) in _reference_lookups(references).items():
            for stmt, key in _reference_statements(model, ids, rows):
                results = session.scalars(stmt).unique()
                found.update(_found_references(model, ids, rows, key, results))
    _raise_for_missing_references(references, found)
    return found


async def _async_check_body_references(
    session: SA_AsyncSession, data: Any
) -> dict[tuple[type[Any], Any], Any]:
    """Async twin of :func:`_check_body_references`."""
    references = _body_references(data)
    found: dict[tuple[type[Any], Any], Any] = {}
    with session.no_autoflush:
        for model, (ids, rows) in _reference_lookups(references).items():
            for stmt, key in _reference_statements(model, ids, rows):
                results = (await session.scalars(stmt)).unique()
                found.update(_found_references(model, ids, rows, key, results))
    _raise_for_missing_references(references, found)
    return found


def _id_schema_fields(
    schema_obj: pydantic.BaseModel, model_cls: type[Any] | None
) -> Iterator[tuple[str, type[Any], list[Any], bool]]:
    """``(field, model, ids, is_list)`` for each ``IDRef`` or ``IDSchema``
    field that the client sent. ``ids`` keeps the client's order, without
    repeats, so a repeated id cannot give a false 404.

    A tuple counts only when :func:`_is_reference_tuple` accepts it."""
    for field in schema_obj.model_fields_set:
        value = getattr(schema_obj, field, None)
        model = _reference_model(value)
        if model is not None:
            yield field, model, [value.id], False
        elif (
            isinstance(value, list) or _is_reference_tuple(schema_obj, field, model_cls)
        ) and any(isinstance(i, IDSchema) for i in value):
            # Assume all IdSchemas are for the same model
            model = value[0].get_sql_model_annotation()
            if model is not None:
                yield field, model, list(dict.fromkeys(i.id for i in value)), True


def _is_reference_tuple(
    schema_obj: pydantic.BaseModel, field: str, model_cls: type[Any] | None
) -> bool:
    """Whether ``field`` of ``schema_obj`` holds a tuple of references for a
    to-many relationship of ``model_cls``.

    The field's type decides, not its value: it must be a collection of
    references, such as ``tuple[IDRef[Tag], ...]``. Another tuple, such as
    ``tuple[IDRef[Author], int]`` or a tuple for a JSON column, is written as
    it is.
    """
    field_info = type(schema_obj).model_fields.get(field)
    if (
        model_cls is None
        or field_info is None
        or not isinstance(getattr(schema_obj, field, None), tuple)
    ):
        return False
    item = _collection_item_type(field_info.annotation)
    return (
        item is not None
        and is_reference_annotation(item)
        and is_to_many_relationship(model_cls, field)
    )


def _client_name(field_name: str, field_info: FieldInfo) -> str:
    """The name of a field as the client sends it in a body: its validation
    alias when that is a string, else its alias, else its name."""
    if isinstance(field_info.validation_alias, str):
        return field_info.validation_alias
    return field_info.alias or field_name


def _public_name(schema_obj: pydantic.BaseModel, field: str) -> str:
    """The name of ``field`` of ``schema_obj`` as the client sends it."""
    return _client_name(field, type(schema_obj).model_fields[field])


def _id_list(ids: list[Any]) -> str:
    """Ids for an error text, as the client wrote them, such as ``[7, 9]``."""
    return "[" + ", ".join(map(str, ids)) + "]"


def _id_schema_rows(
    label: str, ids: list[Any], found: list[Any], key: str, is_list: bool
) -> Any:
    """The resolved value of a field, from the rows that were ``found``.
    Raises ``NotFound`` for an id that has no row, with the field's public
    name, ``label``."""
    if not is_list:
        if not found:
            raise NotFound(f"Id not found for {label}: {ids[0]}")
        return found[0]
    by_id = {getattr(row, key): row for row in found}
    missing = [id_ for id_ in ids if id_ not in by_id]
    if missing:
        raise NotFound(f"Id not found for {label}: {_id_list(missing)}")
    return [by_id[id_] for id_ in ids]


async def _async_resolve_ids_to_sqlalchemy_objects(
    session: SA_AsyncSession,
    schema_obj: pydantic.BaseModel,
    model_cls: type[Any] | None = None,
) -> dict[str, Any]:
    """Async twin of :func:`_resolve_ids_to_sqlalchemy_objects`."""
    checked = _checked_references(session)
    resolved: dict[str, Any] = {}
    for field, model, ids, is_list in _id_schema_fields(schema_obj, model_cls):
        rows = _recorded_rows(checked, model, ids)
        if rows is not None:
            resolved[field] = rows if is_list else rows[0]
            continue
        if not is_list and _default_scope(model) is None:
            # unscoped: keep get_one, which serves identity-map hits
            try:
                resolved[field] = await session.get_one(model, ids[0])
            except NoResultFound as e:
                label = _public_name(schema_obj, field)
                raise NotFound(f"Id not found for {label}: {ids[0]}") from e
            continue
        found: list[Any] = []
        for stmt, key in _reference_statements(model, ids, rows=True):
            found.extend((await session.scalars(stmt)).unique())
        label = _public_name(schema_obj, field)
        resolved[field] = _id_schema_rows(label, ids, found, key, is_list)
    return resolved


def _resolve_ids_to_sqlalchemy_objects(
    session: SA_Session,
    schema_obj: pydantic.BaseModel,
    model_cls: type[Any] | None = None,
) -> dict[str, Any]:
    """Resolve the ``IDRef`` and ``IDSchema`` fields of ``schema_obj`` to rows.

    Returns ``{field_name: row_or_rows}`` for the fields that refer to a
    model. ``schema_obj`` itself is left unchanged, so it keeps the validated
    request values (``IDRef[T]``, not ORM rows). ``model_cls`` is the model
    that ``schema_obj`` is written to. A tuple of references resolves only
    for a to-many relationship of it, so without ``model_cls`` no tuple
    resolves.

    The lookup applies the referenced model's ``default_scope``, so a
    reference to a row that the scope hides is ``NotFound``, as a missing row
    is. A model without a ``default_scope`` resolves by primary key alone.
    Inside a write action, a reference that the action already checked is
    taken from its record, without a query.
    """
    checked = _checked_references(session)
    resolved: dict[str, Any] = {}
    for field, model, ids, is_list in _id_schema_fields(schema_obj, model_cls):
        rows = _recorded_rows(checked, model, ids)
        if rows is not None:
            resolved[field] = rows if is_list else rows[0]
            continue
        if not is_list and _default_scope(model) is None:
            # unscoped: keep get_one, which serves identity-map hits
            try:
                resolved[field] = session.get_one(model, ids[0])
            except NoResultFound as e:
                label = _public_name(schema_obj, field)
                raise NotFound(f"Id not found for {label}: {ids[0]}") from e
            continue
        found: list[Any] = []
        for stmt, key in _reference_statements(model, ids, rows=True):
            found.extend(session.scalars(stmt).unique())
        label = _public_name(schema_obj, field)
        resolved[field] = _id_schema_rows(label, ids, found, key, is_list)
    return resolved


def _ref_exists_marker(field_info: FieldInfo) -> RefExists | None:
    """Return the ``RefExists`` marker on a field, or ``None``.

    Found at the field's top level (``MustExist[pk, M]``), or recovered from
    the union member for an optional field (``MustExist[pk, M] | None``),
    where Pydantic keeps the marker on the inner annotation rather than
    ``field_info.metadata`` -- so an optional checked FK is checked like the
    plain form (parity with ``Optional[IDRef]``).
    """
    _, metadata = _unwrap_type(field_info.annotation)
    for m in (*field_info.metadata, *metadata):
        if isinstance(m, RefExists):
            return m
    return None


#: The collections of a to-many field of rows, as in ``Sequence[PostSchema]``
#: or ``list[IDRef[Tag]]``. A set cannot hold them: Pydantic models are not
#: hashable.
_ROW_COLLECTIONS = (list, tuple, _AbcSequence)
#: The collections of a list of ids, as in ``list[MustExist[int, Tag]]``.
_ID_COLLECTIONS = (*_ROW_COLLECTIONS, set, frozenset, _AbcSet)


def _collection_item_type(
    annotation: Any,
    seen: set[Any] | None = None,
    collections: tuple[type, ...] = _ROW_COLLECTIONS,
) -> Any:
    """The item type of a list-like ``annotation``, or ``None``.

    The ``collections`` count, by default a list and a ``Sequence``, and a
    tuple only in its form with one item type, ``tuple[X, ...]``. ``str``
    does not count. The ``Optional``, ``Annotated`` and type alias layers
    around the collection are removed first. ``seen`` is as in
    :func:`_alias_value`.
    """
    annotation, _ = _unwrap_type(annotation, seen)
    origin = get_origin(annotation)
    if origin not in collections:
        return None
    args = get_args(annotation)
    if origin is tuple and not (len(args) == 2 and args[1] is Ellipsis):
        return None
    return args[0] if args else None


def _listed_ref_exists_marker(field_info: FieldInfo) -> RefExists | None:
    """Return the ``RefExists`` marker of the items of a list field, such as
    ``list[MustExist[int, Tag]]``, or ``None``.

    The field may allow ``None``, and so may each item. A tuple counts only
    in its form with one item type, ``tuple[MustExist[int, Tag], ...]``.
    """
    item = _collection_item_type(field_info.annotation, collections=_ID_COLLECTIONS)
    if item is None:
        return None
    _, metadata = _unwrap_type(item)
    for m in metadata:
        if isinstance(m, RefExists):
            return m
    return None


def _ref_exists_fields(
    schema_obj: pydantic.BaseModel,
) -> dict[type[Any], list[tuple[str, Any, bool]]]:
    """``{model: [(label, id, listed), ...]}`` for the ``MustExist``
    fields that the client sent with a value other than ``None``. ``label`` is
    the name that the client sends. A list of ids gives one entry for each
    id, with ``listed`` set."""
    by_target: dict[type[Any], list[tuple[str, Any, bool]]] = {}
    model_fields = type(schema_obj).model_fields
    for field_name in schema_obj.model_fields_set:
        field_info = model_fields.get(field_name)
        if field_info is None:
            continue
        value = getattr(schema_obj, field_name, None)
        if value is None:
            continue
        label = _client_name(field_name, field_info)
        marker = _ref_exists_marker(field_info)
        if marker is not None:
            by_target.setdefault(marker.model, []).append((label, value, False))
            continue
        listed = _listed_ref_exists_marker(field_info)
        if listed is not None:
            by_target.setdefault(listed.model, []).extend(
                (label, item, True) for item in value if item is not None
            )
    return by_target


def _unchecked_ref_exists_fields(
    session: SA_Session | SA_AsyncSession, schema_obj: pydantic.BaseModel
) -> dict[type[Any], list[tuple[str, Any, bool]]]:
    """:func:`_ref_exists_fields` without the ids that the running write
    action has checked."""
    checked = _checked_references(session)
    unchecked: dict[type[Any], list[tuple[str, Any, bool]]] = {}
    for model, items in _ref_exists_fields(schema_obj).items():
        items = [item for item in items if (model, item[1]) not in checked]
        if items:
            unchecked[model] = items
    return unchecked


def _raise_for_missing_refs(
    items: list[tuple[str, Any, bool]], found: set[Any]
) -> None:
    """Raise ``NotFound`` for the first field with an id that was not found.
    A list of ids names all its missing ids."""
    missing: dict[str, list[Any]] = {}
    listed: set[str] = set()
    for field_name, value, is_listed in items:
        if value in found:
            continue
        ids = missing.setdefault(field_name, [])
        if value not in ids:
            ids.append(value)
        if is_listed:
            listed.add(field_name)
    for field_name, ids in missing.items():
        shown = _id_list(ids) if field_name in listed else ids[0]
        raise NotFound(f"Id not found for {field_name}: {shown}")


def _check_ref_exists(session: SA_Session, schema_obj: pydantic.BaseModel) -> None:
    """Check that every ``MustExist`` id of ``schema_obj`` exists (sync).

    One ``SELECT pk WHERE pk IN (...)`` per referenced model (no N+1); raises
    ``NotFound`` (404) naming the field and id on a miss. The model's
    ``default_scope`` applies, so a row the scope hides is a miss. Inside a
    write action, an id that the action already checked is not queried again.
    """
    for model, items in _unchecked_ref_exists_fields(session, schema_obj).items():
        ids = list(dict.fromkeys(value for _, value, _ in items))
        found: set[Any] = set()
        for stmt, _ in _reference_statements(model, ids, rows=False):
            found.update(session.scalars(stmt))
        _raise_for_missing_refs(items, found)


async def _async_check_ref_exists(
    session: SA_AsyncSession, schema_obj: pydantic.BaseModel
) -> None:
    """Async twin of :func:`_check_ref_exists`."""
    for model, items in _unchecked_ref_exists_fields(session, schema_obj).items():
        ids = list(dict.fromkeys(value for _, value, _ in items))
        found: set[Any] = set()
        for stmt, _ in _reference_statements(model, ids, rows=False):
            found.update(await session.scalars(stmt))
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


#: Pydantic's secret types. A response shows their value masked, and a write
#: stores the secret.
_SECRET_TYPES: tuple[type, ...] = (
    pydantic.SecretStr,
    pydantic.SecretBytes,
    pydantic.Secret,
)


def _hides_value(field: FieldInfo) -> bool:
    """Whether a response never shows the value of ``field``.

    A WriteOnly field, or one with ``exclude=True``, never goes out. A field
    of one of Pydantic's secret types goes out masked. A filter or a sort on
    such a field would let a client find out the value.
    """
    if field.exclude is True:
        return True
    annotation, _ = _unwrap_type(field.annotation)
    origin = get_origin(annotation) or annotation
    return isinstance(origin, type) and issubclass(origin, _SECRET_TYPES)


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
    model_cls: type[pydantic.BaseModel], *, omit: Iterable[str] = ()
) -> type[pydantic.BaseModel]:
    """
    Create a subclass of the given pydantic model class with a new name.
    Read-only fields are removed, and so are the fields named in ``omit``.
    """
    new_model_name = _schema_role_name(model_cls, "Create")
    new_doc = (model_cls.__doc__ or "") + "\nRead-only fields have been removed."

    # Create a subclass that mixes in OmitReadOnlyMixin
    new_model_cls = type(
        new_model_name,
        (OmitReadOnlyMixin, model_cls),
        {
            "__module__": model_cls.__module__,
            "__doc__": new_doc,
            "__restly_omit_fields__": frozenset(omit),
        },
    )

    return new_model_cls


class OmitReadOnlyMixin(pydantic.BaseModel):
    """
    Mixin for pydantic models that removes all fields marked as ReadOnly.

    Implementation note: this mutates ``cls.model_fields`` in place and then
    calls ``model_rebuild(force=True)`` to regenerate the validator/serializer.
    Pydantic v2 does not officially document mutation of ``model_fields`` as
    a supported customisation hook, but this approach has been stable since
    pydantic 2.0 and works on the pinned minimum (``pydantic>=2.11.7``). The
    regression test ``tests/test_pydantic_model_fields_mutation.py`` exercises
    this contract on the currently-installed pydantic.

    Building the class with ``pydantic.create_model(...)`` over the kept
    fields was tried first, and it is harder: validators, config, generics
    and serializers do not carry over the way inheritance carries them. Only
    revisit that if pydantic stops allowing this mutation.
    """

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)

        # Collect readonly fields to delete first
        omit = getattr(cls, "__restly_omit_fields__", ())
        readonly_fields = []
        for name, field_info in cls.model_fields.items():
            if _is_readonly(field_info) or name in omit:
                readonly_fields.append(name)

        # Delete readonly fields after iteration is complete
        for name in readonly_fields:
            del cls.model_fields[name]
        _skip_field_checks(cls, set(readonly_fields))

        cls.model_rebuild(force=True)


class _OmitWriteOnlyMixin(pydantic.BaseModel):
    """Mixin for the response class: removes the WriteOnly fields."""

    #: The names and aliases of the removed WriteOnly fields.
    __restly_writeonly_keys__: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)

        writeonly_fields = {
            name: field_info
            for name, field_info in cls.model_fields.items()
            if _is_writeonly(field_info)
        }
        if not writeonly_fields:
            return
        cls.__restly_writeonly_keys__ = frozenset(
            key
            for name, field_info in writeonly_fields.items()
            for key in _input_keys(name, field_info)
        )
        for name in writeonly_fields:
            del cls.model_fields[name]
        _skip_field_checks(cls, set(writeonly_fields))

        # A forward reference that is not defined yet does not fail here:
        # pydantic builds the class again when it is first used.
        cls.model_rebuild(force=True, raise_errors=False)

    # "before", not "wrap": pydantic does not pass ``by_name`` through a wrap
    # validator to the nested models.
    @pydantic.model_validator(mode="before")
    @classmethod
    def _drop_writeonly_keys(cls, value: Any) -> Any:
        """Drop WriteOnly keys from a dict. With ``extra="allow"`` they would
        otherwise go out as extra fields."""
        keys = cls.__restly_writeonly_keys__
        if keys and isinstance(value, Mapping):
            return {key: item for key, item in value.items() if key not in keys}
        return value


def _input_keys(name: str, field_info: FieldInfo) -> set[str]:
    """The keys under which a field can be in a dict: its name and aliases."""
    keys = {name}
    if field_info.alias is not None:
        keys.add(field_info.alias)
    validation_alias = field_info.validation_alias
    if isinstance(validation_alias, str):
        keys.add(validation_alias)
    elif isinstance(validation_alias, pydantic.AliasChoices):
        keys.update(
            choice for choice in validation_alias.choices if isinstance(choice, str)
        )
    return keys


def _skip_field_checks(cls: type[pydantic.BaseModel], removed: set[str]) -> None:
    """Let validators and serializers of the removed fields stay on ``cls``.

    Pydantic checks that each field validator and serializer names a field of
    the class. A ``@field_validator("password")`` on the view's schema would
    then fail the response class, which has no ``password``, and a
    ``@field_validator("slug")`` on a ``ReadOnly`` slug would fail the request
    bodies. Without the check, pydantic does not use it there.
    """
    decorators = cls.__pydantic_decorators__
    for group in (
        decorators.validators,
        decorators.field_validators,
        decorators.field_serializers,
    ):
        for name, decorator in list(group.items()):
            if removed.intersection(decorator.info.fields):
                info = dataclasses.replace(decorator.info, check_fields=False)
                group[name] = dataclasses.replace(decorator, info=info)  # type: ignore[assignment]


def _as_response(cls: type[pydantic.BaseModel], value: Any) -> Any:
    """An instance of another model as an instance of the response class
    ``cls``. Fields that ``cls`` does not have stay behind, also on a route
    without a response model, such as the list of a react-admin view.

    Restly copies the values of an instance of a subclass of ``cls``, and of
    an instance of the view's schema for a response class that
    :func:`derive_schema_response` built. It does not validate them again:
    they are already valid, and a validator that changes a value would
    change it twice.

    Any other model is validated into ``cls`` from its attributes. A response
    class that a view sets itself can have other fields and types, so Restly
    cannot copy into it.

    A value that is not a model comes back as it is. So does an ORM object,
    also a SQLModel table row, which is a model too: the view serializes it
    as an ORM object.
    """
    if type(value) is cls or not isinstance(value, pydantic.BaseModel):
        return value
    if is_mapped_instance(value):
        return value
    schema = cls.__dict__.get("__restly_response_of__")
    if not isinstance(value, cls) and (schema is None or not isinstance(value, schema)):
        return _response_adapter(cls).validate_python(
            value, from_attributes=True, by_alias=False, by_name=True
        )
    fields = cls.model_fields
    values = {name: item for name, item in value.__dict__.items() if name in fields}
    writeonly_keys = getattr(cls, "__restly_writeonly_keys__", frozenset())
    extra = {
        key: item
        for key, item in (value.__pydantic_extra__ or {}).items()
        if key not in writeonly_keys
    }
    copy = cls.model_construct(
        _fields_set=value.model_fields_set & fields.keys(), **extra, **values
    )
    if value.__pydantic_private__ is not None:
        object.__setattr__(
            copy, "__pydantic_private__", dict(value.__pydantic_private__)
        )
    return copy


@functools.cache
def _response_adapter(
    schema_response: type[pydantic.BaseModel],
) -> pydantic.TypeAdapter[pydantic.BaseModel]:
    """Validates into a response class. Through an adapter, not
    ``model_validate``: a schema may narrow that classmethod's signature
    (SQLModel drops ``by_alias`` and ``by_name``)."""
    return pydantic.TypeAdapter(schema_response)


_SchemaT = TypeVar("_SchemaT", bound=pydantic.BaseModel)


def derive_schema_response(schema: type[_SchemaT]) -> type[_SchemaT]:
    """Build the response class of a view's schema: the schema without its
    ``WriteOnly`` fields, named ``<Resource>Response``.

    A view without its own
    :attr:`~fastapi_restly.views.BaseRestView.schema_response` gets this
    class. Restly builds it also when the schema has no ``WriteOnly`` fields,
    so OpenAPI shows ``UserResponse`` and not the view's schema.

    The class is a subclass of ``schema``. Each call with the same schema
    returns the same class, so a custom route can name the class that the
    view uses::

        UserResponse = fr.schemas.derive_schema_response(UserSchema)


        class UserView(fr.AsyncRestView):
            schema = UserSchema

            @fr.get("/me", response_model=UserResponse)
            async def me(self): ...

    :param schema: The view's schema.
    :returns: The response class.
    """
    return cast(type[_SchemaT], _derive_schema_response(schema))


@functools.cache
def _derive_schema_response(
    schema: type[pydantic.BaseModel],
) -> type[pydantic.BaseModel]:
    """Cached, so a view, a custom route and every request use the same
    class. :func:`_as_response` turns an instance of the schema into an
    instance of this class."""
    cls = type(
        _schema_role_name(schema, "Response"),
        (_OmitWriteOnlyMixin, schema),
        {
            "__module__": schema.__module__,
            "__doc__": schema.__doc__,
            "__restly_response_of__": schema,
        },
    )
    # A recursive schema refers to itself by name, like list["NodeSchema"].
    # Pydantic finds a class's own name, but not the name of a parent class
    # that was defined in a function. So the response class gets that name.
    namespace = {**(schema.__pydantic_parent_namespace__ or {})}
    namespace[schema.__name__] = schema
    cls.model_rebuild(force=True, raise_errors=False, _types_namespace=namespace)
    return cls


def derive_schema_list_response(
    schema_response: type[pydantic.BaseModel],
    *,
    pagination: NumberedPagination | NoPagination | None = _DEFAULT_PAGINATION,
) -> type[pydantic.BaseModel]:
    """Build the list response class of a view: the envelope of its
    pagination, filled with its response class, named
    ``<Resource>ListResponse``.

    ``GET /`` of a view returns this class, except on a react-admin view,
    which returns a plain list. A custom list route names it, so OpenAPI
    shows one type for both routes::

        UserResponse = fr.schemas.derive_schema_response(UserSchema)
        UserListResponse = fr.schemas.derive_schema_list_response(
            UserResponse, pagination=AppView.pagination
        )


        class UserView(AppView):
            schema = UserSchema

            @fr.get("/inactive", response_model=UserListResponse)
            async def inactive(self, list_params): ...

    Each call with the same response class and envelope returns the same
    class. Only the envelope of the pagination matters: two paginations with
    the same envelope give the same class. A view has no attribute for this
    class. To change it, change the view's ``schema_response`` or the
    envelope of its ``pagination``.

    :param schema_response: The view's response class, such as the one from
        :func:`derive_schema_response`.
    :param pagination: The view's pagination. ``None`` is a view without
        pagination, as on the view. The default is the default pagination.
    :returns: The list response class.
    """
    envelope = Envelope if pagination is None else pagination.envelope
    return _named_list_envelope(envelope, schema_response)


@functools.cache
def _named_list_envelope(
    envelope: type[pydantic.BaseModel], schema_response: type[pydantic.BaseModel]
) -> type[pydantic.BaseModel]:
    """Cached, so the route and every request use the same class, and two
    views with the same response class and envelope share it.

    It is a subclass, so OpenAPI shows ``UserListResponse`` and not
    ``PaginatedEnvelope_UserResponse_``.
    """
    filled: Any = envelope[schema_response]  # type: ignore[index]
    return type(
        _schema_role_name(schema_response, "ListResponse"),
        (filled,),
        {
            "__module__": schema_response.__module__,
            # For the warning about two classes with one name.
            "__restly_list_response_of__": (envelope, schema_response),
        },
    )


def rebase_with_model_config(
    base: tuple[type, ...], model_cls: type[pydantic.BaseModel]
) -> type[pydantic.BaseModel]:
    def class_body(ns: dict[str, Any]) -> None:
        ns["model_config"] = model_cls.model_config.copy()

    return types.new_class(
        f"{model_cls.__name__}ModelConfig", base, exec_body=class_body
    )


def create_model_with_optional_fields(
    model_cls: type[pydantic.BaseModel], *, omit: Iterable[str] = ()
) -> type[pydantic.BaseModel]:
    """
    Create a subclass of the given pydantic model class with a new name.
    Read-only fields and the fields named in ``omit`` are removed, and the
    client may leave out each writable field, which then defaults to None.
    Each field keeps its type, so ``null`` is valid only where the type
    allows it; see :class:`PatchMixin`.
    """
    new_model_name = _schema_role_name(model_cls, "Update")
    new_doc = (
        model_cls.__doc__ or ""
    ) + "\nRead-only fields have been removed and all fields are optional."

    # Create a subclass that mixes in both OmitReadOnlyMixin and PatchMixin
    new_model_cls = type(
        new_model_name,
        (PatchMixin, OmitReadOnlyMixin, model_cls),
        {
            "__module__": model_cls.__module__,
            "__doc__": new_doc,
            "__restly_omit_fields__": frozenset(omit),
        },
    )

    return new_model_cls


class PatchMixin(pydantic.BaseModel):
    """
    A mixin for pydantic classes that lets the client leave out every field,
    for the body of ``PATCH``.

    Each field keeps its type, so ``null`` is valid only where the type
    allows ``None``, as in the body of ``POST``. A field that the client
    leaves out gets the default ``None``. Pydantic does not validate it, and
    Restly writes only the fields in ``model_fields_set``, so the default is
    never written. The defaults of the schema are **replaced** by ``None``.

    Implementation note: like :class:`OmitReadOnlyMixin` this mutates
    ``cls.model_fields`` (``FieldInfo.default`` and ``validate_default``) and
    then calls ``model_rebuild(force=True)``. This relies on pydantic v2
    keeping ``FieldInfo`` mutable; verified on the pinned
    ``pydantic>=2.11.7`` minimum and exercised by
    ``tests/test_pydantic_model_fields_mutation.py``.
    """

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)

        for field in cls.model_fields.values():
            field.default = None
            # A field cannot have both. FastAPI builds the field again from
            # its attributes for OpenAPI, which raises with both.
            field.default_factory = None
            # Also when the class sets ``validate_default=True``: the default
            # is not a value of the field's type.
            field.validate_default = False

        # The rebuild validates the default again for a field with a
        # ``@validator(..., always=True)``.
        validators = cls.__pydantic_decorators__.validators
        for name, decorator in list(validators.items()):
            if decorator.info.always:
                info = dataclasses.replace(decorator.info, always=False)
                validators[name] = dataclasses.replace(decorator, info=info)  # type: ignore[assignment]

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


def get_writable_inputs(schema_obj: pydantic.BaseModel) -> dict[str, Any]:
    """
    Return a dictionary of field_name: value pairs for writable input fields.

    Filters out:
    - ReadOnly fields
    - fields not provided with input (using Pydantic model_fields_set)

    :param schema_obj: The schema object to extract writable fields from. Its
        own schema decides which fields are ``ReadOnly``.
    :returns: Dictionary mapping field names to their values, for writable
        input fields only.
    """
    schema_cls = type(schema_obj)
    updated_fields: dict[str, Any] = {}
    for field_name, value in schema_obj:
        if field_name not in schema_obj.model_fields_set:
            continue
        # Skip readonly fields
        if is_readonly_field(schema_cls, field_name):
            continue
        updated_fields[field_name] = value

    return updated_fields
