import collections.abc as _abc
import dataclasses
import datetime as _dt
import decimal as _decimal
import enum
import functools
import re
import weakref
from collections import defaultdict
from typing import (
    Annotated,
    Any,
    Callable,
    Iterator,
    Literal,
    Optional,
    cast,
    get_args,
    get_origin,
)

import pydantic
import sqlalchemy
from pydantic import Field, PydanticInvalidForJsonSchema
from pydantic.fields import FieldInfo
from pydantic.json_schema import GenerateJsonSchema
from pydantic_core import (
    PydanticCustomError,
    PydanticOmit,
    SchemaValidator,
    ValidationError,
    core_schema,
)
from sqlalchemy import ColumnElement, Select, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, aliased
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.orm.properties import ColumnProperty
from sqlalchemy.orm.util import AliasedClass
from sqlalchemy.types import NullType
from starlette.datastructures import QueryParams

from .._columns import check_text, column_value
from .._pagination import (
    _DEFAULT_PAGINATION,
    _SORT_QUERY_PARAM,
    Pagination,
    _check_query_param,
    _settings_of,
)
from .._times import value_error_reason
from ..exc import BadQueryParam, RestlyConfigurationError
from ..schemas._base import (
    IDRef,
    IDSchema,
    _hides_value,
    _schema_role_name,
    _unwrap_type,
)
from ._paged import _pagination_of_list_params, _takes_page_fields
from ._shared import (
    SortNulls,
    _append_pk_tiebreak,
    _can_be_null,
    _class_attribute,
    _escape_like_value,
    _sort_term,
)

_SchemaClass = type[pydantic.BaseModel]
_JoinAliases = dict[tuple[str, ...], AliasedClass[Any]]

_AnyPagination = Pagination | None

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
    #: The key settings, as tuples of patterns in Python names.
    include_filters: tuple[str, ...]
    exclude_filters: tuple[str, ...]
    include_sort_keys: tuple[str, ...]
    exclude_sort_keys: tuple[str, ...]
    #: The name of the sort parameter.
    sort_query_param: str
    #: The sort when the request has none, as a client writes it.
    default_sort: str | None
    #: The keys of ``default_sort``, as a client writes them.
    default_sort_keys: tuple[str, ...]
    #: The sort keys that are on, as a client writes them, in the order of
    #: the schema.
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
    hand-written ones.

    A subclass of generated list params has the record of its base, so it
    reads its values as the base does.
    """
    for klass in params_model.__mro__:
        record = _RECORDS.get(klass)
        if record is not None:
            return record
    return None


def _check_default_sort(label: str, value: Any) -> None:
    """Reject a ``default_sort`` that is not sort keys, written as the sort
    parameter takes them.

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


def _reserved_names(
    pagination: _AnyPagination, sort_query_param: str = _SORT_QUERY_PARAM
) -> frozenset[str]:
    """Query parameters the schema produces besides filters.

    A filter column whose public name is one of these would shadow pagination
    or sort, which would silently break the endpoint contract. Treated as a
    hard error.
    """
    return frozenset({sort_query_param, *_settings_of(pagination)._query_params()})


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
)


def _holds_text(column: Any) -> bool:
    """Whether ``column`` has a text type, also inside ``TypeDecorator`` layers.
    An ``Enum`` type is a text type to SQLAlchemy, but it holds labels."""
    sql_type = getattr(column, "type", None)
    while isinstance(sql_type, TypeDecorator):
        sql_type = sql_type.impl_instance
    return isinstance(sql_type, sqlalchemy.String) and not isinstance(
        sql_type, sqlalchemy.Enum
    )


def _is_label_field(field: FieldInfo) -> bool:
    """Whether ``field`` takes one of a set of labels: an enum, a ``Literal`` or
    a bool."""
    annotation, _ = _unwrap_type(field.annotation)
    if get_origin(annotation) is Literal or annotation is bool:
        return True
    return isinstance(annotation, type) and issubclass(annotation, enum.Enum)


def _supports_text_search(field: FieldInfo, column: Any) -> bool:
    """Whether ``field`` gets ``__icontains``. The column decides, because SQL
    searches the column: ``url: HttpUrl`` on a text column has it, and ``name:
    str`` on another column has not. Labels have it on no column. A column that
    names no type, such as an expression, leaves it to the field.

    A ``TypeDecorator`` needs a text type inside and a ``str`` field.
    SQLAlchemy runs its ``process_bind_param`` on the search text too, so a
    type that stores another value as text, such as a money amount or an
    encrypted text, would search for something else.
    """
    if _is_label_field(field):
        return False
    sql_type = getattr(column, "type", None)
    is_text_field = _unwrap_type(field.annotation)[0] is str
    if isinstance(sql_type, NullType):
        return is_text_field
    if isinstance(sql_type, TypeDecorator):
        return is_text_field and _holds_text(column)
    return _holds_text(column)


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
    (and range) filters on such a field would always fail at request time, so
    only ``__isnull`` is generated -- the same advertise-only-what-works
    principle applied to non-column fields.

    The check mirrors what the request path's ``_value_parser`` (full Pydantic
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
    # a flag, such as an IntFlag, is a Collection on Python 3.11+ because it
    # can hold several flags, but a value from the URL is one flag value
    return not (
        isinstance(origin, type)
        and issubclass(origin, _abc.Collection)
        and not issubclass(origin, (str, bytes, enum.Flag))
    )


def _is_sortable(field: FieldInfo, column: Any) -> bool:
    """Whether a sort key on ``field`` has an order that a client means.

    A field that gets only ``__isnull``, such as a ``dict``, a ``list`` or a
    field on a JSON column, gets no sort key either, and neither does a
    nested model: a document or a list has no such order, and PostgreSQL's
    ``json`` type has no order at all, so a sort on it fails on every
    request. A reference, such as ``IDRef[User]``, sorts by its id.

    :param column: The column of the field.
    """
    if _get_nested_schema(field) is not None and not _is_idref_field(field):
        return False
    return _supports_scalar_operators(field) and not _has_json_type(column)


def _supports_range_operators(field: FieldInfo, column: Any) -> bool:
    if _enum_type(column) is not None:
        # an Enum column holds labels, whatever the type of the field
        return False
    annotation, _ = _unwrap_type(field.annotation)
    if _holds_text(column):
        # SQL compares text, so ``?value__gte=9`` on an int misses 10. Only the
        # text of a date always sorts as the date does. A datetime does not:
        # ``...05.5Z`` sorts before ``...05Z``.
        return annotation is _dt.date
    if annotation is bool:
        return False
    if get_origin(annotation) is Literal:
        # labels too, unless they are numbers
        return all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in get_args(annotation)
        )
    # ``Any`` is a class from Python 3.11 on; it is still an unknown type.
    if annotation is Any or not isinstance(annotation, type):
        return True
    if issubclass(annotation, bool):
        return False
    if issubclass(annotation, enum.Enum):
        # labels; an int enum has the order of its numbers, but the numbers
        # of a flag have no order
        return issubclass(annotation, int) and not issubclass(annotation, enum.Flag)
    return issubclass(annotation, _ORDERABLE_TYPES)


def _enum_type(column: Any) -> sqlalchemy.Enum | None:
    """The ``Enum`` type of ``column``, also inside ``TypeDecorator``
    layers, or ``None``. Such a column holds labels: the names of the
    members, or the strings that it was given."""
    sql_type = getattr(column, "type", None)
    while isinstance(sql_type, TypeDecorator):
        sql_type = sql_type.impl_instance
    return sql_type if isinstance(sql_type, sqlalchemy.Enum) else None


def _has_json_type(column: Any) -> bool:
    """Whether ``column`` has SQLAlchemy's ``JSON`` type, also inside
    ``TypeDecorator`` layers, such as ``fr.models.PydanticJSON``. ``JSONB`` is such
    a type too.

    Such a column holds JSON text, such as ``"x"`` with its quotes.
    SQLAlchemy compares it with a value such as ``x``, without the quotes, so
    a filter finds no row on SQLite and fails on PostgreSQL.
    """
    sql_type = getattr(column, "type", None)
    while isinstance(sql_type, TypeDecorator):
        sql_type = sql_type.impl_instance
    return isinstance(sql_type, sqlalchemy.JSON)


