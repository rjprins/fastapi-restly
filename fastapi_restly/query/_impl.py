import collections.abc as _abc
import dataclasses
import datetime as _dt
import decimal as _decimal
import enum
import functools
import re
import uuid as _uuid
import weakref
from collections import defaultdict
from typing import Annotated, Any, Callable, Iterator, Optional, cast, get_origin

import pydantic
import sqlalchemy
from pydantic import Field
from pydantic.fields import FieldInfo
from pydantic_core import PydanticCustomError, SchemaValidator, core_schema
from sqlalchemy import ColumnElement, Select
from sqlalchemy.orm import DeclarativeBase, aliased
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.orm.properties import ColumnProperty
from sqlalchemy.orm.util import AliasedClass
from starlette.datastructures import QueryParams

from .._pagination import (
    _DEFAULT_PAGINATION,
    _SORT_QUERY_PARAM,
    NoPagination,
    NumberedPagination,
    _check_query_param,
)
from ..exc import BadQueryParam, RestlyConfigurationError
from ..schemas._base import IDRef, IDSchema, _schema_role_name, _unwrap_type
from ._shared import SortNulls, _append_pk_tiebreak, _escape_like_value, _sort_term

SchemaType = type[pydantic.BaseModel]
_JoinAliases = dict[tuple[str, ...], AliasedClass[Any]]

_AnyPagination = NumberedPagination | NoPagination | None

#: The names of the settings that choose the generated keys, in order.
_KEY_SETTINGS = (
    "include_filters",
    "exclude_filters",
    "include_sort_keys",
    "exclude_sort_keys",
)

#: The value of each key setting when no one sets it: the keys on the
#: resource's own fields, and no dotted keys.
_KEY_SETTING_DEFAULTS: dict[str, tuple[str, ...]] = {
    "include_filters": ("*",),
    "exclude_filters": (),
    "include_sort_keys": ("*",),
    "exclude_sort_keys": (),
}


_NO_DEFAULT: Any = object()


@dataclasses.dataclass(frozen=True)
class _OwnFilter:
    """A filter key of the view's own, from a method with
    ``@fr.list_filter``."""

    key: str
    #: The name of the method on the view.
    method: str
    #: The type of the method's value.
    annotation: Any
    #: The default of the value, or ``_NO_DEFAULT``.
    default: Any
    description: str | None

    @property
    def always(self) -> bool:
        """Whether Restly calls the method on every request: its value has
        a default. Without one, it calls the method when the key has a
        value."""
        return self.default is not _NO_DEFAULT


@dataclasses.dataclass(frozen=True)
class _ListParamsRecord:
    """What Restly generated a list params model with.

    :func:`apply_list_params` and view registration read it back.
    """

    pagination: _AnyPagination
    #: The key settings, as tuples of patterns.
    include_filters: tuple[str, ...]
    exclude_filters: tuple[str, ...]
    include_sort_keys: tuple[str, ...]
    exclude_sort_keys: tuple[str, ...]
    #: The name of the sort parameter.
    sort_query_param: str
    #: The sort when the request has none, as the view sets it.
    default_sort: str | None
    #: The keys of ``default_sort``.
    default_sort_keys: tuple[str, ...]
    #: The sort keys that are on, in the order of the schema.
    sort_keys: tuple[str, ...]
    #: The view's own filter keys.
    own_filters: tuple[_OwnFilter, ...]

    def own_filter_values(
        self, list_params: pydantic.BaseModel
    ) -> list[tuple[_OwnFilter, Any]]:
        """The own filters to apply to ``list_params``, each with its
        value."""
        values = []
        for own in self.own_filters:
            value = getattr(list_params, own.key)
            if own.always or value is not None:
                values.append((own, value))
        return values


_RECORDS: "weakref.WeakKeyDictionary[type, _ListParamsRecord]" = (
    weakref.WeakKeyDictionary()
)


class _FromParams:
    """The default of :func:`apply_list_params`'s ``pagination``: the one its
    params model was created with."""

    def __repr__(self) -> str:
        # the default as the API reference shows it; the docstring explains it
        return "..."


_FROM_PARAMS = _FromParams()


def _record_of(params_model: type) -> _ListParamsRecord | None:
    """What generated list params were created with, or ``None`` for
    hand-written ones."""
    return _RECORDS.get(params_model)


def _check_default_sort(label: str, value: Any) -> None:
    """Reject a ``default_sort`` that is not sort keys as a client writes
    them in the sort parameter.

    :raises RestlyConfigurationError: ``value`` is not ``None`` or a string
        of keys separated by commas, or it has a key more than once.
    """
    if value is None:
        return
    if not isinstance(value, str):
        raise RestlyConfigurationError(
            f"{label} must be a string such as '-created_at,name', or None, "
            f"got {value!r}."
        )
    keys = [term.removeprefix("-") for term in value.split(",")]
    if not all(keys):
        raise RestlyConfigurationError(
            f"{label} must be sort keys separated by commas, such as "
            f"'-created_at,name', got {value!r}."
        )
    repeated = _repeated_key(keys)
    if repeated is not None:
        raise RestlyConfigurationError(
            f"{label} has the sort key {repeated!r} more than once, got {value!r}."
        )


def _repeated_key(keys: _abc.Iterable[str]) -> str | None:
    """The first key that comes a second time, or ``None``. ``name,-name``
    sorts twice on one column, and the second term changes nothing."""
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            return key
        seen.add(key)
    return None


def _check_key_setting(label: str, value: Any) -> tuple[str, ...]:
    """The value of a key setting as a tuple of patterns.

    :param label: The setting in messages, such as ``ItemView.include_filters``.

    :raises RestlyConfigurationError: ``value`` is a string, holds something
        that is not a string, or uses a wildcard that Restly does not have.
    """
    if isinstance(value, str):
        raise RestlyConfigurationError(
            f"{label} must be a tuple of keys, got the string {value!r}. "
            f"For one key, write ({value!r},) with a comma."
        )
    if isinstance(value, _abc.Iterator):
        # the class-definition check would use it up before registration
        raise RestlyConfigurationError(
            f"{label} must be a tuple of keys, got an iterator, which can be "
            "read only once. Write tuple(...) around it."
        )
    try:
        entries = tuple(value)
    except TypeError:
        raise RestlyConfigurationError(
            f"{label} must be a tuple of keys, got {value!r}."
        ) from None
    for entry in entries:
        if not isinstance(entry, str) or not entry:
            raise RestlyConfigurationError(
                f"{label} must hold keys as strings, got {entry!r}."
            )
        if "**" in entry:
            raise RestlyConfigurationError(
                f"{label}: {entry!r} uses '**'. A key pattern has one "
                "wildcard, '*', for any characters except '.'. Write one "
                "entry for each level, such as ('vendor.*', 'vendor.*.*')."
            )
        if any(char in entry for char in "?[]"):
            raise RestlyConfigurationError(
                f"{label}: {entry!r} uses '?' or '[...]'. A key pattern "
                "has one wildcard, '*', for any characters except '.'."
            )
    return entries


@functools.cache
def _pattern_regex(pattern: str) -> "re.Pattern[str]":
    """A regular expression for a key pattern, for ``fullmatch``.

    ``*`` matches any characters except ``.``. Every other character, ``.``
    too, matches only itself.
    """
    return re.compile("[^.]*".join(re.escape(part) for part in pattern.split("*")))


