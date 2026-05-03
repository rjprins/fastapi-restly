import datetime as _dt
import decimal as _decimal
import functools
import uuid as _uuid
from collections import defaultdict
from typing import Annotated, Any, Callable, Iterator, Optional, cast

import pydantic
import sqlalchemy
from fastapi import HTTPException
from pydantic import Field
from pydantic.fields import FieldInfo
from sqlalchemy import ColumnElement, Select
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.orm.properties import ColumnProperty
from starlette.datastructures import QueryParams

from ._shared import _escape_like_value, _unwrap_optional_annotation

SchemaType = type[pydantic.BaseModel]

#: Default ``page_size`` applied to list endpoints when the client does not
#: send one. ``None`` disables the implicit cap (lists return every matching
#: row and ``page`` is ignored). Override per-view via
#: :attr:`BaseRestView.default_page_size`.
DEFAULT_PAGE_SIZE: int | None = None

#: Maximum ``page_size`` accepted by list endpoints. Values above this are
#: rejected with a 422 by the FastAPI Pydantic-Query validation layer.
#: Override per-view via :attr:`BaseRestView.max_page_size`.
MAX_PAGE_SIZE = 1000

#: Reserved query-parameter names produced by the schema. Filter columns
#: literally named one of these would shadow pagination/sort, which would
#: silently break the endpoint contract. Treated as a hard error.
_RESERVED_NAMES = frozenset({"page", "page_size", "order_by"})

# Types that support SQL ``<``/``<=``/``>``/``>=`` comparisons. Booleans
# deliberately don't — ordering booleans is rarely meaningful and emitting
# ``WHERE active >= true`` raises ``sqlalchemy.exc.ArgumentError`` at query
# time, which would otherwise surface to the client as a 500.
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


def create_list_params_schema(
    schema_cls: SchemaType,
    *,
    default_page_size: int | None = DEFAULT_PAGE_SIZE,
    max_page_size: int = MAX_PAGE_SIZE,
) -> SchemaType:
    """
    Create a Pydantic model that describes and validates URL query parameters
    for list endpoints.

    The generated model accepts pagination (``page``, ``page_size``), sorting
    (``order_by``), and one filter parameter per response-schema field with
    optional ``__ne``/``__gte``/``__lte``/``__gt``/``__lt``/``__isnull``/
    ``__contains`` suffixes.

    ``page`` and ``page_size`` are validated by Pydantic with bounds
    (``page >= 1``, ``1 <= page_size <= max_page_size``); out-of-range values
    produce a standard 422 response from FastAPI.

    Args:
        schema_cls: The response schema whose fields drive the available
            filter parameters.
        default_page_size: Default value for the ``page_size`` parameter.
            ``None`` (the default) means "no implicit page size" — omitting
            ``page_size`` returns every matching row and ``page`` is ignored.
        max_page_size: Upper bound (inclusive) for the ``page_size``
            parameter. Defaults to :data:`MAX_PAGE_SIZE`.
    """
    fields: dict[str, Any] = {
        "page": (
            Annotated[
                int,
                Field(
                    ge=1,
                    description=(
                        "1-based page number. Only takes effect when "
                        "``page_size`` is also set."
                    ),
                ),
            ],
            1,
        ),
        "page_size": (
            Annotated[
                Optional[int],
                Field(
                    ge=1,
                    le=max_page_size,
                    description=(
                        f"Number of items per page (1–{max_page_size}). "
                        "Omit to return every matching row (no implicit cap)."
                    ),
                ),
            ],
            default_page_size,
        ),
        "order_by": (
            Annotated[
                Optional[str],
                Field(
                    description=(
                        "Comma-separated list of fields to sort by. Prefix a "
                        "field with ``-`` for descending order. Example: "
                        "``-created_at,name``."
                    ),
                ),
            ],
            None,
        ),
    }
    for name, field in _iter_fields_including_nested(schema_cls):
        if name in _RESERVED_NAMES:
            raise ValueError(
                f"List-params schema for {schema_cls.__name__!r} cannot expose "
                f"field {name!r}: it collides with a reserved pagination/sort "
                "parameter. Add a Pydantic alias to expose it as a filter."
            )

        # Type filter parameters as ``Optional[list[str]]`` (rather than the
        # column's true type) so FastAPI/Starlette preserve repeated query
        # parameters as a list and downstream ``_parse_value`` can perform
        # the actual type coercion. ``__isnull`` stays a scalar bool because
        # repeating it makes no sense.
        eq_desc = (
            f"Filter by ``{name}``. Comma-separated values are OR-combined "
            "(SQL ``IN``). Repeat the parameter to AND multiple predicates."
        )
        ne_desc = (
            f"Exclude rows where ``{name}`` matches. Comma-separated values "
            "are AND-combined (SQL ``NOT IN``)."
        )
        fields[name] = (
            Annotated[Optional[list[str]], Field(description=eq_desc)],
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
                    ),
                ),
            ],
            None,
        )

        if _supports_range_operators(field):
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
                            f"Case-insensitive substring search on "
                            f"``{name}``. Repeat the parameter to AND "
                            "multiple terms; whitespace inside one value is "
                            "also AND-split as a convenience."
                        ),
                    ),
                ],
                None,
            )

    schema_name = "ListParams" + schema_cls.__name__
    return pydantic.create_model(schema_name, **fields)  # type: ignore[call-overload]