def derive_list_params_schema(
    response_schema: _SchemaClass,
    model: type[Any],
    *,
    pagination: _AnyPagination = _DEFAULT_PAGINATION,
    include_filters: _abc.Iterable[str] = ("*",),
    exclude_filters: _abc.Iterable[str] = (),
    include_sort_keys: _abc.Iterable[str] = ("*",),
    exclude_sort_keys: _abc.Iterable[str] = (),
    sort_query_param: str = "sort",
    default_sort: str | None = None,
) -> _SchemaClass:
    """
    Build the list params: a Pydantic model that describes and validates the
    URL query parameters of a list endpoint. A view keeps it in
    :attr:`~fastapi_restly.views.BaseRestView.list_params_schema`.

    The generated model accepts pagination (``page`` and ``page_size``, or
    ``limit`` and ``offset``), sorting (``sort``), and filters. Each field of ``response_schema`` that maps to a
    column of ``model`` gives filter keys, and its type decides which ones.
    Every such field gives the plain key and the keys with the suffixes
    ``__in``, ``__ne`` and ``__not_in``. A column that can be NULL also gives
    ``__isnull``, and so does each dotted key, because a related row can be
    missing. A type with an order, such as a number, a date or an int enum,
    also gives ``__gte``, ``__lte``, ``__gt`` and ``__lt``, unless the column
    has SQLAlchemy's ``Enum`` type, which holds labels. A text column gives
    ``__icontains``, and range keys only for a field of type ``date``.

    A field that does not map to a column, such as a relationship, gives no
    keys. A WriteOnly field gives none either, because a filter on it would
    let a client find out its value. A name with ``.`` or ``__`` gives none,
    because a key could not be read back. A field whose type is a collection
    (``dict``/``list``, e.g. a ``JSON`` or ``ARRAY`` column) gives only
    ``__isnull``: a value from the URL cannot become a collection. A field on
    a JSON column gives only ``__isnull`` too, also when it holds one value,
    such as a ``str``: the database compares the JSON text, not the value. A
    field of a nested schema gives dotted keys, such as ``vendor.name``.

    The model forbids a key that it does not have, so a route of your own
    answers ``422`` for it. A view passes on only the keys that it knows when
    its ``unknown_query_params`` is ``"ignore"``.

    Each key has the type of the field and its validators, without its bounds,
    such as ``gt``: a filter compares a value, and does not store it. Only
    ``__in`` and ``__not_in`` take several values, separated by commas or from a
    repeated key.

    The four key settings choose which of these keys the model has. Each
    entry is a key with the Python name of its field, also for a field with
    an alias, or a pattern. An entry with the name in the URL of such a field
    raises an error. ``*`` in a pattern
    matches any characters except ``.``, so it stays within one level.
    By default the model has the keys of the resource's own fields, and no
    dotted keys. An entry that matches no key raises an error.

    With a ``pagination`` (the default is ``page`` and ``page_size``), its two
    query parameters are added under its names and validated by Pydantic with
    bounds. With a :class:`~fastapi_restly.views.NumberedPagination`, the page
    is at least 1 and at most ``max_page`` when set, and the page size is from
    1 to ``max_page_size``. With an
    :class:`~fastapi_restly.views.OffsetPagination`, the limit is from 1 to
    ``max_limit``, and the offset is at least 0 and at most ``max_offset`` when
    set. The resulting SQL offset must also fit in a signed 64-bit integer.
    Out-of-range values produce a standard 422 response from FastAPI. With a
    :class:`~fastapi_restly.views.NoPagination`
    or ``None``, no pagination parameters are emitted at all -- the endpoint
    returns every matching row -- while sorting and filtering stay available.

    :param response_schema: The response schema whose fields decide the
        available filter parameters. A view passes its ``response_schema``, so
        a client can filter only on a field of that class.
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
        ``include_filters``. A name without a suffix removes the whole field:
        the plain key and each key with a suffix.
    :param include_sort_keys: The keys that ``sort`` accepts. A sort key has
        no suffix, and never goes through a to-many relationship. With
        ``()``, the model has no ``sort``.
    :param exclude_sort_keys: The sort keys to remove, after
        ``include_sort_keys``.
    :param sort_query_param: The name of the sort parameter, such as
        ``"order_by"``.
    :param default_sort: The sort when the request has none, with the
        Python names of the fields, such as ``"-created_at"``. It becomes the
        default of the sort parameter, with the names in the URL. A key must
        be a field that a sort key could have, but
        ``include_sort_keys`` does not have to keep it.
    :raises fastapi_restly.exc.RestlyConfigurationError: An entry of a key
        setting matches no key, matches only names in the URL, or is not a
        key or pattern. The sort
        parameter has the name of a page parameter. A key of
        ``default_sort`` is not a field to sort on, or appears twice.
    """
    return _derive_list_params(
        response_schema,
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
    response_schema: _SchemaClass,
    model: type[DeclarativeBase],
    *,
    pagination: _AnyPagination,
    settings: _abc.Mapping[str, Any],
    owner: str | None = None,
    inherited: _abc.Mapping[str, str] | None = None,
    own_filters: _abc.Sequence[_OwnFilter] = (),
    sort_query_param: str = _SORT_QUERY_PARAM,
    default_sort: str | None = None,
    default_sort_from: str | None = None,
) -> _SchemaClass:
    """:func:`derive_list_params_schema`, for a view too.

    :param owner: The view, for the messages.
    :param inherited: The key settings that a view inherits, each with the
        view that sets it. Their entries may match no key, because a base
        view serves many schemas.
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
    settings = _settings_of(pagination)
    if sort_query_param in settings._query_params():
        raise RestlyConfigurationError(
            f"{_setting_label(owner, 'sort_query_param')} cannot be "
            f"{sort_query_param!r}: it is a page parameter of the pagination. "
            "Choose another name for one of them."
        )
    fields: dict[str, Any] = settings._list_params_fields()

    reserved = _reserved_names(pagination, sort_query_param)
    # The key settings name a field by its Python name, and a client by the
    # name that a response shows. So each key below is in Python names, and
    # its value holds the key in the URL.
    filter_fields: dict[str, tuple[str, Any]] = {}
    # The filter keys of each field, for the messages.
    keys_of_field: dict[str, list[str]] = {}
    # The name in the URL of each field with a column.
    url_names: dict[str, str] = {}
    sort_keys: dict[str, str] = {}
    for url_name, name, field in _iter_fields_including_nested(response_schema):
        # Only emit params for fields that resolve to a filterable column on the
        # model -- using the very predicate the request path applies. A
        # relationship/collection field (e.g. ``books: list[BookRef]``) or a
        # reference traversal that does not resolve would otherwise advertise
        # filters in OpenAPI that always fail at request time.
        try:
            relationships, column = _resolve_column(model, url_name, response_schema)
        except BadQueryParam:
            continue
        url_names[name] = url_name
        # ``sort=tags.name`` has no clear meaning when an item has several
        # tags, and the join repeats rows.
        if _is_sortable(field, column) and not any(
            relationship.property.uselist for relationship in relationships
        ):
            sort_keys[name] = url_name
        field_filters = _filter_fields(response_schema, url_name, field, column)
        keys_of_field[name] = []
        for url_key, filter_field in field_filters.items():
            key = name + url_key.removeprefix(url_name)
            filter_fields[key] = (url_key, filter_field)
            keys_of_field[name].append(key)

    schema_name = _written_name(response_schema)
    url_filter_keys = {key: url_key for key, (url_key, _) in filter_fields.items()}
    inherited = inherited or {}
    for name in _KEY_SETTINGS:
        is_filter_setting = name in ("include_filters", "exclude_filters")
        label = _setting_label(owner, name)
        # also for a setting that the view inherits: the entry would leave
        # the keys on, or off, without a sign
        _check_url_names(
            f"{label} (from {inherited[name]})" if name in inherited else label,
            patterns[name],
            url_filter_keys if is_filter_setting else sort_keys,
            url_names,
            whole_fields=name == "exclude_filters",
        )
        if name in inherited or patterns[name] == _KEY_SETTING_DEFAULTS[name]:
            continue
        if is_filter_setting:
            _check_filter_entries(
                label,
                patterns[name],
                keys_of_field,
                url_names,
                schema_name,
                whole_fields=name == "exclude_filters",
            )
        else:
            _check_sort_entries(
                label, patterns[name], sort_keys, url_names, schema_name
            )

    label = _setting_label(owner, "default_sort")
    default_sort_keys = _default_sort_keys(
        f"{label} (from {default_sort_from})" if default_sort_from else label,
        default_sort,
        sort_keys,
        url_names,
        schema_name,
        hint=(
            f" Set {label} = None for primary key order." if default_sort_from else ""
        ),
    )
    # OpenAPI and the record hold the sort as a client writes it
    url_default_sort = (
        None
        if default_sort is None
        else ",".join(
            ("-" if term.startswith("-") else "") + sort_keys[term.removeprefix("-")]
            for term in default_sort.split(",")
        )
    )
    url_sort_keys = [
        sort_keys[key]
        for key in _pick_keys(
            list(sort_keys),
            patterns["include_sort_keys"],
            patterns["exclude_sort_keys"],
        )
    ]
    if url_sort_keys:
        fields[sort_query_param] = _sort_field(url_sort_keys, url_default_sort)
    for key in _pick_keys(
        list(filter_fields),
        patterns["include_filters"],
        _whole_fields(patterns["exclude_filters"]),
    ):
        url_key, filter_field = filter_fields[key]
        if url_key in reserved:
            raise RestlyConfigurationError(
                f"{_setting_label(owner, 'list_params_schema')}: the filter key "
                f"{url_key!r} of {schema_name} is also a page or sort parameter. "
                "Give the field a Pydantic alias, rename the page or sort "
                "parameter, or leave the field out with "
                f"exclude_filters = ({key!r},)."
            )
        fields[url_key] = filter_field
    for own in own_filters:
        _check_own_key(
            _setting_label(owner, own.method),
            own.key,
            fields,
            reserved,
            url_names,
            {url_key: key for key, url_key in url_filter_keys.items()},
            schema_name,
        )
        annotation = own.annotation if own.always else Optional[own.annotation]
        default = own.default if own.always else None
        fields[own.key] = (
            Annotated[annotation, Field(description=own.description)],
            default,
        )

    params_model = pydantic.create_model(  # type: ignore[call-overload]
        _schema_role_name(response_schema, "ListParams"),
        # also on a route outside a view, which reads the model with Query()
        __config__=pydantic.ConfigDict(extra="forbid"),
        __validators__=settings._list_params_validators(),
        **fields,
    )
    _RECORDS[params_model] = _ListParamsRecord(
        pagination=pagination,
        **patterns,
        sort_query_param=sort_query_param,
        default_sort=url_default_sort,
        default_sort_keys=tuple(sort_keys[key] for key in default_sort_keys),
        sort_keys=tuple(url_sort_keys),
        own_filters=tuple(own_filters),
    )
    return params_model


def _default_sort_keys(
    label: str,
    default_sort: str | None,
    sort_keys: _abc.Mapping[str, str],
    url_names: _abc.Mapping[str, str],
    schema_name: str,
    *,
    hint: str = "",
) -> tuple[str, ...]:
    """The keys of ``default_sort``, in Python names.

    :param sort_keys: Every key that the schema can sort on, before the key
        settings choose, with its name in the URL.
    :param url_names: The name in the URL of each field with a column.
    :param hint: The end of the message.
    :raises RestlyConfigurationError: a key is not in ``sort_keys``.
    """
    if default_sort is None:
        return ()
    keys = tuple(term.removeprefix("-") for term in default_sort.split(","))
    for key in keys:
        if key in sort_keys:
            continue
        python_name = _python_name_of(key, url_names)
        if python_name in sort_keys:
            message = (
                f"{label}: {key!r} is the name in the URL. A key is the Python "
                f"name of a field, so write {python_name!r}."
            )
        else:
            message = (
                f"{label}: {key!r} is not a field of {schema_name} to sort on. "
                "A key is the Python name of a field."
            )
            if sort_keys:
                message += (
                    f" The fields to sort on are: {_names(sort_keys, url_names)}."
                )
            else:
                message += f" {schema_name} has no fields to sort on."
        raise RestlyConfigurationError(message + hint)
    return keys


def _python_name_of(url_name: str, url_names: _abc.Mapping[str, str]) -> str | None:
    """The Python name of the field that has ``url_name`` in the URL, when the
    two names differ."""
    return next(
        (
            name
            for name, url in url_names.items()
            if url == url_name and name != url_name
        ),
        None,
    )


def _names(names: _abc.Iterable[str], url_names: _abc.Mapping[str, str]) -> str:
    """Fields for a message, each with its name in the URL when that differs,
    such as ``id, created_at (createdAt in the URL)``."""
    return ", ".join(_name(name, url_names) for name in names)


def _name(name: str, url_names: _abc.Mapping[str, str]) -> str:
    url_name = url_names.get(name, name)
    return name if url_name == name else f"{name} ({url_name} in the URL)"


def _quoted_name(name: str, url_names: _abc.Mapping[str, str]) -> str:
    """A field for a message, as :func:`_name`, with quotes: ``'created_at'
    (createdAt in the URL)``."""
    return repr(name) + _name(name, url_names).removeprefix(name)


def _check_url_names(
    label: str,
    entries: tuple[str, ...],
    url_keys: _abc.Mapping[str, str],
    url_names: _abc.Mapping[str, str],
    *,
    whole_fields: bool = False,
) -> None:
    """Raise for an entry that matches a key only by its name in the URL.

    The key settings take Python names. Such an entry would leave the keys
    on, or off, without a sign, so this check runs also for a setting that a
    view inherits.

    :param url_keys: Each key in Python names, with the key in the URL.
    :param whole_fields: A bare name stands for the whole field, as in
        ``exclude_filters``; see :func:`_whole_fields`.
    """
    for entry in entries:
        patterns = _whole_fields((entry,)) if whole_fields else (entry,)
        regexes = [_pattern_regex(pattern) for pattern in patterns]
        if any(regex.fullmatch(key) for regex in regexes for key in url_keys):
            continue
        matched = next(
            (
                (key, url_key)
                for key, url_key in url_keys.items()
                if any(regex.fullmatch(url_key) for regex in regexes)
            ),
            None,
        )
        if matched is None:
            continue
        key, url_key = matched
        field, _ = _split_key(entry)
        python_name = _python_name_of(field, url_names)
        if "*" not in entry and python_name is not None:
            raise RestlyConfigurationError(
                f"{label}: {entry!r} is the name in the URL. The key settings "
                "take the Python name of a field, so write "
                f"{python_name + entry.removeprefix(field)!r}."
            )
        raise RestlyConfigurationError(
            f"{label}: {entry!r} matches only names in the URL, such as "
            f"{url_key!r}. The key settings take the Python names of the "
            f"fields, such as {key!r}."
        )


def _split_key(key: str) -> tuple[str, str]:
    """The field and the operator of a filter key, such as ``("vendor.name",
    "icontains")``. The operator is ``""`` for a key without one. The last
    ``__`` splits them, so a field may end in ``_``, as ``type___in`` does."""
    field, separator, operator = key.rpartition("__")
    return (field, operator) if separator else (key, "")


def _check_own_key(
    label: str,
    key: str,
    fields: dict[str, Any],
    reserved: _abc.Set[str],
    url_names: _abc.Mapping[str, str],
    python_keys: _abc.Mapping[str, str],
    schema_name: str,
) -> None:
    """Reject an own filter key that the list params already have, or that
    does not read as the generated keys do. An own key is a key in the URL.

    :param url_names: The name in the URL of each field with a column.
    :param python_keys: Each generated filter key in the URL, also the keys
        that are off, with the key in Python names, for the messages.

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
    field, _ = _split_key(key)
    if ("__" in key or "." in key) and field not in url_names.values():
        what = (
            f"the key {key!r} starts with {field!r}, which is"
            if field != key
            else f"the key {key!r} is"
        )
        message = (
            f"{label}: {what} not a field of {schema_name} with filters. A key "
            "with '__' or '.' starts with a field, as the generated keys do. Or "
            "choose a key without '__' and '.'."
        )
        if url_names:
            message += f" The fields with filters are: {', '.join(url_names.values())}."
        else:
            message += f" {schema_name} has no fields with filters."
        raise RestlyConfigurationError(message)
    if key in fields:
        python_key = python_keys.get(key, key)
        message = (
            f"{label}: the key {key!r} is also a generated filter key. To "
            f"replace it, leave it out with exclude_filters = ({python_key!r},)."
        )
        if "__" not in key:
            message += (
                f" That also removes {python_key}__in and the other keys of the "
                "field. To keep them, list the keys you want in include_filters "
                "instead."
            )
        raise RestlyConfigurationError(message)