def _matches(patterns: tuple[str, ...], key: str) -> bool:
    return any(_pattern_regex(pattern).fullmatch(key) for pattern in patterns)


def _pick_keys(
    keys: list[str], include: tuple[str, ...], exclude: tuple[str, ...]
) -> list[str]:
    """The keys that ``include`` matches and ``exclude`` does not, in order."""
    return [
        key for key in keys if _matches(include, key) and not _matches(exclude, key)
    ]


def _query_settings(pagination: _AnyPagination) -> tuple[Any, ...] | None:
    """What the pagination fields of list params depend on; ``None`` for
    no pagination."""
    if not isinstance(pagination, NumberedPagination):
        return None
    return (
        pagination.page_query_param,
        pagination.page_size_query_param,
        pagination.default_page_size,
        pagination.max_page_size,
        pagination.max_page,
    )


#: Largest pagination offset accepted by supported SQL databases. SQLite and
#: PostgreSQL bind ``OFFSET`` as a signed 64-bit integer.
_MAX_PAGINATION_OFFSET = 2**63 - 1


def _reserved_names(
    pagination: _AnyPagination, sort_query_param: str = _SORT_QUERY_PARAM
) -> frozenset[str]:
    """Query parameters the schema produces besides filters.

    A filter column whose public name is one of these would shadow pagination
    or sort, which would silently break the endpoint contract. Treated as a
    hard error.
    """
    names = {sort_query_param}
    if isinstance(pagination, NumberedPagination):
        names |= {pagination.page_query_param, pagination.page_size_query_param}
    return frozenset(names)


# Types that support SQL ``<``/``<=``/``>``/``>=`` comparisons. Booleans are
# excluded: ordering booleans is rarely meaningful, and ``WHERE active >= true``
# raises ``sqlalchemy.exc.ArgumentError`` at query time, which would otherwise
# surface to the client as a 500.
_ORDERABLE_TYPES: tuple[type, ...] = (
    int,
    float,
    _decimal.Decimal,
    _dt.date,
    _dt.datetime,
    _dt.time,
    _dt.timedelta,
    str,
)


def _is_string_field(field: FieldInfo) -> bool:
    annotation, _ = _unwrap_type(field.annotation)
    return annotation is str


def _is_idref_field(field: FieldInfo) -> bool:
    """True for a scalar ``IDRef[T]`` FK field (e.g. ``post_id: IDRef[Post]``).

    That ``*_id``-named spelling is discouraged -- the misuse lint steers it to
    ``fr.MustExist[int, Post]`` -- but the query layer still handles it here so
    the field keeps its filter param.

    ``IDRef`` is a ``BaseModel`` subclass, so without this it would be recursed
    into as a nested schema and yield only ``post_id.id`` -- which never resolves
    (``post_id`` is a scalar column, not a relationship), leaving the FK with no
    filter param at all. Treated as a leaf, it filters on its own public name.
    Targets ``IDRef`` specifically, not ``IDSchema``: a nested *resource* schema
    that embeds its ``id`` also subclasses ``IDSchema`` and must keep its dotted
    traversal. ``list[IDRef[T]]`` (to-many) is unaffected -- its annotation is a
    list, so it is never a nested schema here.
    """
    annotation, _ = _unwrap_type(field.annotation)
    return isinstance(annotation, type) and issubclass(annotation, IDRef)


def _supports_scalar_operators(field: FieldInfo) -> bool:
    """False for collection-typed fields (``dict``, ``list[str]``, ``Sequence``,
    ...) that no query-string value can coerce into: ``eq``/``__in``/``__ne``
    (and range) filters on such a field would always 400 at request time, so
    only ``__isnull`` is generated -- the same advertise-only-what-works
    principle applied to non-column fields.

    The check mirrors what the request path's ``_parse_value`` (full Pydantic
    field validation) will accept. A ``pydantic.Json[...]`` field counts as
    scalar: its validation parses the query string as JSON, so its filters
    work. ``str``/``bytes`` are collections in the abc sense but coerce fine,
    so they stay scalar.
    """
    annotation, metadata = _unwrap_type(field.annotation)
    metadata = [*field.metadata, *metadata]
    # ``pydantic.Json`` is a real class at runtime; the type stubs model it as
    # an ``Annotated`` factory, hence the cast.
    json_marker = cast(type, pydantic.Json)
    if any(isinstance(item, json_marker) for item in metadata):
        return True
    origin = get_origin(annotation) or annotation
    return not (
        isinstance(origin, type)
        and issubclass(origin, _abc.Collection)
        and not issubclass(origin, (str, bytes))
    )


def _supports_range_operators(field: FieldInfo) -> bool:
    annotation, _ = _unwrap_type(field.annotation)
    if annotation is bool:
        return False
    # ``Any`` is a class from Python 3.11 on; it is still an unknown type.
    if annotation is Any or not isinstance(annotation, type):
        return True
    if issubclass(annotation, bool):
        return False
    if issubclass(annotation, enum.Enum):
        # SQLAlchemy's Enum stores the names of the members, so a range would
        # compare names; Restly sees only the Python type, not the column
        return False
    if issubclass(annotation, _ORDERABLE_TYPES):
        return True
    if issubclass(annotation, _uuid.UUID):
        return False
    return False


def _pagination_offset_validator(pagination: NumberedPagination) -> Any:
    """A model validator that rejects a page whose SQL offset would not fit in
    a signed 64-bit integer."""
    page_param = pagination.page_query_param
    page_size_param = pagination.page_size_query_param

    @pydantic.model_validator(mode="after")
    def _validate_pagination_offset(params: pydantic.BaseModel) -> pydantic.BaseModel:
        page = cast(int, getattr(params, page_param))
        page_size = cast(Optional[int], getattr(params, page_size_param))
        if page_size is None:
            return params
        if page - 1 > _MAX_PAGINATION_OFFSET // page_size:
            raise ValueError(
                f"{page_param} and {page_size_param} produce an offset above "
                f"{_MAX_PAGINATION_OFFSET}"
            )
        return params

    return _validate_pagination_offset


