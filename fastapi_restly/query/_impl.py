import collections.abc as _abc
import datetime as _dt
import decimal as _decimal
import functools
import uuid as _uuid
import weakref
from collections import defaultdict
from typing import (
    Annotated,
    Any,
    Callable,
    Iterator,
    Optional,
    cast,
    get_args,
    get_origin,
)

import pydantic
import sqlalchemy
from pydantic import Field
from pydantic.fields import FieldInfo
from pydantic_core import SchemaValidator, core_schema
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
)
from ..exc import BadQueryParam
from ..schemas._base import (
    IDRef,
    IDSchema,
    _schema_role_name,
    _unwrap_optional_annotation,
    is_writeonly_field,
)
from ._shared import _append_pk_tiebreak, _escape_like_value

SchemaType = type[pydantic.BaseModel]
_JoinAliases = dict[tuple[str, ...], AliasedClass[Any]]

_AnyPagination = NumberedPagination | NoPagination | None

#: The pagination that each generated list params model was created with, so
#: :func:`apply_list_params` and view registration can read it back.
_CREATED_WITH: "weakref.WeakKeyDictionary[type, _AnyPagination]" = (
    weakref.WeakKeyDictionary()
)


class _FromParams:
    """The default of :func:`apply_list_params`'s ``pagination``: the one its
    params model was created with."""

    def __repr__(self) -> str:
        # the default as the API reference shows it; the docstring explains it
        return "..."


_FROM_PARAMS = _FromParams()
_UNKNOWN = object()


def _created_with(params_model: type) -> Any:
    """The pagination that generated list params were created with, or
    ``_UNKNOWN`` for hand-written ones."""
    return _CREATED_WITH.get(params_model, _UNKNOWN)


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


def _reserved_names(pagination: _AnyPagination) -> frozenset[str]:
    """Query parameters the schema produces besides filters.

    A filter column whose public name is one of these would shadow pagination
    or sort, which would silently break the endpoint contract. Treated as a
    hard error.
    """
    names = {_SORT_QUERY_PARAM}
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
    annotation = _unwrap_optional_annotation(field.annotation)
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
    annotation = _unwrap_optional_annotation(field.annotation)
    return isinstance(annotation, type) and issubclass(annotation, IDRef)


def _supports_scalar_operators(field: FieldInfo) -> bool:
    """False for collection-typed fields (``dict``, ``list[str]``, ``Sequence``,
    ...) that no query-string value can coerce into: ``eq``/``__in``/``__ne``
    (and range) filters on such a field would always 400 at request time, so
    only ``__isnull`` is generated -- the same advertise-only-what-works
    principle applied to non-column fields.

    The check mirrors what the request path's ``_parse_value`` (full Pydantic
    field validation) will accept. ``Annotated`` wrappers are unwrapped even
    inside ``Optional`` (``conlist(...)`` produces those), and a
    ``pydantic.Json[...]`` field counts as scalar: its validation parses the
    query string as JSON, so its filters work. ``str``/``bytes`` are
    collections in the abc sense but coerce fine, so they stay scalar.
    """
    annotation = field.annotation
    metadata: list[Any] = list(field.metadata)
    while True:
        annotation = _unwrap_optional_annotation(annotation)
        if get_origin(annotation) is Annotated:
            annotation, *extra = get_args(annotation)
            metadata.extend(extra)
            continue
        break
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
    annotation = _unwrap_optional_annotation(field.annotation)
    if annotation is bool:
        return False
    if not isinstance(annotation, type):
        return True
    if issubclass(annotation, bool):
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
    schema: SchemaType,
    model: type[DeclarativeBase],
    *,
    pagination: _AnyPagination = _DEFAULT_PAGINATION,
) -> SchemaType:
    """
    Build the list params: a Pydantic model that describes and validates the
    URL query parameters of a list endpoint. A view keeps it in
    :attr:`~fastapi_restly.views.BaseRestView.schema_list_params`.

    The generated model accepts pagination (``page``, ``page_size``), sorting
    (``sort``), and one filter parameter per field of ``schema`` that maps to
    a filterable column on ``model`` -- with optional ``__in``/``__ne``/``__gte``/
    ``__lte``/``__gt``/``__lt``/``__isnull``/``__contains``/``__icontains``
    suffixes. Fields that do not resolve to a column (relationship/collection
    fields, or reference traversals the request path would reject) get no filter
    params, so the generated schema -- and the OpenAPI it produces -- no longer
    advertises filters for fields that are not filterable at all. WriteOnly
    fields get none either, since a filter on one would leak its value. Fields
    whose type is a collection (``dict``/``list``, e.g. ``JSON`` or ``ARRAY``
    columns) generate only ``__isnull``: a query-string value cannot coerce into
    them, so every other operator would fail at request time.

    With a ``pagination`` (the default is ``page`` and ``page_size``), its two
    query parameters are added under its names and validated by Pydantic with
    bounds: the page at least 1 and at most ``max_page`` when set, the page
    size from 1 to ``max_page_size``. The resulting SQL offset must also fit in
    a signed 64-bit integer. Out-of-range values produce a standard 422
    response from FastAPI. With a :class:`~fastapi_restly.views.NoPagination`
    or ``None``, no pagination parameters are emitted at all -- the endpoint
    returns every matching row -- while sorting and filtering stay available.

    :param schema: The view's schema. Its fields decide the available
        filter parameters.
    :param model: The SQLAlchemy model the list endpoint queries. Used to verify
        each field resolves to a filterable column; non-column fields are
        omitted from the generated params.
    :param pagination: The view's pagination settings. A ``NoPagination`` or
        ``None`` is an unpaginated view, which returns the full result set.
    """
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
    fields[_SORT_QUERY_PARAM] = (
        Annotated[
            Optional[str],
            Field(
                description=(
                    "Comma-separated list of fields to sort by. Prefix a "
                    "field with ``-`` for descending order. Example: "
                    "``-created_at,name``."
                )
            ),
        ],
        None,
    )
    reserved = _reserved_names(pagination)
    for name, field in _iter_fields_including_nested(schema):
        if name in reserved:
            raise ValueError(
                f"List params for {schema.__name__!r} cannot expose "
                f"field {name!r}: it collides with a reserved pagination/sort "
                "parameter. Add a Pydantic alias to expose it as a filter, or "
                "rename the pagination parameter."
            )

        # Only emit params for fields that resolve to a filterable column on the
        # model -- using the very predicate the request path applies. A
        # relationship/collection field (e.g. ``books: list[BookRef]``) or a
        # reference traversal that does not resolve would otherwise advertise
        # filters in OpenAPI that always 400 at request time.
        try:
            _resolve_column(model, name, schema)
        except BadQueryParam:
            continue

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
                        Optional[list[str]],
                        Field(description=f"``{name} {sql} value``."),
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

    schema_name = _schema_role_name(schema, "ListParams")
    validators = (
        {"_validate_pagination_offset": _pagination_offset_validator(pagination)}
        if isinstance(pagination, NumberedPagination)
        else {}
    )
    params_model = pydantic.create_model(  # type: ignore[call-overload]
        schema_name, __validators__=validators, **fields
    )
    _CREATED_WITH[params_model] = pagination
    return params_model