def _setting_label(owner: str | None, name: str) -> str:
    return f"{owner}.{name}" if owner else name


def _filter_fields(
    response_schema: _SchemaClass, name: str, field: FieldInfo, column: Any
) -> dict[str, Any]:
    """The filter keys of one field, as fields of the list params model.

    A key has the type of its field, and takes one value. Only ``__in`` and
    ``__not_in`` take several. A text search takes one piece of text, and
    ``__isnull`` a bool, and only a column that can be NULL has it. A field
    that holds a collection, such as a ``dict``, or that has a JSON column, has
    no other key. A value from the URL cannot become a collection, and a JSON
    column holds JSON text, so a filter on it finds no row on SQLite and fails
    on PostgreSQL.

    :param response_schema: The schema that the key ``name`` starts from.
    :param name: The field as the client names it, such as ``vendor.name``.
    :param field: The field.
    :param column: The column of the field, whose type can take the range
        keys away.
    """
    fields: dict[str, Any] = {}
    supports_scalar = _supports_scalar_operators(field) and not _has_json_type(column)
    # a related row can be missing, so a dotted key can always be NULL
    has_isnull = "." in name or _can_be_null(column)
    if supports_scalar:
        schema_cls, field_name = _field_of(response_schema, name)
        values = _KeyValues(
            schema_cls,
            field_name,
            column,
            isnull_key=f"{name}__isnull" if has_isnull else None,
        )

        def one_value(description: str) -> Any:
            return _key_field(
                lambda: values.json_schema, _one_value(values.read), description
            )

        def several_values(description: str) -> Any:
            return _key_field(
                lambda: {"type": "array", "items": values.json_schema},
                _several_values(values.read),
                description,
                annotation=Optional[list[Any]],
            )

        fields[name] = one_value(f"Rows where ``{name}`` is this value.")
        fields[f"{name}__in"] = several_values(
            f"Rows where ``{name}`` is one of these values. Separate the values "
            "with commas, or repeat the key."
        )
        fields[f"{name}__ne"] = one_value(f"Rows where ``{name}`` is not this value.")
        fields[f"{name}__not_in"] = several_values(
            f"Rows where ``{name}`` is none of these values. Separate the values "
            "with commas, or repeat the key."
        )
    if has_isnull:
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

    if supports_scalar and _supports_range_operators(field, column):
        for suffix, sql in (
            ("__gte", ">="),
            ("__lte", "<="),
            ("__gt", ">"),
            ("__lt", "<"),
        ):
            fields[f"{name}{suffix}"] = one_value(f"``{name} {sql} value``.")

    if supports_scalar and _supports_text_search(field, column):
        fields[f"{name}__icontains"] = _key_field(
            lambda: {"type": "string"},
            _search_value,
            f"Rows where ``{name}`` holds this text, in any case.",
        )
    return fields