def derive_schema_list_params(
    schema_response: SchemaType,
    model: type[DeclarativeBase],
    *,
    pagination: _AnyPagination = _DEFAULT_PAGINATION,
    include_filters: _abc.Iterable[str] = ("*",),
    exclude_filters: _abc.Iterable[str] = (),
    include_sort_keys: _abc.Iterable[str] = ("*",),
    exclude_sort_keys: _abc.Iterable[str] = (),
    sort_query_param: str = "sort",
    default_sort: str | None = None,
) -> SchemaType:
    """
    Build the list params: a Pydantic model that describes and validates the
    URL query parameters of a list endpoint. A view keeps it in
    :attr:`~fastapi_restly.views.BaseRestView.schema_list_params`.

    The generated model accepts pagination (``page``, ``page_size``), sorting
    (``sort``), and filters. Each field of ``schema_response`` that maps to a
    column of ``model`` gives filter keys, and its type decides which ones.
    Every such field gives the plain key and the keys with the suffixes
    ``__in``, ``__ne`` and ``__isnull``. A type with an order, such as a
    number, text or a date, also gives ``__gte``, ``__lte``, ``__gt`` and
    ``__lt``. Text also gives ``__contains`` and ``__icontains``. A field
    that does not map to a column, such as a relationship, gives no keys. A
    WriteOnly field gives none either, because a filter on it would let a
    client find out its value. A field whose type is a collection (``dict``/``list``, e.g. a
    ``JSON`` or ``ARRAY`` column) gives only ``__isnull``: a value from the
    URL cannot become a collection. A field of a nested schema gives dotted
    keys, such as ``vendor.name``.

    The four key settings choose which of these keys the model has. Each
    entry is a key as the client sends it, or a pattern. ``*`` in a pattern
    matches any characters except ``.``, so it stays within one level.
    By default the model has the keys of the resource's own fields, and no
    dotted keys. An entry that matches no key raises an error.

    With a ``pagination`` (the default is ``page`` and ``page_size``), its two
    query parameters are added under its names and validated by Pydantic with
    bounds: the page at least 1 and at most ``max_page`` when set, the page
    size from 1 to ``max_page_size``. The resulting SQL offset must also fit in
    a signed 64-bit integer. Out-of-range values produce a standard 422
    response from FastAPI. With a :class:`~fastapi_restly.views.NoPagination`
    or ``None``, no pagination parameters are emitted at all -- the endpoint
    returns every matching row -- while sorting and filtering stay available.

    :param schema_response: The response class whose fields decide the
        available filter parameters. A view passes its ``schema_response``, so
        a client can filter only on what it can see.
    :param model: The SQLAlchemy model the list endpoint queries. Used to verify
        each field resolves to a filterable column; non-column fields are
        omitted from the generated params.
    :param pagination: The view's pagination settings. A ``NoPagination`` or
        ``None`` is an unpaginated view, which returns the full result set.
    :param include_filters: The filter keys to keep. ``("*", "*.*")`` keeps
        the own keys and the dotted keys one level down, and ``()`` keeps
        none. Each entry must match a key, so ``"*.*"`` needs a nested
        schema.
    :param exclude_filters: The filter keys to remove, after
        ``include_filters``.
    :param include_sort_keys: The keys that ``sort`` accepts. A sort key has
        no suffix, and never goes through a to-many relationship. With
        ``()``, the model has no ``sort``.
    :param exclude_sort_keys: The sort keys to remove, after
        ``include_sort_keys``.
    :param sort_query_param: The name of the sort parameter, such as
        ``"order_by"``.
    :param default_sort: The sort when the request has none, such as
        ``"-created_at"``. It becomes the default of the sort parameter. A
        key must be a field that a sort key could have, but
        ``include_sort_keys`` does not have to keep it.
    :raises fastapi_restly.exc.RestlyConfigurationError: An entry of a key
        setting matches no key, or is not a key or pattern. The sort
        parameter has the name of a page parameter. A key of
        ``default_sort`` is not a field to sort on, or appears twice.
    """
    return _derive_list_params(
        schema_response,
        model,
        pagination=pagination,
        settings={
            "include_filters": include_filters,
            "exclude_filters": exclude_filters,
            "include_sort_keys": include_sort_keys,
            "exclude_sort_keys": exclude_sort_keys,
        },
        sort_query_param=sort_query_param,
        default_sort=default_sort,
    )


def _derive_list_params(
    schema_response: SchemaType,
    model: type[DeclarativeBase],
    *,
    pagination: _AnyPagination,
    settings: _abc.Mapping[str, Any],
    owner: str | None = None,
    unchecked: _abc.Set[str] = frozenset(),
    own_filters: _abc.Sequence[_OwnFilter] = (),
    sort_query_param: str = _SORT_QUERY_PARAM,
    default_sort: str | None = None,
    default_sort_from: str | None = None,
) -> SchemaType:
    """:func:`derive_schema_list_params`, for a view too.

    :param owner: The view, for the messages.
    :param unchecked: The key settings whose entries may match no key: the
        ones that a view inherits. A base view serves many schemas.
    :param own_filters: The view's own filter keys, which are always on.
    :param default_sort_from: The base view that ``default_sort`` comes
        from, for the messages. It is checked too: an order that does not
        apply would change the list without a sign.
    """
    patterns = {
        name: _check_key_setting(_setting_label(owner, name), settings[name])
        for name in _KEY_SETTINGS
    }
    _check_query_param(_setting_label(owner, "sort_query_param"), sort_query_param)
    _check_default_sort(_setting_label(owner, "default_sort"), default_sort)
    if isinstance(pagination, NumberedPagination) and sort_query_param in (
        pagination.page_query_param,
        pagination.page_size_query_param,
    ):
        raise RestlyConfigurationError(
            f"{_setting_label(owner, 'sort_query_param')} cannot be "
            f"{sort_query_param!r}: it is a page parameter of the pagination. "
            "Choose another name for one of them."
        )
    fields: dict[str, Any] = {}
    if isinstance(pagination, NumberedPagination):
        page_description = "1-based page number."
        if pagination.max_page is not None:
            page_description += f" At most {pagination.max_page}."
        fields[pagination.page_query_param] = (
            Annotated[
                int, Field(ge=1, le=pagination.max_page, description=page_description)
            ],
            1,
        )
        fields[pagination.page_size_query_param] = (
            Annotated[
                Optional[int],
                Field(
                    ge=1,
                    le=pagination.max_page_size,
                    description=(
                        f"Number of items per page (1–{pagination.max_page_size}). "
                        f"Defaults to {pagination.default_page_size}."
                    ),
                ),
            ],
            pagination.default_page_size,
        )

    reserved = _reserved_names(pagination, sort_query_param)
    filter_fields: dict[str, Any] = {}
    # The filter keys of each field, for the messages.
    keys_of_field: dict[str, list[str]] = {}
    sort_keys: list[str] = []
    for name, field in _iter_fields_including_nested(schema_response):
        # Only emit params for fields that resolve to a filterable column on the
        # model -- using the very predicate the request path applies. A
        # relationship/collection field (e.g. ``books: list[BookRef]``) or a
        # reference traversal that does not resolve would otherwise advertise
        # filters in OpenAPI that always 400 at request time.
        try:
            relationships, _ = _resolve_column(model, name, schema_response)
        except BadQueryParam:
            continue
        # ``sort=tags.name`` has no clear meaning when an item has several
        # tags, and the join repeats rows.
        if not any(relationship.property.uselist for relationship in relationships):
            sort_keys.append(name)
        field_filters = _filter_fields(name, field)
        filter_fields.update(field_filters)
        keys_of_field[name] = list(field_filters)

    schema_name = _written_name(schema_response)
    for name in ("include_filters", "exclude_filters"):
        if name not in unchecked and patterns[name] != _KEY_SETTING_DEFAULTS[name]:
            _check_filter_entries(
                _setting_label(owner, name), patterns[name], keys_of_field, schema_name
            )
    for name in ("include_sort_keys", "exclude_sort_keys"):
        if name not in unchecked and patterns[name] != _KEY_SETTING_DEFAULTS[name]:
            _check_sort_entries(
                _setting_label(owner, name), patterns[name], sort_keys, schema_name
            )

    label = _setting_label(owner, "default_sort")
    default_sort_keys = _default_sort_keys(
        f"{label} (from {default_sort_from})" if default_sort_from else label,
        default_sort,
        sort_keys,
        schema_name,
        hint=(
            f" Set {label} = None for primary key order." if default_sort_from else ""
        ),
    )
    sort_keys = _pick_keys(
        sort_keys, patterns["include_sort_keys"], patterns["exclude_sort_keys"]
    )
    if sort_keys:
        fields[sort_query_param] = _sort_field(sort_keys, default_sort)
    for key in _pick_keys(
        list(filter_fields), patterns["include_filters"], patterns["exclude_filters"]
    ):
        if key in reserved:
            raise ValueError(
                f"List params for {schema_name!r} cannot expose the filter "
                f"key {key!r}: it collides with a reserved pagination/sort "
                "parameter. Add a Pydantic alias to the field, rename the "
                f"page or sort parameter, or leave the key out with "
                f"exclude_filters = ({key!r},)."
            )
        fields[key] = filter_fields[key]
    for own in own_filters:
        _check_own_key(
            _setting_label(owner, own.method),
            own.key,
            fields,
            reserved,
            keys_of_field,
            schema_name,
        )
        annotation = own.annotation if own.always else Optional[own.annotation]
        default = own.default if own.always else None
        fields[own.key] = (
            Annotated[annotation, Field(description=own.description)],
            default,
        )

    validators = (
        {"_validate_pagination_offset": _pagination_offset_validator(pagination)}
        if isinstance(pagination, NumberedPagination)
        else {}
    )
    params_model = pydantic.create_model(  # type: ignore[call-overload]
        _schema_role_name(schema_response, "ListParams"),
        __validators__=validators,
        **fields,
    )
    _RECORDS[params_model] = _ListParamsRecord(
        pagination=pagination,
        **patterns,
        sort_query_param=sort_query_param,
        default_sort=default_sort,
        default_sort_keys=default_sort_keys,
        sort_keys=tuple(sort_keys),
        own_filters=tuple(own_filters),
    )
    return params_model