def apply_list_params(
    query: Select[Any],
    list_params: pydantic.BaseModel | QueryParams,
    model: type[DeclarativeBase],
    schema: SchemaType,
    *,
    pagination: _AnyPagination = cast(Any, _FROM_PARAMS),
) -> Select[Any]:
    """
    Apply pagination, sorting, and filtering to ``query`` using validated
    list params.

    ``list_params`` is normally an instance of the model returned by
    :func:`derive_schema_list_params`. The default list endpoints always pass a
    validated instance, so pagination/filter bounds have already been checked.
    ``schema`` is the view's schema, which decides the fields a client can
    filter and sort on.

    The arguments match the view method
    :meth:`~fastapi_restly.views.RestView.apply_list_params`, which calls this
    function with the view's model, schema and pagination.

    A raw :class:`~starlette.datastructures.QueryParams` is also accepted
    for callers that build the query parameters programmatically.
    **Raw inputs bypass schema validation** — the caller is responsible
    for verifying ``page``/``page_size`` ranges and any per-view bounds
    (``max_page_size``); this function only performs the minimum coercion
    needed to apply the SQL clauses.

    ``pagination`` names the page and page-size parameters to read. By default
    it is the pagination that :func:`derive_schema_list_params` created the
    list params model with, and ``page`` and ``page_size`` for raw
    ``QueryParams`` or a hand-written model. A ``NoPagination`` or ``None``
    applies no ``LIMIT``/``OFFSET``, and so does a missing page-size key.

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
    if isinstance(pagination, _FromParams):
        pagination = (
            _CREATED_WITH.get(type(list_params), _DEFAULT_PAGINATION)
            if isinstance(list_params, pydantic.BaseModel)
            else _DEFAULT_PAGINATION
        )
    query_params = _coerce_to_query_params(list_params)
    aliases: _JoinAliases = {}
    query = _apply_filtering(
        query_params, query, model, schema, aliases=aliases, pagination=pagination
    )
    query = _apply_sorting(query_params, query, model, schema, aliases=aliases)
    if isinstance(pagination, NumberedPagination):
        query = _apply_pagination(query_params, query, pagination)
    return query


def _coerce_to_query_params(
    list_params: pydantic.BaseModel | QueryParams,
) -> QueryParams:
    """Normalise validated list params or raw QueryParams to QueryParams.

    When a dumped field is a list (e.g. a repeated ``name__contains``), each
    element is expanded to its own ``(key, value)`` tuple so that
    ``QueryParams.multi_items()`` later returns the original repeated values.
    """
    if isinstance(list_params, QueryParams):
        return list_params
    if isinstance(list_params, pydantic.BaseModel):
        dumped = list_params.model_dump(exclude_none=True, by_alias=True, mode="json")
        items: list[tuple[str, str]] = []
        for key, value in dumped.items():
            if isinstance(value, list):
                items.extend((key, str(item)) for item in value)
            else:
                items.append((key, str(value)))
        return QueryParams(items)
    return QueryParams(list_params)


def _apply_pagination(
    query_params: QueryParams, select_query: Select[Any], pagination: NumberedPagination
) -> Select[Any]:
    page_size = _get_int(query_params, pagination.page_size_query_param)
    if page_size is None:
        return select_query
    page = _get_int(query_params, pagination.page_query_param) or 1
    offset = (page - 1) * page_size
    return select_query.limit(page_size).offset(offset)


def _get_int(query_params: QueryParams, param_name: str) -> Optional[int]:
    value = query_params.get(param_name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise BadQueryParam(
            f"Invalid value for URL query parameter {param_name}: "
            f"{value} is not an integer"
        )


def _apply_sorting(
    query_params: QueryParams,
    select_query: Select[Any],
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
    *,
    aliases: _JoinAliases | None = None,
) -> Select[Any]:
    if aliases is None:
        aliases = {}
    sorted_on: list[InstrumentedAttribute[Any]] = []
    for column_name in (query_params.get("sort") or "").split(","):
        if not column_name:
            continue
        order = sqlalchemy.asc
        if column_name.startswith("-"):
            order = sqlalchemy.desc
            column_name = column_name[1:]
        select_query, column = _join_column(
            select_query, model, column_name, schema_cls, aliases
        )
        select_query = select_query.order_by(order(column))
        sorted_on.append(column)
    return _append_pk_tiebreak(select_query, model, sorted_on)


def _iter_fields_including_nested(
    schema_cls: SchemaType, prefix: str = ""
) -> Iterator[tuple[str, FieldInfo]]:
    for name, field in schema_cls.model_fields.items():
        # Never in a response, so never a filter or sort key.
        if is_writeonly_field(schema_cls, name):
            continue
        public_name = field.alias or name
        # Each segment of the public dotted path becomes part of the URL
        # grammar. ``__`` is reserved for operator suffixes (``__gte``,
        # ``__contains``, ...) and ``.`` is reserved for relation traversal,
        # so a segment containing either character would create an
        # ambiguous URL key. Reject at schema-generation time so the
        # collision surfaces during view registration, not at request time.
        if "__" in public_name:
            raise ValueError(
                f"List params for {schema_cls.__name__!r} cannot "
                f"expose field {public_name!r}: ``__`` is reserved for "
                "operator suffixes. Choose a different Pydantic alias."
            )
        if "." in public_name:
            raise ValueError(
                f"List params for {schema_cls.__name__!r} cannot "
                f"expose field {public_name!r}: ``.`` is reserved for "
                "relation traversal. Choose a different Pydantic alias."
            )
        full_name = f"{prefix}.{public_name}" if prefix else public_name
        nested = _get_nested_schema(field)
        if nested and not _is_idref_field(field):
            yield from _iter_fields_including_nested(nested, full_name)
        else:
            yield full_name, field


def _resolve_field_name(schema_cls: SchemaType, public_name: str) -> str | None:
    """Return the Python field name for a public URL field name.

    The public name is the field's alias when one is declared, otherwise the
    field name itself. Aliased fields are *only* reachable by their alias —
    Python field names are never part of the public URL contract, even when
    the schema has ``populate_by_name=True`` (which only affects how Pydantic
    parses input bodies, not the generated list params).

    A WriteOnly field never resolves: filtering or sorting on it would let a
    client read back a value that responses leave out.
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
    if resolved is not None and is_writeonly_field(schema_cls, resolved):
        return None
    return resolved