def apply_list_params(
    params: pydantic.BaseModel | QueryParams,
    select_query: Select[Any],
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
) -> Select[Any]:
    """
    Apply pagination, sorting, and filtering on a SQL query using validated
    list-endpoint query parameters.

    ``params`` is normally an instance of the schema returned by
    :func:`create_list_params_schema`. The generated FastAPI endpoints
    always pass a validated instance, so pagination/filter bounds have
    already been checked.

    A raw :class:`~starlette.datastructures.QueryParams` is also accepted
    for callers that build the query parameters programmatically.
    **Raw inputs bypass schema validation** — the caller is responsible
    for verifying ``page``/``page_size`` ranges and any per-view bounds
    (``max_page_size``); this function only performs the minimum coercion
    needed to apply the SQL clauses.

    Examples::

        # Pagination
        page=2&page_size=50

        # Sorting
        order_by=name,-created_at

        # Filtering
        name=Bob&status=active&created_at__gte=2024-01-01

        # Contains (string fields)
        name__contains=john&email__contains=example
    """
    query_params = _coerce_to_query_params(params)
    select_query = _apply_filtering(query_params, select_query, model, schema_cls)
    select_query = _apply_sorting(query_params, select_query, model, schema_cls)
    select_query = _apply_pagination(query_params, select_query)
    return select_query


def _coerce_to_query_params(
    params: pydantic.BaseModel | QueryParams,
) -> QueryParams:
    """Normalise a validated Pydantic model or raw QueryParams to QueryParams.

    When a dumped field is a list (e.g. a repeated ``name__contains``), each
    element is expanded to its own ``(key, value)`` tuple so that
    ``QueryParams.multi_items()`` later returns the original repeated values.
    """
    if isinstance(params, QueryParams):
        return params
    if isinstance(params, pydantic.BaseModel):
        dumped = params.model_dump(exclude_none=True, by_alias=True, mode="json")
        items: list[tuple[str, str]] = []
        for key, value in dumped.items():
            if isinstance(value, list):
                items.extend((key, str(item)) for item in value)
            else:
                items.append((key, str(value)))
        return QueryParams(items)
    return QueryParams(params)


def _apply_pagination(
    query_params: QueryParams, select_query: Select[Any]
) -> Select[Any]:
    page_size = _get_int(query_params, "page_size")
    if page_size is None:
        return select_query
    page = _get_int(query_params, "page") or 1
    offset = (page - 1) * page_size
    return select_query.limit(page_size).offset(offset)