def _default_sort_keys(
    label: str,
    default_sort: str | None,
    sort_keys: list[str],
    schema_name: str,
    *,
    hint: str = "",
) -> tuple[str, ...]:
    """The keys of ``default_sort``.

    :param sort_keys: Every key that the schema can sort on, before the key
        settings choose.
    :param hint: The end of the message.
    :raises RestlyConfigurationError: a key is not in ``sort_keys``.
    """
    if default_sort is None:
        return ()
    keys = tuple(term.removeprefix("-") for term in default_sort.split(","))
    for key in keys:
        if key in sort_keys:
            continue
        message = (
            f"{label}: {key!r} is not a field of {schema_name} to sort on. "
            "A key is a field as the client writes it in the sort parameter."
        )
        if sort_keys:
            message += f" The fields to sort on are: {', '.join(sort_keys)}."
        else:
            message += f" {schema_name} has no fields to sort on."
        raise RestlyConfigurationError(message + hint)
    return keys


def _check_own_key(
    label: str,
    key: str,
    fields: dict[str, Any],
    reserved: _abc.Set[str],
    keys_of_field: dict[str, list[str]],
    schema_name: str,
) -> None:
    """Reject an own filter key that the list params already have, or that
    does not read as the generated keys do.

    :param keys_of_field: The generated filter keys of each field, also the
        keys that are off.

    :raises RestlyConfigurationError: the key is a page parameter or the
        sort parameter, or a generated filter key that is on, or it has
        ``__`` or ``.`` and does not start with a field with filters.
    """
    if key in reserved:
        raise RestlyConfigurationError(
            f"{label}: the key {key!r} is a page parameter or the sort "
            "parameter. Choose another key."
        )
    # ``__`` splits a field from an operator, and ``.`` walks to a related
    # field, in every key
    field = key.partition("__")[0]
    if ("__" in key or "." in key) and field not in keys_of_field:
        message = (
            f"{label}: the key {key!r} starts with {field!r}, which is not a "
            f"field of {schema_name} with filters. A key with '__' or '.' "
            "starts with a field, as the generated keys do. Or choose a key "
            "without '__' and '.'."
        )
        if keys_of_field:
            message += f" The fields with filters are: {', '.join(keys_of_field)}."
        else:
            message += f" {schema_name} has no fields with filters."
        raise RestlyConfigurationError(message)
    if key in fields:
        message = (
            f"{label}: the key {key!r} is also a generated filter key. To "
            f"replace it, leave it out with exclude_filters = ({key!r},)."
        )
        if "__" not in key:
            message += (
                f" Then choose what happens to {key}__in and the other keys "
                "of the field."
            )
        raise RestlyConfigurationError(message)


def _setting_label(owner: str | None, name: str) -> str:
    return f"{owner}.{name}" if owner else name


def _filter_fields(name: str, field: FieldInfo) -> dict[str, Any]:
    """The filter keys of one field, as fields of the list params model."""
    fields: dict[str, Any] = {}
    # Type filter parameters as ``Optional[list[str]]`` instead of the
    # column's true type) so FastAPI/Starlette preserve repeated query
    # parameters as a list and downstream ``_parse_value`` can perform
    # field-type coercion. ``__isnull`` stays a scalar bool because
    # repeating it makes no sense.
    # Collection/JSON-typed fields (``dict``, ``list[str]``) resolve to
    # real columns, but no query-string value can coerce into them, so
    # every operator except ``__isnull`` would 400 at request time.
    supports_scalar = _supports_scalar_operators(field)
    if supports_scalar:
        eq_desc = (
            f"Filter by ``{name}``. Comma-separated values are OR-combined "
            "(SQL ``IN``). Repeat the parameter to AND multiple predicates."
        )
        ne_desc = (
            f"Exclude rows where ``{name}`` matches. Comma-separated values "
            "are AND-combined (SQL ``NOT IN``)."
        )
        in_desc = (
            f"Filter by ``{name}`` with explicit SQL ``IN`` semantics. "
            "Provide comma-separated values."
        )
        fields[name] = (
            Annotated[Optional[list[str]], Field(description=eq_desc)],
            None,
        )
        fields[f"{name}__in"] = (
            Annotated[Optional[list[str]], Field(description=in_desc)],
            None,
        )
        fields[f"{name}__ne"] = (
            Annotated[Optional[list[str]], Field(description=ne_desc)],
            None,
        )
    fields[f"{name}__isnull"] = (
        Annotated[
            Optional[bool],
            Field(
                description=(
                    f"``true`` matches rows where ``{name}`` IS NULL; "
                    f"``false`` matches IS NOT NULL."
                )
            ),
        ],
        None,
    )

    if supports_scalar and _supports_range_operators(field):
        for suffix, sql in (
            ("__gte", ">="),
            ("__lte", "<="),
            ("__gt", ">"),
            ("__lt", "<"),
        ):
            fields[f"{name}{suffix}"] = (
                Annotated[
                    Optional[list[str]], Field(description=f"``{name} {sql} value``.")
                ],
                None,
            )

    if _is_string_field(field):
        fields[f"{name}__contains"] = (
            Annotated[
                Optional[list[str]],
                Field(
                    description=(
                        f"Case-sensitive substring search on "
                        f"``{name}``. Repeat the parameter to AND "
                        "multiple terms; whitespace inside one value is "
                        "also AND-split as a convenience."
                    )
                ),
            ],
            None,
        )
        fields[f"{name}__icontains"] = (
            Annotated[
                Optional[list[str]],
                Field(
                    description=(
                        f"Case-insensitive substring search on "
                        f"``{name}``. Repeat the parameter to AND "
                        "multiple terms; whitespace inside one value is "
                        "also AND-split as a convenience."
                    )
                ),
            ],
            None,
        )
    return fields