class _KeyValues:
    """How the filter keys of one field read a value, and its type in
    OpenAPI.

    Restly builds each of them when something first needs it: a request, or
    OpenAPI. Both need the core schema of the whole schema class, which a
    class that names a schema defined later does not have yet when the view
    registers. And a key that the settings leave out costs nothing.
    """

    def __init__(
        self,
        schema_cls: _SchemaClass,
        field_name: str,
        column: Any,
        *,
        isnull_key: str | None = None,
    ) -> None:
        self._schema_cls = schema_cls
        self._field_name = field_name
        self._column = column
        self._isnull_key = isnull_key

    @functools.cached_property
    def _reader(self) -> Callable[[Any], Any]:
        return _checked_reader(
            self._schema_cls,
            self._field_name,
            self._column,
            isnull_key=self._isnull_key,
        )

    def read(self, value: Any) -> Any:
        """The value of a key, from the value in the URL; see
        :func:`_checked_reader`."""
        return self._reader(value)

    @functools.cached_property
    def json_schema(self) -> dict[str, Any]:
        """The JSON schema of one value; see :func:`_value_json_schema`."""
        return _value_json_schema(self._schema_cls, self._field_name, self._column)


@dataclasses.dataclass(frozen=True)
class _KeyJsonSchema:
    """The JSON schema of a filter key in OpenAPI, built when OpenAPI is
    first written. It replaces the schema that Pydantic would write, as
    ``pydantic.WithJsonSchema`` does.

    ``value_schema`` gives the JSON schema of the key's value. The key can
    always be left out, so the schema also allows ``null``.
    """

    value_schema: Callable[[], dict[str, Any]]

    def __get_pydantic_json_schema__(self, schema: Any, handler: Any) -> dict[str, Any]:
        value_schema = self.value_schema()
        if not value_schema:
            return {}
        options = (
            value_schema["anyOf"] if set(value_schema) == {"anyOf"} else [value_schema]
        )
        return {"anyOf": [*options, {"type": "null"}]}


def _key_field(
    value_schema: Callable[[], dict[str, Any]],
    validate: Callable[[Any], Any],
    description: str,
    *,
    annotation: Any = Optional[Any],
) -> tuple[Any, None]:
    """A filter key as a field of the list params model, with no default.

    Restly validates the value itself, so the type in OpenAPI comes from
    ``value_schema``: the type of the field, without its bounds.

    :param value_schema: Gives the JSON schema of the key's value.
    :param validate: Gives the value of the key, from the value in the URL.
    :param description: The text of the key in OpenAPI.
    :param annotation: The type of the key, a list for a key with several
        values, so that the list route reads each value of a repeated key.
    """
    return (
        Annotated[
            annotation,
            pydantic.PlainValidator(validate),
            _KeyJsonSchema(value_schema),
            Field(description=description),
        ],
        None,
    )