def _get_int(query_params: QueryParams, param_name: str) -> Optional[int]:
    value = query_params.get(param_name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise HTTPException(
            400,
            f"Invalid value for URL query parameter {param_name}: "
            f"{value} is not an integer",
        )


def _apply_sorting(
    query_params: QueryParams,
    select_query: Select[Any],
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
) -> Select[Any]:
    sort_string = query_params.get("order_by")
    if not sort_string:
        id_column = getattr(model, "id", None)
        if id_column is not None:
            return select_query.order_by(id_column)
        return select_query

    for column_name in sort_string.split(","):
        order = sqlalchemy.asc
        if column_name.startswith("-"):
            order = sqlalchemy.desc
            column_name = column_name[1:]
        joins, column = _resolve_column(model, column_name, schema_cls)
        for join in joins:
            select_query = select_query.join(join)
        select_query = select_query.order_by(order(column))
    return select_query


def _iter_fields_including_nested(
    schema_cls: SchemaType, prefix: str = ""
) -> Iterator[tuple[str, FieldInfo]]:
    for name, field in schema_cls.model_fields.items():
        public_name = field.alias or name
        # Each segment of the public dotted path becomes part of the URL
        # grammar. ``__`` is reserved for operator suffixes (``__gte``,
        # ``__contains``, ...) and ``.`` is reserved for relation traversal,
        # so a segment containing either character would create an
        # ambiguous URL key. Reject at schema-generation time so the
        # collision surfaces during view registration, not at request time.
        if "__" in public_name:
            raise ValueError(
                f"List-params schema for {schema_cls.__name__!r} cannot "
                f"expose field {public_name!r}: ``__`` is reserved for "
                "operator suffixes. Choose a different Pydantic alias."
            )
        if "." in public_name:
            raise ValueError(
                f"List-params schema for {schema_cls.__name__!r} cannot "
                f"expose field {public_name!r}: ``.`` is reserved for "
                "relation traversal. Choose a different Pydantic alias."
            )
        full_name = f"{prefix}.{public_name}" if prefix else public_name
        nested = _get_nested_schema(field)
        if nested:
            yield from _iter_fields_including_nested(nested, full_name)
        else:
            yield full_name, field


def _resolve_field_name(schema_cls: SchemaType, public_name: str) -> str | None:
    """Given a schema and a public (URL-facing) name, return the canonical
    Python field name to use with the model.

    The public name is the field's alias when one is declared, otherwise the
    field name itself. Aliased fields are *only* reachable by their alias —
    Python field names are never part of the public URL contract, even when
    the schema has ``populate_by_name=True`` (which only affects how Pydantic
    parses input bodies, not the generated list-params query schema).
    """
    for field_name, field in schema_cls.model_fields.items():
        if field.alias == public_name:
            return field_name

    if public_name in schema_cls.model_fields:
        field = schema_cls.model_fields[public_name]
        if field.alias is None:
            return public_name
    return None


def _resolve_column(
    model: type[DeclarativeBase],
    column_path: str,
    schema_cls: SchemaType,
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
            raise HTTPException(400, f"Invalid attribute in URL query: {column_path}")
        field_name = _resolve_field_name(current_schema, relation)
        if field_name is None:
            raise HTTPException(400, f"Invalid attribute in URL query: {column_path}")
        rel = getattr(current_model, field_name, None)
        if not isinstance(rel, InstrumentedAttribute) or not hasattr(
            rel.property, "mapper"
        ):
            raise HTTPException(400, f"Invalid attribute in URL query: {column_path}")
        joins.append(rel)
        current_model = rel.property.mapper.class_
        current_schema = _get_nested_schema(current_schema.model_fields[field_name])

    if current_schema is None:
        raise HTTPException(400, f"Invalid attribute in URL query: {column_path}")
    field_name = _resolve_field_name(current_schema, name)
    if field_name is None:
        raise HTTPException(400, f"Invalid attribute in URL query: {column_path}")
    column = getattr(current_model, field_name, None)
    if (
        column is None
        or not isinstance(column, InstrumentedAttribute)
        or not isinstance(column.property, ColumnProperty)
    ):
        raise HTTPException(400, f"Invalid attribute in URL query: {column_path}")
    return joins, cast(InstrumentedAttribute[Any], column)


def _apply_filtering(
    query_params: QueryParams,
    select_query: Select[Any],
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
) -> Select[Any]:
    """Apply ``key=value`` and ``key__op=value`` filters to ``select_query``.

    Multiple filters on the same column are AND-combined. Comma-separated
    values within one parameter are OR-combined for ``eq`` (the default) and
    AND-combined for ``ne`` (so ``status__ne=a,b`` means NOT IN (a, b)). For
    ``contains`` values are split on whitespace and AND-combined.
    """
    filters: dict[InstrumentedAttribute[Any], list[ColumnElement[Any]]] = defaultdict(
        list
    )
    joins: set[InstrumentedAttribute[Any]] = set()

    for key, raw_value in query_params.multi_items():
        if key in _RESERVED_NAMES:
            continue

        if "__" in key:
            column_name, op = key.split("__", 1)
        else:
            column_name, op = key, "eq"

        column_joins, column = _resolve_column(model, column_name, schema_cls)
        joins.update(column_joins)
        parser = functools.partial(_parse_value, schema_cls, column_name)

        if op == "isnull":
            try:
                value = pydantic.TypeAdapter(bool).validate_python(raw_value)
            except pydantic.ValidationError as exc:
                raise HTTPException(
                    400, f"Invalid value for URL query parameter {key}"
                ) from exc
            filters[column].append(column.is_(None) if value else column.isnot(None))
            continue

        clause = _build_clause(column, raw_value, op, parser)
        if clause is not None:
            filters[column].append(clause)

    for join in joins:
        select_query = select_query.join(join)

    for column, clauses in filters.items():
        and_clause = (
            clauses[0] if len(clauses) == 1 else sqlalchemy.and_(*clauses)
        )
        select_query = select_query.where(and_clause)
    return select_query


def _build_clause(
    column: InstrumentedAttribute[Any],
    raw_value: str,
    op: str,
    parser: Callable[[str], Any],
) -> ColumnElement[Any] | None:
    """Combine multiple values within one parameter according to ``op`` semantics."""
    if op == "contains":
        values = [v for v in raw_value.split() if v]
        if not values:
            return None
        clauses = [_make_where_clause(column, v, op, parser) for v in values]
        return clauses[0] if len(clauses) == 1 else sqlalchemy.and_(*clauses)

    values = raw_value.split(",")
    if not values:
        return None
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
            raise HTTPException(400, f"Invalid attribute in URL query: {column_name}")
        return _parse_value(nested, column_part, value)

    field_name = _resolve_field_name(schema_cls, column_name)
    if field_name is None:
        raise HTTPException(400, f"Invalid attribute in URL query: {column_name}")

    try:
        obj = schema_cls.__pydantic_validator__.validate_assignment(
            schema_cls.model_construct(), field_name, value
        )
        return getattr(obj, field_name)
    except Exception:
        raise HTTPException(400, f"Invalid attribute in URL query: {column_name}")


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
    if op == "gte":
        return column >= parser(filter_value)
    if op == "lte":
        return column <= parser(filter_value)
    if op == "gt":
        return column > parser(filter_value)
    if op == "lt":
        return column < parser(filter_value)
    if op == "ne":
        return column != parser(filter_value)
    if op == "contains":
        return column.ilike(f"%{_escape_like_value(filter_value)}%", escape="\\")
    if op == "eq":
        return column == parser(filter_value)
    raise HTTPException(400, f"Unsupported filter operator: {op!r}")