#: Why a field has no key with this suffix, for the messages.
_SUFFIX_HINTS = {
    "contains": "Only text fields have __contains and __icontains.",
    "icontains": "Only text fields have __contains and __icontains.",
    **dict.fromkeys(
        ("gte", "lte", "gt", "lt"),
        "Only fields with an order, such as numbers, text and dates, have "
        "__gte, __lte, __gt and __lt.",
    ),
    **dict.fromkeys(
        ("", "in", "ne"), "A field that holds a dict or a list has only __isnull."
    ),
}


def _check_filter_entries(
    label: str,
    entries: tuple[str, ...],
    keys_of_field: dict[str, list[str]],
    schema_name: str,
) -> None:
    """Raise for the first entry that matches no filter key.

    The message lists the keys of the field that the entry names, or the
    fields that have filters.
    """
    keys = [key for field_keys in keys_of_field.values() for key in field_keys]
    for entry in entries:
        if any(_pattern_regex(entry).fullmatch(key) for key in keys):
            continue
        message = f"{label}: {entry!r} matches no filter key of {schema_name}."
        field, _, suffix = entry.partition("__")
        if "*" not in field and field in keys_of_field:
            field_keys = ", ".join(keys_of_field[field])
            message += f" The filter keys of {field!r} are: {field_keys}."
            if suffix in _SUFFIX_HINTS:
                message += f" {_SUFFIX_HINTS[suffix]}"
        elif "*" not in entry:
            if keys_of_field:
                message += f" The fields with filters are: {', '.join(keys_of_field)}."
            else:
                message += f" {schema_name} has no fields with filters."
        raise RestlyConfigurationError(message)


def _check_sort_entries(
    label: str, entries: tuple[str, ...], sort_keys: list[str], schema_name: str
) -> None:
    """Raise for the first entry that matches no sort key."""
    for entry in entries:
        if any(_pattern_regex(entry).fullmatch(key) for key in sort_keys):
            continue
        message = f"{label}: {entry!r} matches no sort key of {schema_name}."
        if "__" in entry:
            message += " A sort key has no suffix."
        if sort_keys:
            message += f" The sort keys are: {', '.join(sort_keys)}."
        else:
            message += f" {schema_name} has no sort keys."
        raise RestlyConfigurationError(message)


def _sort_field(sort_keys: list[str], default: str | None) -> tuple[Any, str | None]:
    """The ``sort`` field of a list params model, which accepts ``sort_keys``.

    :param default: The sort when the request has none. It does not have to
        be in ``sort_keys``.
    """
    allowed = frozenset(sort_keys)

    def check_sort_keys(value: str | None) -> str | None:
        if value == default:
            # FastAPI validates the default that it fills in
            return value
        keys = [term.removeprefix("-") for term in (value or "").split(",") if term]
        for key in keys:
            if key not in allowed:
                raise PydanticCustomError(
                    "unknown_sort_key", "Unknown sort key '{key}'", {"key": key}
                )
        repeated = _repeated_key(keys)
        if repeated is not None:
            raise PydanticCustomError(
                "repeated_sort_key",
                "Sort key '{key}' appears more than once",
                {"key": repeated},
            )
        return value

    example = ",".join([f"-{sort_keys[0]}", *sort_keys[1:2]])
    keys = ", ".join(f"``{key}``" for key in sort_keys)
    description = (
        "Keys to sort by, separated by commas. A ``-`` before a key sorts in "
        f"descending order. Use each key only once. Example: ``{example}``. "
        f"The sort keys are: {keys}."
    )
    return (
        Annotated[
            Optional[str],
            Field(description=description),
            pydantic.AfterValidator(check_sort_keys),
        ],
        default,
    )


def apply_list_params(
    stmt: Select[Any],
    list_params: pydantic.BaseModel | QueryParams,
    model: type[DeclarativeBase],
    schema_response: SchemaType,
    *,
    pagination: _AnyPagination = cast(Any, _FROM_PARAMS),
    sort_nulls: SortNulls = "last",
) -> Select[Any]:
    """
    Apply pagination, sorting, and filtering to ``stmt`` using validated
    list params.

    ``list_params`` is normally an instance of the model returned by
    :func:`derive_schema_list_params`. The default list endpoints pass a
    validated instance, so the page and the page size are within their
    bounds. This function still checks each filter value itself, with the
    validation of its field.
    ``schema_response`` decides the fields a client can filter and sort on.
    A view passes its response class.

    The arguments match the view method
    :meth:`~fastapi_restly.views.RestView.apply_list_params`, which calls this
    function with the view's model, response class and pagination.

    With list params from :func:`derive_schema_list_params`, the sort
    parameter has the name that the model was created with, and accepts only
    its sort keys. With raw ``QueryParams`` or a hand-written model, the sort
    parameter is ``sort``. List params
    with a value for a view's own filter key, from
    :func:`~fastapi_restly.views.list_filter`, raise ``ValueError``: only the
    view can apply them. Use the view's ``apply_list_params``.

    A raw :class:`~starlette.datastructures.QueryParams` is also accepted
    for callers that build the query parameters programmatically. It skips
    the list params model, so nothing checks the ``page`` and ``page_size``
    bounds, such as ``max_page_size``: the caller checks them. Each filter
    value still goes through the validation of its field. Every filter key
    that :func:`derive_schema_list_params` can generate works, also a dotted
    one.

    ``pagination`` names the page and page-size parameters to read. By default
    it is the pagination that :func:`derive_schema_list_params` created the
    list params model with, and ``page`` and ``page_size`` for raw
    ``QueryParams`` or a hand-written model. A ``NoPagination`` or ``None``
    applies no ``LIMIT``/``OFFSET``, and so does a missing page-size key.

    ``sort_nulls`` says where the sort puts NULLs: ``"last"`` (the default)
    or ``"first"`` in both directions, or ``None`` for where the database
    puts them. Without a sort parameter, the sort is the ``default_sort``
    that the list params were created with.

    Examples::

        # Pagination
        page=2&page_size=50

        # Sorting
        sort=name,-created_at

        # Filtering
        name=Bob&status=active&created_at__gte=2024-01-01

        # Contains (string fields)
        name__contains=John&email__icontains=example
    """
    if sort_nulls not in ("first", "last", None):
        raise ValueError(
            f"sort_nulls must be 'first', 'last' or None, got {sort_nulls!r}."
        )
    _reject_own_filters(list_params)
    stmt = _apply_filters(
        stmt, list_params, model, schema_response, pagination=pagination
    )
    return _apply_sort_and_page(
        stmt,
        list_params,
        model,
        schema_response,
        pagination=pagination,
        sort_nulls=sort_nulls,
    )