def _join_column(
    select_query: Select[Any],
    model: type[DeclarativeBase],
    column_path: str,
    schema_cls: SchemaType,
    aliases: _JoinAliases,
) -> tuple[Select[Any], InstrumentedAttribute[Any]]:
    """Join each relationship path once and return its aliased leaf column."""
    relationships, column = _resolve_column(model, column_path, schema_cls)
    source: type[DeclarativeBase] | AliasedClass[Any] = model
    path: tuple[str, ...] = ()
    for relationship in relationships:
        # The full path distinguishes home_city.country from work_city.country
        # and successive hops through a self-referential relationship.
        path += (relationship.key,)
        target = aliases.get(path)
        if target is None:
            target = aliased(relationship.property.mapper)
            select_query = select_query.join_from(
                source, getattr(source, relationship.key).of_type(target)
            )
            aliases[path] = target
        source = target
    return select_query, getattr(source, column.key)


def _resolve_column(
    model: type[DeclarativeBase], column_path: str, schema_cls: SchemaType
) -> tuple[list[InstrumentedAttribute[Any]], InstrumentedAttribute[Any]]:
    """Resolve a (possibly dotted) public column path to its SQLAlchemy column,
    plus the relationship attributes that need to be joined.

    Strict: every path segment must resolve through the schema's public name
    (alias when set, Python field name otherwise). Falling back to a raw
    model attribute lookup would let URLs reach columns the schema didn't
    expose — for example, a Python field name on an aliased schema field —
    and silently bypass the public-name contract.
    """
    joins: list[InstrumentedAttribute[Any]] = []
    current_model = model
    current_schema: SchemaType | None = schema_cls
    name = column_path
    while "." in name:
        relation, _, name = name.partition(".")
        if current_schema is None:
            raise BadQueryParam(f"Invalid attribute in URL query: {column_path}")
        field_name = _resolve_field_name(current_schema, relation)
        if field_name is None:
            raise BadQueryParam(f"Invalid attribute in URL query: {column_path}")
        rel = getattr(current_model, field_name, None)
        if not isinstance(rel, InstrumentedAttribute) or not hasattr(
            rel.property, "mapper"
        ):
            raise BadQueryParam(f"Invalid attribute in URL query: {column_path}")
        joins.append(rel)
        current_model = rel.property.mapper.class_
        current_schema = _get_nested_schema(current_schema.model_fields[field_name])

    if current_schema is None:
        raise BadQueryParam(f"Invalid attribute in URL query: {column_path}")
    field_name = _resolve_field_name(current_schema, name)
    if field_name is None:
        raise BadQueryParam(f"Invalid attribute in URL query: {column_path}")
    column = getattr(current_model, field_name, None)
    if (
        column is None
        or not isinstance(column, InstrumentedAttribute)
        or not isinstance(column.property, ColumnProperty)
    ):
        raise BadQueryParam(f"Invalid attribute in URL query: {column_path}")
    return joins, cast(InstrumentedAttribute[Any], column)