def _one_value(read: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """The validator of a filter key that takes one value."""

    def validate(value: Any) -> Any:
        if value is None:
            return None
        try:
            return read(value)
        except _BadValue as exc:
            raise PydanticCustomError(
                cast(Any, exc.error_type), cast(Any, exc.reason)
            ) from exc

    return validate


def _several_values(read: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """The validator of ``__in`` and ``__not_in``. An error names the position
    of the value that fails. A value cannot be ``None``: SQL compares no value
    with NULL, so ``NOT IN ('a', NULL)`` would leave out every row."""

    def validate(value: Any) -> Any:
        if value is None:
            return None
        values: list[Any] = []
        errors: list[Any] = []
        for position, item in enumerate(_listed_values(value)):
            try:
                result = read(item)
                if result is None:
                    raise _BadValue("value_error", _NULL_IN_LIST)
                values.append(result)
            except _BadValue as exc:
                errors.append(
                    {
                        "type": PydanticCustomError(
                            cast(Any, exc.error_type), cast(Any, exc.reason)
                        ),
                        "loc": (position,),
                        "input": item,
                    }
                )
        if errors:
            raise ValidationError.from_exception_data("list", errors)
        return values

    return validate


#: Why ``__in`` or ``__not_in`` refuses ``None``, for the messages.
_NULL_IN_LIST = "A list of values cannot hold null."


def _listed_values(value: Any) -> list[Any]:
    """The values of ``__in`` and ``__not_in``. One text is split at each comma,
    and spaces around each part go. With several values, as from a repeated key,
    each value stays as it is, so a value can hold a comma."""
    values = (
        list(value) if isinstance(value, (list, tuple, set, frozenset)) else [value]
    )
    if len(values) == 1 and isinstance(values[0], str):
        return [part.strip() for part in values[0].split(",")]
    return values


def _search_value(value: Any) -> str | None:
    """The value of a text search: one piece of text, without spaces around
    it. An empty value is no filter.

    :raises ValueError: the value is a collection, such as a list from code,
        or the text holds a NUL character.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        raise ValueError("A text search takes one piece of text.")
    text = str(value).strip()
    check_text(text)
    return text or None


#: Why a field has no key but ``__isnull``, for the messages.
_ISNULL_ONLY_HINT = (
    "A field that holds a dict or a list, or that has a JSON column, has only __isnull."
)

#: Why a field has no key with this suffix, for the messages.
_SUFFIX_HINTS = {
    "icontains": (
        "Only a field on a text column has __icontains, and not an enum, a Literal "
        "or a bool. On a TypeDecorator column, the field must be a str."
    ),
    "isnull": "Only a column that can be NULL has __isnull.",
    **dict.fromkeys(
        ("gte", "lte", "gt", "lt"),
        "Only fields with an order, such as numbers, dates and int enums, have "
        "__gte, __lte, __gt and __lt. A column of SQLAlchemy's Enum type has none, "
        "and a text column has them only for a field of type date.",
    ),
    **dict.fromkeys(("", "in", "ne", "not_in"), _ISNULL_ONLY_HINT),
}


def _whole_fields(entries: tuple[str, ...]) -> tuple[str, ...]:
    """The patterns of ``exclude_filters``: a name without a suffix stands for
    the whole field, the plain key and each key with a suffix. So a bare name
    never leaves a client more than you named."""
    patterns: list[str] = []
    for entry in entries:
        patterns.append(entry)
        if "*" not in entry and "__" not in entry:
            patterns.append(f"{entry}__*")
    return tuple(patterns)


def _check_filter_entries(
    label: str,
    entries: tuple[str, ...],
    keys_of_field: dict[str, list[str]],
    url_names: _abc.Mapping[str, str],
    schema_name: str,
    *,
    whole_fields: bool = False,
) -> None:
    """Raise for the first entry that matches no filter key.

    The message lists the keys of the field that the entry names, or the
    fields that have filters.

    :param keys_of_field: The filter keys of each field, in Python names.
    :param url_names: The name in the URL of each field with a column.
    :param whole_fields: A bare name stands for the whole field, as in
        ``exclude_filters``; see :func:`_whole_fields`.
    """
    keys = [key for field_keys in keys_of_field.values() for key in field_keys]
    for entry in entries:
        patterns = _whole_fields((entry,)) if whole_fields else (entry,)
        if any(
            _pattern_regex(pattern).fullmatch(key)
            for pattern in patterns
            for key in keys
        ):
            continue
        message = f"{label}: {entry!r} matches no filter key of {schema_name}."
        field, suffix = _split_key(entry)
        if "*" not in field and field in keys_of_field:
            field_keys = ", ".join(keys_of_field[field])
            named = _quoted_name(field, url_names)
            if field_keys:
                message += f" The filter keys of {named} are: {field_keys}."
            else:
                message += f" {named} has no filter keys."
            if keys_of_field[field] in ([], [f"{field}__isnull"]):
                message += f" {_ISNULL_ONLY_HINT}"
                if not keys_of_field[field]:
                    message += f" {_SUFFIX_HINTS['isnull']}"
            elif suffix in _SUFFIX_HINTS:
                message += f" {_SUFFIX_HINTS[suffix]}"
        elif "*" not in entry:
            if keys_of_field:
                message += (
                    f" The fields with filters are: {_names(keys_of_field, url_names)}."
                )
            else:
                message += f" {schema_name} has no fields with filters."
        raise RestlyConfigurationError(message)


def _check_sort_entries(
    label: str,
    entries: tuple[str, ...],
    sort_keys: _abc.Collection[str],
    url_names: _abc.Mapping[str, str],
    schema_name: str,
) -> None:
    """Raise for the first entry that matches no sort key.

    :param sort_keys: Every sort key, in Python names.
    :param url_names: The name in the URL of each field with a column.
    """
    for entry in entries:
        if any(_pattern_regex(entry).fullmatch(key) for key in sort_keys):
            continue
        message = f"{label}: {entry!r} matches no sort key of {schema_name}."
        if "__" in entry:
            message += " A sort key has no suffix."
        if sort_keys:
            message += f" The sort keys are: {_names(sort_keys, url_names)}."
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
    model: type[Any],
    response_schema: _SchemaClass,
    *,
    pagination: _AnyPagination = cast(Any, _FROM_PARAMS),
    sort_nulls: SortNulls = "last",
) -> Select[Any]:
    """
    Apply pagination, sorting, and filtering to ``stmt`` using validated
    list params.

    ``list_params`` is normally an instance of the model returned by
    :func:`derive_list_params_schema`. The default list endpoints pass a
    validated instance, so the page and the page size are within their
    bounds. This function still checks each filter value itself, with the
    validation of its field.
    ``response_schema`` decides the fields a client can filter and sort on.
    A view passes its response schema.

    The arguments match the view method
    :meth:`~fastapi_restly.views.RestView.apply_list_params`, which calls this
    function with the view's model, response schema and pagination.

    With list params from :func:`derive_list_params_schema`, the sort
    parameter has the name that the model was created with, and accepts only
    its sort keys. With raw ``QueryParams`` or a hand-written model, the sort
    parameter is ``sort``. List params
    with a value for a view's own filter key, from
    :func:`~fastapi_restly.views.list_filter`, raise ``ValueError``: only the
    view can apply them. Use the view's ``apply_list_params``.

    A raw :class:`~starlette.datastructures.QueryParams` is also accepted
    for callers that build the query parameters programmatically. It skips
    the list params model, so nothing checks the bounds of the page, such as
    ``max_page_size`` or ``max_limit``: the caller checks them. Each filter
    value still goes through the validation of its field. Every filter key
    that :func:`derive_list_params_schema` can generate works, also a dotted
    one.

    ``pagination`` names the page parameters to read, and says how the page
    applies. By default it is the pagination that the list params were created
    with, by :func:`derive_list_params_schema` or by a view.
    :class:`~fastapi_restly.query.OffsetListParams` outside a view read
    ``limit`` and ``offset``. Raw ``QueryParams`` and other hand-written models
    read ``page`` and ``page_size``.

    A ``NoPagination`` or ``None`` applies no ``LIMIT``/``OFFSET``. With page
    numbers, a missing page-size key also applies neither. With an offset, a
    missing limit applies no ``LIMIT``, and the offset still applies.

    ``sort_nulls`` says where the sort puts NULLs: ``"last"`` (the default)
    or ``"first"`` in both directions, or ``None`` for where the database
    puts them. Without a sort parameter, the sort is the ``default_sort``
    that the list params were created with.

    Examples::

        # Pagination
        page=2&page_size=50
        limit=50&offset=50

        # Sorting
        sort=name,-created_at

        # Filtering
        name=Bob&status=active&created_at__gte=2024-01-01

        # Search in text (text columns)
        name__icontains=John&email__icontains=example
    """
    if sort_nulls not in ("first", "last", None):
        raise ValueError(
            f"sort_nulls must be 'first', 'last' or None, got {sort_nulls!r}."
        )
    _reject_own_filters(list_params)
    stmt = _apply_filters(
        stmt, list_params, model, response_schema, pagination=pagination
    )
    return _apply_sort_and_page(
        stmt,
        list_params,
        model,
        response_schema,
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
) -> tuple[_ListParamsRecord | None, _AnyPagination, str, dict[str, Any]]:
    """The record of ``list_params``, the pagination to apply, the name of
    the sort parameter, and the values of the list params by query key.

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
        pagination = (
            record.pagination
            if record
            else _pagination_of_list_params(type(list_params))
        )
    sort_query_param = record.sort_query_param if record else _SORT_QUERY_PARAM
    if sort_query_param in _settings_of(pagination)._query_params():
        # list params from derive_list_params_schema reject this when created
        raise RestlyConfigurationError(
            f"A page parameter of the pagination is {sort_query_param!r}, "
            "which is also the sort parameter of these list params. Give the "
            "page parameter another name."
        )
    own_keys = {own.key for own in record.own_filters} if record else set()
    values = _list_params_values(list_params, exclude=own_keys, pagination=pagination)
    return record, pagination, sort_query_param, values


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
    response_schema: _SchemaClass,
    *,
    pagination: _AnyPagination = cast(Any, _FROM_PARAMS),
) -> Select[Any]:
    """The first step of :func:`apply_list_params`: the generated filters."""
    record, pagination, sort_query_param, values = _read_list_params(
        list_params, pagination
    )
    aliases: _JoinAliases = {}
    stmt = _apply_filtering(
        values,
        stmt,
        model,
        response_schema,
        validated=record is not None,
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
    response_schema: _SchemaClass,
    *,
    pagination: _AnyPagination = cast(Any, _FROM_PARAMS),
    sort_nulls: SortNulls = "last",
) -> Select[Any]:
    """The last steps of :func:`apply_list_params`: the sort, then the page."""
    record, pagination, sort_query_param, values = _read_list_params(
        list_params, pagination
    )
    stmt = _apply_sorting(
        values,
        stmt,
        model,
        response_schema,
        aliases=dict(_STATEMENT_JOINS.get(stmt, {})),
        # the default is the value of the sort field, also with a key that
        # the client cannot send
        sort_keys=record.sort_keys + record.default_sort_keys if record else None,
        sort_query_param=sort_query_param,
        default_sort=record.default_sort if record else None,
        sort_nulls=sort_nulls,
    )
    return _settings_of(pagination)._apply(stmt, values)


def _public_name(name: str, field: FieldInfo) -> str:
    """The name of a schema field in the URL: the name that a response shows."""
    return field.serialization_alias or field.alias or name


def _query_key(name: str, field: FieldInfo) -> str:
    """The query key of a list params field: its validation alias, its alias, or
    its name, as FastAPI reads a query model."""
    if isinstance(field.validation_alias, str):
        return field.validation_alias
    return field.alias or name


def _list_params_values(
    list_params: Any,
    *,
    exclude: _abc.Set[str] = frozenset(),
    pagination: _AnyPagination = None,
) -> dict[str, Any]:
    """The values of ``list_params`` by query key, without the keys that have
    no value.

    Each field of a model goes under its query key. The page fields of list
    params of your own go under the names of ``pagination``, also when code
    created them without a view. In a ``QueryParams``, the last value of a
    repeated key counts, as in FastAPI, except on ``__in`` and ``__not_in``.

    A generated key holds the value of its field, which stays as it is: a
    dump would make a dataclass a dict. A value of list params of your own
    goes through the validation of its field, as text from a URL does, so it
    comes in its JSON form, such as ``"desc"`` for a member of a ``str``
    enum, or a list for a tuple.

    :param exclude: The fields of a model to leave out.
    :param pagination: The pagination to read the page from.
    """
    if isinstance(list_params, pydantic.BaseModel):
        params_model = type(list_params)
        fields = params_model.model_fields
        page_keys = (
            pagination._own_list_params_keys()
            if pagination is not None and _takes_page_fields(params_model, pagination)
            else {}
        )
        if _record_of(params_model) is not None:
            dumped = {
                name: value
                for name in fields
                if name not in exclude
                and (value := getattr(list_params, name)) is not None
            }
        else:
            dumped = list_params.model_dump(
                mode="json", exclude_none=True, by_alias=False, exclude=set(exclude)
            )
        return {
            page_keys.get(name)
            or (_query_key(name, fields[name]) if name in fields else name): value
            for name, value in dumped.items()
        }
    if isinstance(list_params, _abc.Mapping) and not isinstance(
        list_params, QueryParams
    ):
        return dict(list_params)
    query = (
        list_params
        if isinstance(list_params, QueryParams)
        else QueryParams(list_params)
    )
    return {
        key: query.getlist(key) if _takes_several_values(key) else query[key]
        for key in query.keys()
    }


def _takes_several_values(key: str) -> bool:
    """Whether a generated filter key takes several values."""
    return _split_key(key)[1] in {"in", "not_in"}


def _apply_sorting(
    values: _abc.Mapping[str, Any],
    stmt: Select[Any],
    model: type[DeclarativeBase],
    schema_cls: _SchemaClass,
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
    sort = values.get(sort_query_param) or ""
    # a sort field of your own can be a list, such as ``sort: list[str]``
    sort = ",".join(map(str, sort)) if isinstance(sort, list) else str(sort)
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


def _written_name(schema_cls: _SchemaClass) -> str:
    """The name of the class that the user wrote: for a response schema that
    Restly derived, the main schema."""
    return schema_cls.__dict__.get("__restly_response_of__", schema_cls).__name__


def _iter_fields_including_nested(
    schema_cls: _SchemaClass,
    prefix: str = "",
    python_prefix: str = "",
    _followed: frozenset[_SchemaClass] = frozenset(),
) -> Iterator[tuple[str, str, FieldInfo]]:
    """Each field that can have keys, with its path in the URL and its path
    in Python names, such as ``("vendor.createdAt", "vendor.created_at",
    field)``."""
    for name, field in schema_cls.model_fields.items():
        # A field whose value a response never shows, such as a WriteOnly
        # field or a SecretStr, is never a filter or sort key.
        if _hides_value(field):
            continue
        public_name = _public_name(name, field)
        # ``__`` splits a field from an operator, and ``.`` walks to a related
        # field, so a name that holds either cannot be read from a key. Such a
        # field gets no keys, and the view still starts.
        if "__" in public_name or "." in public_name:
            continue
        full_name = f"{prefix}.{public_name}" if prefix else public_name
        python_name = f"{python_prefix}.{name}" if python_prefix else name
        nested = _get_nested_schema(field)
        if nested and not _is_idref_field(field):
            # Follow each nested schema once in a path. A schema that names
            # itself, such as ``parent: CategorySchema | None``, then gets
            # ``parent.name`` but not ``parent.parent.name``, and the walk ends.
            if nested not in _followed:
                yield from _iter_fields_including_nested(
                    nested, full_name, python_name, _followed | {nested}
                )
        else:
            yield full_name, python_name, field


def _resolve_field_name(schema_cls: _SchemaClass, public_name: str) -> str | None:
    """Return the Python field name for a public URL field name.

    The public name is the name that a response shows: see
    :func:`_public_name`. An aliased field is *only* reachable by that name —
    Python field names are never part of the public URL contract, even when
    the schema has ``populate_by_name=True`` (which only affects how Pydantic
    parses input bodies, not the generated list params).

    A field whose value a response never shows never resolves: a WriteOnly
    field, one with ``exclude=True``, or one of Pydantic's secret types.
    Filtering or sorting on it would let a client read back the value.
    """
    resolved: str | None = None
    for field_name, field in schema_cls.model_fields.items():
        if _public_name(field_name, field) == public_name:
            resolved = field_name
            break
    if resolved is not None and _hides_value(schema_cls.model_fields[resolved]):
        return None
    return resolved


def _join_column(
    stmt: Select[Any],
    model: type[DeclarativeBase],
    column_path: str,
    schema_cls: _SchemaClass,
    aliases: _JoinAliases,
    *,
    kind: str = "filter",
) -> tuple[Select[Any], InstrumentedAttribute[Any]]:
    """Join each relationship path once and return its aliased leaf column.

    Each join is a LEFT OUTER JOIN, so a row without the related row has NULL
    in the column, in a filter and in a sort.
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
                source, getattr(source, relationship.key).of_type(target), isouter=True
            )
            aliases[path] = target
        source = target
    return stmt, getattr(source, column.key)


def _resolve_column(
    model: type[DeclarativeBase],
    column_path: str,
    schema_cls: _SchemaClass,
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
    current_schema: _SchemaClass | None = schema_cls
    name = column_path
    while "." in name:
        relation, _, name = name.partition(".")
        if current_schema is None:
            raise unknown
        field_name = _resolve_field_name(current_schema, relation)
        if field_name is None:
            raise unknown
        rel = _class_attribute(current_model, field_name)
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
    column = _class_attribute(current_model, field_name)
    if (
        column is None
        or not isinstance(column, InstrumentedAttribute)
        or not isinstance(column.property, ColumnProperty)
    ):
        raise unknown
    return joins, cast(InstrumentedAttribute[Any], column)


def _apply_filtering(
    values: _abc.Mapping[str, Any],
    stmt: Select[Any],
    model: type[DeclarativeBase],
    schema_cls: _SchemaClass,
    *,
    validated: bool = False,
    aliases: _JoinAliases | None = None,
    pagination: _AnyPagination = _DEFAULT_PAGINATION,
    sort_query_param: str = _SORT_QUERY_PARAM,
) -> Select[Any]:
    """Apply ``key=value`` and ``key__op=value`` filters to ``stmt``. All
    filters are combined with AND.

    :param values: The values by key, as :func:`_list_params_values` gives them.
    :param validated: The values come from generated list params, whose keys
        validated them. So only the form of the column is left. Other values
        still go through the validation of their field.
    """
    if aliases is None:
        aliases = {}
    reserved = _reserved_names(pagination, sort_query_param)
    filters: dict[InstrumentedAttribute[Any], list[ColumnElement[Any]]] = defaultdict(
        list
    )

    for key, value in values.items():
        if key in reserved:
            continue

        column_name, op = _split_key(key)
        op = op or "eq"

        stmt, column = _join_column(stmt, model, column_name, schema_cls, aliases)
        parse = (
            _column_value_parser(column, key)
            if validated
            else _filter_value_parser(schema_cls, column_name, column)
        )
        if op in {"in", "not_in"}:
            # generated keys have split their values already
            several = validated and isinstance(value, list)
            terms = [value if several else _listed_values(value)]
        else:
            # a list field of your own, such as ``name__icontains: list[str]``,
            # needs each of its values
            terms = value if isinstance(value, list) else [value]
        for term in terms:
            clause = _filter_clause(column, op, term, parse, key)
            if clause is not None:
                filters[column].append(clause)

    for column, clauses in filters.items():
        and_clause = clauses[0] if len(clauses) == 1 else sqlalchemy.and_(*clauses)
        stmt = stmt.where(and_clause)
    return stmt


def _filter_clause(
    column: InstrumentedAttribute[Any],
    op: str,
    value: Any,
    parse: Callable[[Any], Any],
    key: str,
) -> ColumnElement[Any] | None:
    """The condition of one filter key, or ``None`` when it asks for nothing,
    as an empty text search does.

    :raises BadQueryParam: the value fails, or the operator does not exist.
    """
    if op == "isnull":
        try:
            is_null = _BOOL_ADAPTER.validate_python(value)
        except pydantic.ValidationError as exc:
            raise BadQueryParam(
                f"Invalid filter value for {key!r}: {value!r}. Use true or false."
            ) from exc
        return column.is_(None) if is_null else column.isnot(None)
    if op == "icontains":
        # LIKE takes the text as it is, without the field's validation
        try:
            text = _search_value(value)
        except ValueError as exc:
            raise BadQueryParam(
                f"Invalid filter value for {key!r}: {value!r}. "
                f"{value_error_reason(exc)}"
            ) from exc
        if text is None:
            return None
        return column.ilike(f"%{_escape_like_value(text)}%", escape="\\")
    if op in {"in", "not_in"}:
        items = [parse(item) for item in value]
        if any(item is None for item in items):
            # SQL compares no value with NULL: NOT IN ('a', NULL) is never true
            raise BadQueryParam(
                f"Invalid filter value for {key!r}: {value!r}. {_NULL_IN_LIST}"
            )
        if op == "in":
            return column.in_(items)
        # NULL is none of these values, so the row stays; SQL leaves it out
        return sqlalchemy.or_(column.is_(None), column.not_in(items))

    parsed = parse(value)
    if op == "eq":
        return column == parsed
    if op == "ne":
        # NULL is not this value, so the row stays; SQL leaves it out
        return column.is_distinct_from(parsed)
    if op == "gte":
        return column >= parsed
    if op == "lte":
        return column <= parsed
    if op == "gt":
        return column > parsed
    if op == "lt":
        return column < parsed
    raise BadQueryParam(f"Unsupported filter operator: {op!r}")


_BOOL_ADAPTER = pydantic.TypeAdapter(bool)


class _BadValue(Exception):
    """A filter value that fails: the client's error.

    A generated key reports it as a ``422``. :class:`FilterKey` reports it as a
    ``400``, because such a key is inside the value of another key.
    """

    def __init__(self, error_type: str, reason: str) -> None:
        super().__init__(reason)
        #: The type of the error, as in Pydantic.
        self.error_type = error_type
        #: Why the value fails, such as ``Input should be a valid integer``.
        self.reason = reason


def _enum_value(enum_type: sqlalchemy.Enum, value: Any) -> Any:
    """``value`` as a column of ``enum_type`` takes it: one of its labels, or
    a member of its enum class.

    A value that equals a member, such as ``1`` for a member of an
    ``IntEnum``, becomes that member: SQLAlchemy binds a plain number as a
    number, and the column holds labels. SQLAlchemy passes any other string
    on, and a native ENUM on PostgreSQL rejects it with an error, which would
    be a 500 for the client.

    :raises _BadValue: the value is no label of the column.
    """
    if value in enum_type.enums:
        return value
    for member in enum_type.enum_class or ():
        if value == member:
            return member
    raise _BadValue("enum", f"Use one of: {', '.join(enum_type.enums)}.")


def _filter_value_parser(
    schema_cls: _SchemaClass, column_name: str, column: Any
) -> Callable[[Any], Any]:
    """The parser of the values of a generated filter key, also a dotted one;
    see :func:`_value_parser`.

    :param column: The column, or ``None`` to leave each value as the field
        gives it.
    """
    field_schema, field_name = _field_of(schema_cls, column_name)
    return _value_parser(field_schema, field_name, column, column_name)


def _field_of(schema_cls: _SchemaClass, column_name: str) -> tuple[_SchemaClass, str]:
    """The schema that holds the field of a filter key, also of a dotted
    one, and the Python name of the field.

    :raises BadQueryParam: ``Unknown filter key``.
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
    return schema_cls, field_name


def _value_parser(
    schema_cls: _SchemaClass, field_name: str, column: Any, key: str
) -> Callable[[Any], Any]:
    """The parser of the values of one filter key: each value as a value for
    ``column``. See :func:`_value_reader` and :func:`_column_step`.

    The generated filters and
    :class:`~fastapi_restly.query.FilterKey` build one parser for each key,
    so the work for the key is done once.

    :param schema_cls: The schema that holds the field.
    :param column: The column, or ``None`` to leave each value as the field
        gives it.
    :param key: The filter key that the client used, for the message.
    """
    read = _value_reader(schema_cls, field_name, column)
    to_column = _column_step(column)

    def parse(value: Any) -> Any:
        """``value`` as a value for the column.

        :raises BadQueryParam: when the value fails one of the steps. The
            message has the reason, such as Pydantic's
            ``Input should be a valid integer``.
        """
        try:
            return to_column(read(value))
        except _BadValue as exc:
            raise _invalid_filter_value(key, value, exc) from exc

    return parse


def _column_value_parser(column: Any, key: str) -> Callable[[Any], Any]:
    """The parser of a value that its key has validated: only the form of the
    column is left. See :func:`_column_step`."""
    to_column = _column_step(column)

    def parse(value: Any) -> Any:
        try:
            return to_column(value)
        except _BadValue as exc:
            raise _invalid_filter_value(key, value, exc) from exc

    return parse


def _invalid_filter_value(key: str, value: Any, exc: _BadValue) -> BadQueryParam:
    return BadQueryParam(f"Invalid filter value for {key!r}: {value!r}. {exc.reason}")


def _checked_reader(
    schema_cls: _SchemaClass,
    field_name: str,
    column: Any,
    *,
    isnull_key: str | None = None,
) -> Callable[[Any], Any]:
    """The value of a generated key: the value of its field, as code reads it
    from the list params, such as a ``Decimal``. It also checks that the
    column can take the value, so a bad value fails with the other values of
    the request.

    :param isnull_key: The ``__isnull`` key of the field, for the messages.
    """
    read = _value_reader(schema_cls, field_name, column, isnull_key=isnull_key)
    to_column = _column_step(column)

    def read_and_check(value: Any) -> Any:
        result = read(value)
        to_column(result)
        return result

    return read_and_check


def _value_reader(
    schema_cls: _SchemaClass,
    field_name: str,
    column: Any,
    *,
    isnull_key: str | None = None,
) -> Callable[[Any], Any]:
    """A function that gives a value the type and the validators of its field.

    A value goes through the validation of its field, without the bounds; see
    :func:`_field_validator`: a text from a URL, or a value from JSON. ``None``
    stays ``None``, for ``IS NULL``. A value that a validator turns into
    ``None``, such as an empty text, fails: a filter needs a value, and the
    client did not ask for NULL. A number must be between ``-1e50`` and
    ``1e50``; see :func:`_check_number_size`. A ``Literal`` of numbers and an
    enum whose values are numbers read the number from the text first; see
    :func:`_has_number_labels`. An ``IDRef`` gives its id. A JSON column
    takes only ``None``: it holds JSON text, so SQL cannot compare it with a
    value. A field of type ``Any``, such as the ``id`` of ``fr.IDSchema``, and
    the id of a reference get the Python type of the column, such as a
    ``UUID`` from its text.

    The function raises :class:`_BadValue` for a value that fails. Only a
    validation error is the client's fault. Another exception from a validator
    is a bug, as it is for a request body.

    :param isnull_key: The ``__isnull`` key of the field, for the messages.
    """
    validator = _field_validator(schema_cls, field_name)
    becomes_null = (
        "The value becomes null after validation, and a filter needs a value."
    )
    if isnull_key is not None:
        becomes_null += f" To find the rows where it is null, send {isnull_key}=true."
    column_type = getattr(column, "type", None)
    json_column = _has_json_type(column)
    annotation = _unwrap_type(schema_cls.model_fields[field_name].annotation)[0]
    # A field of type Any, such as the id of fr.IDSchema, gives the value as
    # it came, and so does a reference for its id.
    untyped = annotation is Any
    number_labels = _has_number_labels(annotation)
    # a column of SQLAlchemy's Enum type takes its labels; see _column_step
    to_python_type = (
        None if _enum_type(column) is not None else _python_type_parser(column_type)
    )

    def read(value: Any) -> Any:
        if json_column and value is not None:
            raise _BadValue(
                "value_error",
                "A filter on a JSON column can only check whether it is null.",
            )
        if number_labels and isinstance(value, str):
            value = _number_from_text(value)
        try:
            values, _, _ = validator.validate_python({field_name: value})
            result = values[field_name]
            if result is None:
                if value is not None:
                    raise _BadValue("value_error", becomes_null)
                return None
            # An IDRef[T] FK field validates to an IDRef object; the SQL bind
            # value is its scalar id, not the reference wrapper (which cannot bind).
            reference = isinstance(result, IDSchema)
            if reference:
                result = result.id
            if (untyped or reference) and to_python_type is not None:
                result = to_python_type(result)
            _check_number_size(result)
            return result
        except pydantic.ValidationError as exc:
            error = exc.errors()[0]
            raise _BadValue(error["type"], error["msg"]) from exc

    return read


def _has_number_labels(annotation: Any) -> bool:
    """Whether a field takes labels that are all numbers: a ``Literal`` such
    as ``Literal[1, 2]``, or an enum whose values are all numbers.

    A value from a URL is text, and Pydantic does not read ``"1"`` as ``1``
    for such a field, as it does for an ``IntEnum``. Without help, the
    filter key of such a field would refuse each value. A ``bool`` is no
    number here, and ``Literal[1, "1"]`` stays as it is.
    """
    if get_origin(annotation) is Literal:
        values: _abc.Sequence[Any] = get_args(annotation)
    elif isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        values = [member.value for member in annotation]
    else:
        return False
    return bool(values) and all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in values
    )


def _number_from_text(text: str) -> Any:
    """The number in ``text``: an ``int``, else a ``float``. Text that holds no
    number stays as it is, and the validation of the field refuses it."""
    for parse in (int, float):
        try:
            return parse(text)
        except ValueError:
            pass
    return text


#: The biggest number that a filter takes, as in django-filter. A filter
#: leaves out the bounds of its field, and without a limit a huge number
#: reaches the database: PostgreSQL then fails on ``1e400`` for a ``Float``
#: column, or on a ``Decimal`` with many digits, which is a 500.
_NUMBER_LIMIT = 1e50


def _check_number_size(value: Any) -> None:
    """Refuse a number above ``1e50`` or below ``-1e50``. NaN stays: a field
    that allows it has a reason.

    :raises _BadValue: the number is too big, or an infinity.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, _decimal.Decimal)):
        return
    if isinstance(value, _decimal.Decimal) and value.is_nan():
        return
    if abs(value) > _NUMBER_LIMIT:
        raise _BadValue(
            "value_error", "A number in a filter must be between -1e50 and 1e50."
        )


def _column_step(column: Any) -> Callable[[Any], Any]:
    """A function that gives a value the form of its column.

    A column of SQLAlchemy's ``Enum`` type takes only its labels. Any other
    value goes through ``column_value``: a text column takes text, a time gets
    the form of its column, a number must fit an integer column, and text must
    hold no NUL character. Its length is not checked: the filter compares the
    text, and PostgreSQL compares text of any length.

    The function raises :class:`_BadValue` for a value that does not fit.
    """
    column_type = getattr(column, "type", None)
    enum_type = _enum_type(column)

    def to_column(value: Any) -> Any:
        if value is None:
            return None
        if enum_type is not None:
            return _enum_value(enum_type, value)
        try:
            return column_value(column_type, value)
        except ValueError as exc:
            raise _BadValue("value_error", value_error_reason(exc)) from exc

    return to_column


def _value_json_schema(
    schema_cls: _SchemaClass, field_name: str, column: Any
) -> dict[str, Any]:
    """The JSON schema of one value of a filter key: the type of the field,
    without its bounds, as :func:`_field_validator` validates it. A field of
    type ``Any`` and a reference have the type of the column."""
    field = schema_cls.model_fields[field_name]
    if _unwrap_type(field.annotation)[0] is Any or _is_idref_field(field):
        return _column_json_schema(column)
    field_schema, definitions, _ = _field_core_schema(schema_cls, field_name)
    while field_schema.get("type") in {"default", "nullable"}:
        field_schema = field_schema["schema"]
    if definitions:
        field_schema = {
            "type": "definitions",
            "schema": field_schema,
            "definitions": definitions,
        }
    try:
        generated = GenerateJsonSchema().generate(field_schema)
    except (PydanticInvalidForJsonSchema, PydanticOmit):
        # PydanticOmit: SkipJsonSchema on the field, which OpenAPI leaves out
        return {}
    schema = _inline_definitions(generated, generated.get("$defs", {}))
    schema.pop("$defs", None)
    return _value_schema_only(schema)


def _column_json_schema(column: Any) -> dict[str, Any]:
    """The JSON schema of the Python type of ``column``, or ``{}`` when the
    column names none or Pydantic does not know the type."""
    try:
        python_type = getattr(column, "type", None).python_type  # type: ignore[union-attr]
        schema = pydantic.TypeAdapter(python_type).json_schema()
    except (
        AttributeError,
        NotImplementedError,
        pydantic.PydanticSchemaGenerationError,
        PydanticInvalidForJsonSchema,
    ):
        return {}
    schema = _inline_definitions(schema, schema.get("$defs", {}))
    schema.pop("$defs", None)
    return _value_schema_only(schema)


def _value_schema_only(schema: dict[str, Any]) -> dict[str, Any]:
    """``schema`` without the names of its class and without ``null``. The key
    has its own title and description, and can always be left out."""
    schema = {k: v for k, v in schema.items() if k not in {"title", "description"}}
    options = schema.get("anyOf")
    if options is not None and set(schema) == {"anyOf"}:
        options = [option for option in options if option != {"type": "null"}]
        return options[0] if len(options) == 1 else {"anyOf": options}
    return schema


def _inline_definitions(
    schema: Any, definitions: dict[str, Any], _inside: frozenset[str] = frozenset()
) -> Any:
    """``schema`` with each ``#/$defs/...`` reference replaced by its target.

    A type that holds itself, such as a tree, would never end. Its reference
    inside itself becomes a schema without a type.

    :param _inside: The definitions that the walk is inside.
    """
    if isinstance(schema, list):
        return [_inline_definitions(item, definitions, _inside) for item in schema]
    if not isinstance(schema, dict):
        return schema
    inlined = {
        key: _inline_definitions(value, definitions, _inside)
        for key, value in schema.items()
        if key != "$ref"
    }
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        name = reference.removeprefix("#/$defs/")
        if name in _inside:
            return inlined
        target = _inline_definitions(definitions[name], definitions, _inside | {name})
        return {**target, **inlined}
    return inlined


def _python_type_parser(column_type: Any) -> Callable[[Any], Any] | None:
    """A function that gives a value the Python type of ``column_type``, such
    as a ``UUID`` from its text, or ``None`` when the type names none.

    The function raises ``pydantic.ValidationError`` for a value that is not
    valid for the type.
    """
    try:
        python_type = column_type.python_type
    except (AttributeError, NotImplementedError):
        return None
    adapter = _python_type_adapter(python_type)

    def to_python_type(value: Any) -> Any:
        if isinstance(value, python_type):
            return value
        return adapter.validate_python(value)

    return to_python_type


@functools.cache
def _python_type_adapter(python_type: type) -> pydantic.TypeAdapter[Any]:
    """The adapter that validates a value as ``python_type``."""
    if python_type is str:
        # A number filters a text column: JSON has 5 for the text "5".
        config = pydantic.ConfigDict(coerce_numbers_to_str=True)
        return pydantic.TypeAdapter(str, config=config)
    try:
        return pydantic.TypeAdapter(python_type)
    except pydantic.PydanticSchemaGenerationError:
        # A type that Pydantic does not know: call it with the value.
        def construct(value: Any) -> Any:
            try:
                return python_type(value)
            except (TypeError, ArithmeticError) as exc:
                raise ValueError(str(exc)) from exc

        return pydantic.TypeAdapter(Annotated[Any, pydantic.AfterValidator(construct)])


#: The settings of a type in Pydantic's core schema that only refuse a value.
_REFUSING_SETTINGS = frozenset(
    {
        "gt",
        "ge",
        "lt",
        "le",
        "multiple_of",
        "max_digits",
        "decimal_places",
        "min_length",
        "max_length",
        "pattern",
        "now_op",
        "now_utc_offset",
        "tz_constraint",
        "allowed_schemes",
        "host_required",
        "version",
        "strict",
    }
)

#: The types of Pydantic's core schema that hold one value.
_SINGLE_VALUE_TYPES = frozenset(
    {
        "bool",
        "int",
        "float",
        "decimal",
        "str",
        "bytes",
        "date",
        "time",
        "datetime",
        "timedelta",
        "uuid",
        "url",
        "multi-host-url",
        "enum",
    }
)

#: Where a part of Pydantic's core schema holds other parts.
_INNER_SCHEMA_KEYS = (
    "schema",
    "lax_schema",
    "strict_schema",
    "json_schema",
    "python_schema",
)


def _without_refusals(schema: Any) -> Any:
    """``schema``, a part of Pydantic's core schema, without the settings that
    only refuse a value. What changes a value stays, such as a validator or
    ``strip_whitespace``.

    A bound that Pydantic cannot put on the type itself stays, because it is a
    function in the schema: on a union such as ``Decimal | int``, after a
    validator of your own, or on a type such as ``HttpUrl``.
    """
    if not isinstance(schema, dict):
        return schema
    copied = dict(schema)
    kind = copied.get("type")
    if kind in _SINGLE_VALUE_TYPES:
        for setting in _REFUSING_SETTINGS:
            copied.pop(setting, None)
        # False refuses infinity and NaN, and True allows them on a Decimal
        if copied.get("allow_inf_nan") is False:
            del copied["allow_inf_nan"]
    for key in _INNER_SCHEMA_KEYS:
        if key in copied and kind != "model":
            copied[key] = _without_refusals(copied[key])
    if isinstance(copied.get("steps"), list):
        copied["steps"] = [_without_refusals(step) for step in copied["steps"]]
    if isinstance(copied.get("choices"), list):
        copied["choices"] = [
            (_without_refusals(choice[0]), choice[1])
            if isinstance(choice, tuple)
            else _without_refusals(choice)
            for choice in copied["choices"]
        ]
    return copied


def _field_core_schema(
    schema_cls: _SchemaClass, field_name: str
) -> tuple[Any, Any, Any]:
    """The core schema of one field, without the settings that only refuse a
    value; see :func:`_without_refusals`. Also the definitions, without them
    too, and the config of the model.
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
    if definitions:
        # a type alias that two fields use, such as a bounded number, is a
        # definition
        definitions = [_without_refusals(definition) for definition in definitions]
    return _without_refusals(node["fields"][field_name]["schema"]), definitions, config


@functools.cache
def _field_validator(schema_cls: _SchemaClass, field_name: str) -> SchemaValidator:
    """A validator for a one-field mapping, cut from the model's core schema.

    The named field supplies ``ValidationInfo.field_name``. No other fields
    are validated, so ``ValidationInfo.data`` is empty.

    The type, the field validators and the model config apply. Bounds do not,
    such as ``gt`` or ``min_length``: a filter compares a value, and does not
    store it, so ``?price__gte=0`` is fine on a field with ``gt=0``. What
    changes a value stays. Model validators do not apply either. A filter value
    is one column, so a cross-field rule has nothing to compare against and
    would only reject legal filters (as validating through
    ``validate_assignment`` on a ``model_construct()`` skeleton did).

    A number becomes text for a text field. A value from a URL is text
    already, and JSON, such as react-admin's ``filter``, has ``5`` for the
    text ``"5"``. For the same reason a value is never strict.
    """
    field_core, definitions, config = _field_core_schema(schema_cls, field_name)
    field_schema: Any = core_schema.model_fields_schema(
        {field_name: core_schema.model_field(field_core)}
    )
    if definitions:
        # A field typed with a class the schema uses more than once is a
        # definition-ref; it validates only next to the definitions.
        field_schema = {
            "type": "definitions",
            "schema": field_schema,
            "definitions": definitions,
        }
    # the bounds of the model config go too
    kept = {
        name: value
        for name, value in (config or {}).items()
        if name not in {"str_max_length", "str_min_length"}
    }
    filter_config = cast(
        core_schema.CoreConfig, {**kept, "coerce_numbers_to_str": True, "strict": False}
    )
    return SchemaValidator(field_schema, filter_config)


def _get_nested_schema(field: FieldInfo | None) -> _SchemaClass | None:
    if field is None:
        return None
    annotation, _ = _unwrap_type(field.annotation)
    if isinstance(annotation, type) and issubclass(annotation, pydantic.BaseModel):
        return annotation
    return None