#: The joins that the filters added to a statement, by the path of
#: relationships. The sort reuses them, so a filter and a sort on the same
#: relationship share one join. A statement that changes after the filters
#: is another object, and its sort joins again.
_STATEMENT_JOINS: "weakref.WeakKeyDictionary[Select[Any], _JoinAliases]" = (
    weakref.WeakKeyDictionary()
)


def _read_list_params(
    list_params: pydantic.BaseModel | QueryParams, pagination: Any
) -> tuple[_ListParamsRecord | None, _AnyPagination, str, QueryParams]:
    """The record of ``list_params``, the pagination to apply, the name of
    the sort parameter, and the list params as query parameters.

    Without a record, as for hand-written list params, the sort parameter is
    ``sort``.

    :raises RestlyConfigurationError: a page parameter has the name of the
        sort parameter.
    """
    record = (
        _record_of(type(list_params))
        if isinstance(list_params, pydantic.BaseModel)
        else None
    )
    if isinstance(pagination, _FromParams):
        pagination = record.pagination if record else _DEFAULT_PAGINATION
    sort_query_param = record.sort_query_param if record else _SORT_QUERY_PARAM
    if isinstance(pagination, NumberedPagination) and sort_query_param in (
        pagination.page_query_param,
        pagination.page_size_query_param,
    ):
        # list params from derive_schema_list_params reject this when created
        raise RestlyConfigurationError(
            f"A page parameter of the pagination is {sort_query_param!r}, "
            "which is also the sort parameter of these list params. Give the "
            "page parameter another name."
        )
    own_keys = {own.key for own in record.own_filters} if record else set()
    query_params = _coerce_to_query_params(list_params, exclude=own_keys)
    return record, pagination, sort_query_param, query_params


def _keep_joins(source: Select[Any], stmt: Select[Any]) -> Select[Any]:
    """``stmt``, which adds only conditions to ``source``, with the joins
    that the filters added to ``source``."""
    joins = _STATEMENT_JOINS.get(source)
    if joins:
        _STATEMENT_JOINS[stmt] = joins
    return stmt


def _reject_own_filters(list_params: pydantic.BaseModel | QueryParams) -> None:
    """Reject list params with a value for a view's own filter key.

    Only the view can call its filter methods. Leaving the key out would
    return more rows than the client asked for.

    :raises ValueError: an own filter has a value.
    """
    if not isinstance(list_params, pydantic.BaseModel):
        return
    record = _record_of(type(list_params))
    if record is None:
        return
    applied = record.own_filter_values(list_params)
    if applied:
        own, _ = applied[0]
        raise ValueError(
            f"apply_list_params cannot apply the key {own.key!r}: it is a "
            f"filter of the view's own, from the method {own.method}. Call "
            "apply_list_params on the view instead."
        )


def _apply_filters(
    stmt: Select[Any],
    list_params: pydantic.BaseModel | QueryParams,
    model: type[DeclarativeBase],
    schema_response: SchemaType,
    *,
    pagination: _AnyPagination = cast(Any, _FROM_PARAMS),
) -> Select[Any]:
    """The first step of :func:`apply_list_params`: the generated filters."""
    _, pagination, sort_query_param, query_params = _read_list_params(
        list_params, pagination
    )
    aliases: _JoinAliases = {}
    stmt = _apply_filtering(
        query_params,
        stmt,
        model,
        schema_response,
        aliases=aliases,
        pagination=pagination,
        sort_query_param=sort_query_param,
    )
    if aliases:
        _STATEMENT_JOINS[stmt] = aliases
    return stmt


def _apply_sort_and_page(
    stmt: Select[Any],
    list_params: pydantic.BaseModel | QueryParams,
    model: type[DeclarativeBase],
    schema_response: SchemaType,
    *,
    pagination: _AnyPagination = cast(Any, _FROM_PARAMS),
    sort_nulls: SortNulls = "last",
) -> Select[Any]:
    """The last steps of :func:`apply_list_params`: the sort, then the page."""
    record, pagination, sort_query_param, query_params = _read_list_params(
        list_params, pagination
    )
    stmt = _apply_sorting(
        query_params,
        stmt,
        model,
        schema_response,
        aliases=dict(_STATEMENT_JOINS.get(stmt, {})),
        # the default is the value of the sort field, also with a key that
        # the client cannot send
        sort_keys=record.sort_keys + record.default_sort_keys if record else None,
        sort_query_param=sort_query_param,
        default_sort=record.default_sort if record else None,
        sort_nulls=sort_nulls,
    )
    if isinstance(pagination, NumberedPagination):
        stmt = _apply_pagination(query_params, stmt, pagination)
    return stmt


def _coerce_to_query_params(
    list_params: pydantic.BaseModel | QueryParams,
    *,
    exclude: _abc.Set[str] = frozenset(),
) -> QueryParams:
    """Normalise validated list params or raw QueryParams to QueryParams.

    When a dumped field is a list (e.g. a repeated ``name__contains``), each
    element is expanded to its own ``(key, value)`` tuple so that
    ``QueryParams.multi_items()`` later returns the original repeated values.

    :param exclude: The fields of a model to leave out.
    """
    if isinstance(list_params, QueryParams):
        return list_params
    if isinstance(list_params, pydantic.BaseModel):
        dumped = list_params.model_dump(
            exclude_none=True, by_alias=True, mode="json", exclude=set(exclude)
        )
        items: list[tuple[str, str]] = []
        for key, value in dumped.items():
            if isinstance(value, list):
                items.extend((key, str(item)) for item in value)
            else:
                items.append((key, str(value)))
        return QueryParams(items)
    return QueryParams(list_params)


def _apply_pagination(
    query_params: QueryParams, stmt: Select[Any], pagination: NumberedPagination
) -> Select[Any]:
    page_size = _get_int(query_params, pagination.page_size_query_param)
    if page_size is None:
        return stmt
    page = _get_int(query_params, pagination.page_query_param) or 1
    offset = (page - 1) * page_size
    return stmt.limit(page_size).offset(offset)