def _apply_filtering(
    query_params: QueryParams,
    select_query: Select[Any],
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
    *,
    aliases: _JoinAliases | None = None,
    pagination: _AnyPagination = _DEFAULT_PAGINATION,
) -> Select[Any]:
    """Apply ``key=value`` and ``key__op=value`` filters to ``select_query``.

    Multiple filters on the same column are AND-combined. Comma-separated
    values within one parameter are OR-combined for ``eq`` (the default),
    mapped to SQL ``IN`` for ``in``, and AND-combined for ``ne`` (so
    ``status__ne=a,b`` means NOT IN (a, b)). For ``contains``/``icontains``
    values are split on whitespace and AND-combined.
    """
    if aliases is None:
        aliases = {}
    reserved = _reserved_names(pagination)
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

        select_query, column = _join_column(
            select_query, model, column_name, schema_cls, aliases
        )
        parser = functools.partial(_parse_value, schema_cls, column_name)

        if op == "isnull":
            try:
                value = pydantic.TypeAdapter(bool).validate_python(raw_value)
            except pydantic.ValidationError as exc:
                raise BadQueryParam(
                    f"Invalid value for URL query parameter {key}"
                ) from exc
            filters[column].append(column.is_(None) if value else column.isnot(None))
            continue

        clause = _build_clause(column, raw_value, op, parser)
        if clause is not None:
            filters[column].append(clause)

    for column, clauses in filters.items():
        and_clause = clauses[0] if len(clauses) == 1 else sqlalchemy.and_(*clauses)
        select_query = select_query.where(and_clause)
    return select_query


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
    if "." in column_name:
        relation, _, column_part = column_name.partition(".")
        relation_field_name = _resolve_field_name(schema_cls, relation) or relation
        field = schema_cls.model_fields.get(relation_field_name)
        nested = _get_nested_schema(field)
        if nested is None:
            raise BadQueryParam(f"Invalid attribute in URL query: {column_name}")
        return _parse_value(nested, column_part, value)

    field_name = _resolve_field_name(schema_cls, column_name)
    if field_name is None:
        raise BadQueryParam(f"Invalid attribute in URL query: {column_name}")

    try:
        values, _, _ = _field_validator(schema_cls, field_name).validate_python(
            {field_name: value}
        )
        result = values[field_name]
    except Exception:
        raise BadQueryParam(f"Invalid attribute in URL query: {column_name}")
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
    annotation = _unwrap_optional_annotation(field.annotation)
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