def _get_int(query_params: QueryParams, param_name: str) -> Optional[int]:
    value = query_params.get(param_name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise BadQueryParam(
            f"Invalid value for {param_name!r}: {value!r}. Use an integer."
        )


def _apply_sorting(
    query_params: QueryParams,
    stmt: Select[Any],
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
    *,
    aliases: _JoinAliases | None = None,
    sort_keys: tuple[str, ...] | None = None,
    sort_query_param: str = _SORT_QUERY_PARAM,
    default_sort: str | None = None,
    sort_nulls: SortNulls = "last",
) -> Select[Any]:
    """Apply the sort parameter to ``stmt``, with the primary key
    last.

    :param sort_keys: The keys that ``sort`` may use. ``None`` allows every
        key that resolves to a column, as for hand-written list params.
    :param default_sort: The sort when the sort parameter has no value.
    :param sort_nulls: Where the sort puts NULLs.
    :raises BadQueryParam: ``Unknown sort key``, or a key that appears more
        than once.
    """
    if aliases is None:
        aliases = {}
    sorted_on: list[InstrumentedAttribute[Any]] = []
    sort = query_params.get(sort_query_param) or ""
    if not sort.strip(","):
        # also a sort of only commas, which has no keys
        sort = default_sort or ""
    seen: set[str] = set()
    for column_name in sort.split(","):
        if not column_name:
            continue
        descending = column_name.startswith("-")
        column_name = column_name.removeprefix("-")
        # The model checked ``sort``, but ``model_copy(update=...)`` does
        # not validate.
        if sort_keys is not None and column_name not in sort_keys:
            raise BadQueryParam(f"Unknown sort key: {column_name!r}")
        if column_name in seen:
            raise BadQueryParam(f"Sort key {column_name!r} appears more than once")
        seen.add(column_name)
        stmt, column = _join_column(
            stmt, model, column_name, schema_cls, aliases, kind="sort"
        )
        stmt = stmt.order_by(
            _sort_term(
                column,
                descending=descending,
                nulls=sort_nulls,
                joined="." in column_name,
            )
        )
        sorted_on.append(column)
    return _append_pk_tiebreak(stmt, model, sorted_on)


def _written_name(schema_cls: SchemaType) -> str:
    """The name of the class that the user wrote: for a response class that
    Restly derived, the view's schema."""
    return schema_cls.__dict__.get("__restly_response_of__", schema_cls).__name__


def _iter_fields_including_nested(
    schema_cls: SchemaType,
    prefix: str = "",
    _followed: frozenset[SchemaType] = frozenset(),
) -> Iterator[tuple[str, FieldInfo]]:
    for name, field in schema_cls.model_fields.items():
        # A WriteOnly field, or one with ``exclude=True``, never goes out,
        # so it is never a filter or sort key.
        if field.exclude is True:
            continue
        public_name = field.alias or name
        # Each segment of the public dotted path becomes part of a key of
        # the list params. ``__`` is reserved for operator suffixes (``__gte``,
        # ``__contains``, ...) and ``.`` is reserved for relation traversal,
        # so a segment containing either character would create an
        # ambiguous URL key. Reject at schema-generation time so the
        # collision surfaces during view registration, not at request time.
        if "__" in public_name:
            raise ValueError(
                f"List params for {_written_name(schema_cls)!r} cannot "
                f"expose field {public_name!r}: ``__`` is reserved for "
                "operator suffixes. Choose a different Pydantic alias."
            )
        if "." in public_name:
            raise ValueError(
                f"List params for {_written_name(schema_cls)!r} cannot "
                f"expose field {public_name!r}: ``.`` is reserved for "
                "relation traversal. Choose a different Pydantic alias."
            )
        full_name = f"{prefix}.{public_name}" if prefix else public_name
        nested = _get_nested_schema(field)
        if nested and not _is_idref_field(field):
            # Follow each nested schema once in a path. A schema that names
            # itself, such as ``parent: CategorySchema | None``, then gets
            # ``parent.name`` but not ``parent.parent.name``, and the walk ends.
            if nested not in _followed:
                yield from _iter_fields_including_nested(
                    nested, full_name, _followed | {nested}
                )
        else:
            yield full_name, field


def _resolve_field_name(schema_cls: SchemaType, public_name: str) -> str | None:
    """Return the Python field name for a public URL field name.

    The public name is the field's alias when one is declared, otherwise the
    field name itself. Aliased fields are *only* reachable by their alias —
    Python field names are never part of the public URL contract, even when
    the schema has ``populate_by_name=True`` (which only affects how Pydantic
    parses input bodies, not the generated list params).

    A field that never goes out, WriteOnly or with ``exclude=True``, never
    resolves: filtering or sorting on it would let a client read back a value
    that responses leave out.
    """
    resolved: str | None = None
    for field_name, field in schema_cls.model_fields.items():
        if field.alias == public_name:
            resolved = field_name
            break
    else:
        field = schema_cls.model_fields.get(public_name)
        if field is not None and field.alias is None:
            resolved = public_name
    if resolved is not None and schema_cls.model_fields[resolved].exclude is True:
        return None
    return resolved


def _join_column(
    stmt: Select[Any],
    model: type[DeclarativeBase],
    column_path: str,
    schema_cls: SchemaType,
    aliases: _JoinAliases,
    *,
    kind: str = "filter",
) -> tuple[Select[Any], InstrumentedAttribute[Any]]:
    """Join each relationship path once and return its aliased leaf column.

    A sort joins with a LEFT OUTER JOIN, so a row without the related row
    stays in the list. A join that a filter added is an inner join: the
    filter leaves out such a row anyway.
    """
    relationships, column = _resolve_column(model, column_path, schema_cls, kind=kind)
    source: type[DeclarativeBase] | AliasedClass[Any] = model
    path: tuple[str, ...] = ()
    for relationship in relationships:
        # The full path distinguishes home_city.country from work_city.country
        # and successive hops through a self-referential relationship.
        path += (relationship.key,)
        target = aliases.get(path)
        if target is None:
            target = aliased(relationship.property.mapper)
            stmt = stmt.join_from(
                source,
                getattr(source, relationship.key).of_type(target),
                isouter=kind == "sort",
            )
            aliases[path] = target
        source = target
    return stmt, getattr(source, column.key)


def _resolve_column(
    model: type[DeclarativeBase],
    column_path: str,
    schema_cls: SchemaType,
    *,
    kind: str = "filter",
) -> tuple[list[InstrumentedAttribute[Any]], InstrumentedAttribute[Any]]:
    """Resolve a (possibly dotted) public column path to its SQLAlchemy column,
    plus the relationship attributes that need to be joined.

    Strict: every path segment must resolve through the schema's public name
    (alias when set, Python field name otherwise). Falling back to a raw
    model attribute lookup would let URLs reach columns the schema didn't
    expose — for example, a Python field name on an aliased schema field —
    and silently bypass the public-name contract.

    A sort key never goes through a to-many relationship: an item with
    several tags has no single ``tags.name`` to sort on.

    :raises BadQueryParam: ``Unknown <kind> key``, with ``kind`` such as
        ``"filter"`` or ``"sort"``.
    """
    unknown = BadQueryParam(f"Unknown {kind} key: {column_path!r}")
    joins: list[InstrumentedAttribute[Any]] = []
    current_model = model
    current_schema: SchemaType | None = schema_cls
    name = column_path
    while "." in name:
        relation, _, name = name.partition(".")
        if current_schema is None:
            raise unknown
        field_name = _resolve_field_name(current_schema, relation)
        if field_name is None:
            raise unknown
        rel = getattr(current_model, field_name, None)
        if not isinstance(rel, InstrumentedAttribute) or not hasattr(
            rel.property, "mapper"
        ):
            raise unknown
        if kind == "sort" and rel.property.uselist:
            raise unknown
        joins.append(rel)
        current_model = rel.property.mapper.class_
        current_schema = _get_nested_schema(current_schema.model_fields[field_name])

    if current_schema is None:
        raise unknown
    field_name = _resolve_field_name(current_schema, name)
    if field_name is None:
        raise unknown
    column = getattr(current_model, field_name, None)
    if (
        column is None
        or not isinstance(column, InstrumentedAttribute)
        or not isinstance(column.property, ColumnProperty)
    ):
        raise unknown
    return joins, cast(InstrumentedAttribute[Any], column)


def _apply_filtering(
    query_params: QueryParams,
    stmt: Select[Any],
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
    *,
    aliases: _JoinAliases | None = None,
    pagination: _AnyPagination = _DEFAULT_PAGINATION,
    sort_query_param: str = _SORT_QUERY_PARAM,
) -> Select[Any]:
    """Apply ``key=value`` and ``key__op=value`` filters to ``stmt``.

    Multiple filters on the same column are AND-combined. Comma-separated
    values within one parameter are OR-combined for ``eq`` (the default),
    mapped to SQL ``IN`` for ``in``, and AND-combined for ``ne`` (so
    ``status__ne=a,b`` means NOT IN (a, b)). For ``contains``/``icontains``
    values are split on whitespace and AND-combined.
    """
    if aliases is None:
        aliases = {}
    reserved = _reserved_names(pagination, sort_query_param)
    filters: dict[InstrumentedAttribute[Any], list[ColumnElement[Any]]] = defaultdict(
        list
    )

    for key, raw_value in query_params.multi_items():
        if key in reserved:
            continue

        if "__" in key:
            column_name, op = key.split("__", 1)
        else:
            column_name, op = key, "eq"

        stmt, column = _join_column(stmt, model, column_name, schema_cls, aliases)
        parser = functools.partial(_parse_value, schema_cls, column_name)

        if op == "isnull":
            try:
                value = pydantic.TypeAdapter(bool).validate_python(raw_value)
            except pydantic.ValidationError as exc:
                raise BadQueryParam(
                    f"Invalid filter value for {key!r}: {raw_value!r}. "
                    "Use true or false."
                ) from exc
            filters[column].append(column.is_(None) if value else column.isnot(None))
            continue

        clause = _build_clause(column, raw_value, op, parser)
        if clause is not None:
            filters[column].append(clause)

    for column, clauses in filters.items():
        and_clause = clauses[0] if len(clauses) == 1 else sqlalchemy.and_(*clauses)
        stmt = stmt.where(and_clause)
    return stmt


def _build_clause(
    column: InstrumentedAttribute[Any],
    raw_value: str,
    op: str,
    parser: Callable[[str], Any],
) -> ColumnElement[Any] | None:
    """Combine multiple values within one parameter according to ``op`` semantics."""
    if op in {"contains", "icontains"}:
        values = [v for v in raw_value.split() if v]
        if not values:
            return None
        clauses = [_make_where_clause(column, v, op, parser) for v in values]
        return clauses[0] if len(clauses) == 1 else sqlalchemy.and_(*clauses)

    values = raw_value.split(",")
    if not values:
        return None
    if op == "in":
        return column.in_([_parse_filter_value(column, v, parser) for v in values])
    clauses = [_make_where_clause(column, v, op, parser) for v in values]
    if len(clauses) == 1:
        return clauses[0]
    # ``ne`` with multiple values means NOT IN (...) — AND-combine, not OR.
    if op == "ne":
        return sqlalchemy.and_(*clauses)
    return sqlalchemy.or_(*clauses)


def _parse_value(schema_cls: SchemaType, column_name: str, value: str) -> Any:
    """Validate one filter value with the validation of its field.

    :raises BadQueryParam: when the value fails that validation. The message
        has Pydantic's reason, such as ``Input should be a valid integer``.
    """
    *relations, name = column_name.split(".")
    for relation in relations:
        field = schema_cls.model_fields.get(
            _resolve_field_name(schema_cls, relation) or relation
        )
        nested = _get_nested_schema(field)
        if nested is None:
            raise BadQueryParam(f"Unknown filter key: {column_name!r}")
        schema_cls = nested

    field_name = _resolve_field_name(schema_cls, name)
    if field_name is None:
        raise BadQueryParam(f"Unknown filter key: {column_name!r}")

    # Only a validation error is the client's fault. Another exception from
    # a validator is a bug, as it is for a request body.
    try:
        values, _, _ = _field_validator(schema_cls, field_name).validate_python(
            {field_name: value}
        )
    except pydantic.ValidationError as exc:
        reason = exc.errors()[0]["msg"]
        raise BadQueryParam(
            f"Invalid filter value for {column_name!r}: {value!r}. {reason}"
        ) from exc
    result = values[field_name]
    # An IDRef[T] FK field validates to an IDRef object; the SQL bind value
    # is its scalar id, not the reference wrapper (which cannot bind).
    if isinstance(result, IDSchema):
        return result.id
    return result


@functools.cache
def _field_validator(schema_cls: SchemaType, field_name: str) -> SchemaValidator:
    """A validator for a one-field mapping, cut from the model's core schema.

    The named field supplies ``ValidationInfo.field_name``. No other fields
    are validated, so ``ValidationInfo.data`` is empty.

    Field validators, constraints and the model config apply; model validators
    do not. A filter value is one column, so a cross-field rule has nothing to
    compare against and would only reject legal filters (as validating through
    ``validate_assignment`` on a ``model_construct()`` skeleton did).
    """
    core: Any = schema_cls.__pydantic_core_schema__
    definitions = core.get("definitions") if core.get("type") == "definitions" else None
    definitions_by_ref = {
        definition["ref"]: definition for definition in definitions or ()
    }
    config = None
    node: Any = core
    while node is not None and node.get("type") != "model-fields":
        # Recursive models keep their model schema in the definitions.
        if node.get("type") == "definition-ref":
            node = definitions_by_ref[node["schema_ref"]]
            continue
        if node.get("type") == "model" and config is None:
            config = node.get("config")
        node = node.get("schema")
    if node is None:
        raise LookupError(f"{schema_cls.__name__} has no model-fields core schema")
    field_schema: Any = core_schema.model_fields_schema(
        {field_name: core_schema.model_field(node["fields"][field_name]["schema"])}
    )
    if definitions:
        # A field typed with a class the schema uses more than once is a
        # definition-ref; it validates only next to the definitions.
        field_schema = {
            "type": "definitions",
            "schema": field_schema,
            "definitions": definitions,
        }
    return SchemaValidator(field_schema, config)


def _get_nested_schema(field: FieldInfo | None) -> SchemaType | None:
    if field is None:
        return None
    annotation, _ = _unwrap_type(field.annotation)
    if isinstance(annotation, type) and issubclass(annotation, pydantic.BaseModel):
        return annotation
    return None


def _make_where_clause(
    column: InstrumentedAttribute[Any],
    filter_value: str,
    op: str,
    parser: Callable[[str], Any],
) -> ColumnElement[Any]:
    if op == "contains":
        return column.like(f"%{_escape_like_value(filter_value)}%", escape="\\")
    if op == "icontains":
        return column.ilike(f"%{_escape_like_value(filter_value)}%", escape="\\")

    value = _parse_filter_value(column, filter_value, parser)
    if op == "gte":
        return column >= value
    if op == "lte":
        return column <= value
    if op == "gt":
        return column > value
    if op == "lt":
        return column < value
    if op == "ne":
        return column != value
    if op == "eq":
        return column == value
    raise BadQueryParam(f"Unsupported filter operator: {op!r}")


def _parse_filter_value(
    column: InstrumentedAttribute[Any], raw_value: str, parser: Callable[[str], Any]
) -> Any:
    value = parser(raw_value)
    if (
        isinstance(value, _dt.datetime)
        and value.utcoffset() is None
        and getattr(column.type, "timezone", False)
    ):
        return value.replace(tzinfo=_dt.timezone.utc)
    return value
