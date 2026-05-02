import datetime as _dt
import decimal as _decimal
import functools
import uuid as _uuid
import warnings
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

#: Default ``page_size`` applied to V2 list endpoints when the client does
#: not send one. ``None`` disables the implicit cap (lists return every
#: matching row and ``page`` is ignored). Override per-view via
#: :attr:`BaseRestView.default_page_size`.
DEFAULT_PAGE_SIZE: int | None = None

#: Maximum ``page_size`` accepted by V2 list endpoints. Values above this
#: are rejected with a 422 by the FastAPI Pydantic-Query validation layer.
#: Override per-view via :attr:`BaseRestView.max_page_size`.
MAX_PAGE_SIZE = 1000

#: Reserved query-parameter names produced by the V2 schema. Filter columns
#: literally named one of these would shadow pagination/sort and are skipped
#: with a warning at schema-creation time.
_V2_RESERVED_NAMES = frozenset({"page", "page_size", "order_by"})


def _is_string_field_v2(field: FieldInfo) -> bool:
    """Check if a field is a string type."""
    annotation = _unwrap_optional_annotation(field.annotation)
    return annotation is str


# Types that support SQL ``<``/``<=``/``>``/``>=`` comparisons. Booleans
# deliberately *don't* — ordering booleans is rarely meaningful and emitting
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


def _supports_range_operators(field: FieldInfo) -> bool:
    annotation = _unwrap_optional_annotation(field.annotation)
    if annotation is bool:
        return False
    if not isinstance(annotation, type):
        # Generic / Annotated / unresolved — be permissive; the parser layer
        # still rejects bad values with a 400.
        return True
    if issubclass(annotation, bool):
        return False
    if issubclass(annotation, _ORDERABLE_TYPES):
        return True
    if issubclass(annotation, _uuid.UUID):
        return False
    return False


def create_query_param_schema_v2(
    schema_cls: SchemaType,
    *,
    default_page_size: int | None = DEFAULT_PAGE_SIZE,
    max_page_size: int = MAX_PAGE_SIZE,
) -> SchemaType:
    """
    Create a pydantic model class that describes and validates all possible query parameters
    for the v2 interface (direct field names, __gte, __lte, etc.).

    ``page`` and ``page_size`` are validated by Pydantic with bounds
    (`page >= 1`, `1 <= page_size <= max_page_size`); out-of-range values
    produce a standard 422 response from FastAPI.

    Args:
        schema_cls: The response schema whose fields drive the available
            filter parameters (direct field names, ``__gte``/``__lte`` etc.).
        default_page_size: Default value for the ``page_size`` parameter.
            ``None`` (the default) means "no implicit page size" — omitting
            ``page_size`` returns every matching row and ``page`` is ignored.
        max_page_size: Upper bound (inclusive) for the ``page_size``
            parameter. Defaults to :data:`MAX_PAGE_SIZE`.
    """
    fields: dict[str, Any] = {
        "page": (Annotated[int, Field(ge=1)], 1),
        "page_size": (
            Annotated[Optional[int], Field(ge=1, le=max_page_size)],
            default_page_size,
        ),
        "order_by": (Optional[str], None),
    }
    for name, field in _iter_fields_including_nested_v2(schema_cls):
        if name in _V2_RESERVED_NAMES:
            warnings.warn(
                f"V2 query schema for {schema_cls.__name__!r} skipping field "
                f"{name!r} because it collides with a reserved pagination/sort "
                "parameter. Add a Pydantic alias to expose it as a filter.",
                stacklevel=2,
            )
            continue
        # Type filter parameters as ``Optional[list[str]]`` (rather than the
        # column's true type) so FastAPI/Starlette preserve repeated query
        # parameters as a list and downstream ``_parse_value_v2`` can perform
        # the actual type coercion. ``__isnull`` stays a scalar bool because
        # repeating it makes no sense.
        fields[name] = (Optional[list[str]], None)
        fields[f"{name}__ne"] = (Optional[list[str]], None)
        fields[f"{name}__isnull"] = (Optional[bool], None)

        # Range operators only make sense on orderable types. Emitting them
        # for booleans would let ``?active__gte=true`` fall through to
        # SQLAlchemy and raise ``ArgumentError`` (HTTP 500).
        if _supports_range_operators(field):
            for suffix in ["__gte", "__lte", "__gt", "__lt"]:
                fields[f"{name}{suffix}"] = (Optional[list[str]], None)

        # Add contains filter for string fields
        if _is_string_field_v2(field):
            fields[f"{name}__contains"] = (Optional[list[str]], None)

    schema_name = "QueryParamV2" + schema_cls.__name__
    query_param_schema = pydantic.create_model(schema_name, **fields)  # type: ignore
    return query_param_schema


def apply_query_modifiers_v2(
    params: pydantic.BaseModel | QueryParams,
    select_query: Select[Any],
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
) -> Select[Any]:
    """
    Apply pagination, sorting, and filtering on a SQL query using the
    already-validated V2 query parameters.

    ``params`` is normally an instance of the schema returned by
    :func:`create_query_param_schema_v2`. Passing a raw
    :class:`~starlette.datastructures.QueryParams` is still supported for
    callers that build the query parameters programmatically.

    Uses a more standard interface::

        # Pagination
        page=2&page_size=50

        # Sorting
        order_by=name,-created_at

        # Filtering
        name=Bob&status=active&created_at__gte=2024-01-01

        # Contains (string fields)
        name__contains=john&email__contains=example
    """
    query_params = _coerce_to_query_params_v2(params)
    select_query = apply_filtering_v2(query_params, select_query, model, schema_cls)
    select_query = apply_sorting_v2(query_params, select_query, model, schema_cls)
    select_query = apply_pagination_v2(query_params, select_query)
    return select_query


def _coerce_to_query_params_v2(
    params: pydantic.BaseModel | QueryParams,
) -> QueryParams:
    """Normalise either a validated Pydantic model or a raw QueryParams to QueryParams.

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


def apply_pagination_v2(
    query_params: QueryParams, select_query: Select[Any]
) -> Select[Any]:
    """
    Apply pagination using ``page`` and ``page_size`` parameters.

    Range and type checks are enforced by the Pydantic query schema returned
    by :func:`create_query_param_schema_v2`. When ``page_size`` is absent or
    ``None`` no LIMIT/OFFSET is applied — every matching row is returned.
    """
    page_size = _get_int_v2(query_params, "page_size")
    if page_size is None:
        return select_query
    page = _get_int_v2(query_params, "page") or 1
    offset = (page - 1) * page_size
    select_query = select_query.limit(page_size).offset(offset)
    return select_query


def _get_field_type_for_schema(field: FieldInfo) -> Any:
    annotation = _unwrap_optional_annotation(field.annotation)
    if annotation is Any:
        return Any
    if isinstance(annotation, type):
        return annotation
    return object


def _iter_fields_including_nested_v2(
    schema_cls: SchemaType, prefix: str = ""
) -> Iterator[tuple[str, FieldInfo]]:
    for name, field in schema_cls.model_fields.items():
        # Use alias if available, otherwise use field name
        field_name = field.alias or name
        full_name = f"{prefix}.{field_name}" if prefix else field_name
        nested = _get_nested_schema_v2(field)
        if nested:
            yield from _iter_fields_including_nested_v2(nested, full_name)
        else:
            yield full_name, field


def _get_int_v2(query_params: QueryParams, param_name: str) -> Optional[int]:
    """Read an integer query parameter, returning ``None`` if absent.

    Range and type checks for the validated parameters (``page`` /
    ``page_size``) are enforced by the Pydantic schema returned by
    :func:`create_query_param_schema_v2`. This helper is kept for callers
    that build raw :class:`QueryParams` and only does the basic
    string-to-int conversion.
    """
    value = query_params.get(param_name)
    if not value:
        return None

    try:
        return int(value)
    except ValueError:
        raise HTTPException(
            400,
            f"Invalid value for URL query parameter {param_name}: {value} is not an integer",
        )


def apply_sorting_v2(
    query_params: QueryParams,
    select_query: Select[Any],
    model: type[DeclarativeBase],
    schema_cls: SchemaType | None = None,
) -> Select[Any]:
    """
    Apply sorting using the order_by parameter (comma-separated, - for descending).
    """
    sort_string = query_params.get("order_by")
    if not sort_string:
        id_column = getattr(model, "id", None)
        if id_column:
            return select_query.order_by(id_column)
        else:
            return select_query

    for column_name in sort_string.split(","):
        order = sqlalchemy.asc
        if column_name.startswith("-"):
            order = sqlalchemy.desc
            column_name = column_name[1:]
        joins, column = _get_sqlalchemy_column_v2(model, column_name, schema_cls)
        for join in joins:
            select_query = select_query.join(join)
        select_query = select_query.order_by(order(column))
    return select_query


def _get_sqlalchemy_column_v2(
    model: type[DeclarativeBase], column_path: str, schema_cls: SchemaType | None = None
) -> tuple[list[InstrumentedAttribute[Any]], InstrumentedAttribute[Any]]:
    *models, column = _resolve_sqlalchemy_column_v2(model, column_path, schema_cls)
    return cast(list[InstrumentedAttribute[Any]], models), cast(
        InstrumentedAttribute[Any], column
    )


def _resolve_sqlalchemy_column_v2(
    model: type[DeclarativeBase], column_name: str, schema_cls: SchemaType | None = None
) -> Iterator[type[DeclarativeBase] | InstrumentedAttribute[Any]]:
    if "." in column_name:
        relation, _, column_part = column_name.partition(".")
        rel = getattr(model, relation, None)
        if not isinstance(rel, InstrumentedAttribute) or not hasattr(
            rel.property, "mapper"
        ):
            raise HTTPException(400, f"Invalid attribute in URL query: {column_name}")
        related_model = rel.property.mapper.class_
        yield rel
        yield from _resolve_sqlalchemy_column_v2(related_model, column_part, schema_cls)
    else:
        # Try to find the column directly
        column = getattr(model, column_name, None)
        if (
            column is not None
            and isinstance(column, InstrumentedAttribute)
            and isinstance(column.property, ColumnProperty)
        ):
            yield cast(InstrumentedAttribute[Any], column)
            return

        # If not found and we have a schema, try to resolve alias to field name
        if schema_cls:
            field_name = None

            # Look for field with this alias
            for name, field in schema_cls.model_fields.items():
                if field.alias == column_name:
                    field_name = name
                    break

            # If not found by alias and populate_by_name is True, try field name
            if field_name is None:
                config = getattr(schema_cls, "model_config", pydantic.ConfigDict())
                populate_by_name = config.get("populate_by_name", False)
                if populate_by_name and column_name in schema_cls.model_fields:
                    field_name = column_name
                # Also allow fields that don't have aliases
                elif not any(f.alias for f in schema_cls.model_fields.values()):
                    # Schema has no aliases, so column_name might be a field name
                    if column_name in schema_cls.model_fields:
                        field_name = column_name
                else:
                    # Check if this field doesn't have an alias
                    for name, field in schema_cls.model_fields.items():
                        if name == column_name and not field.alias:
                            field_name = name
                            break

            if field_name:
                column = getattr(model, field_name, None)

        if (
            column is None
            or not isinstance(column, InstrumentedAttribute)
            or not isinstance(column.property, ColumnProperty)
        ):
            raise HTTPException(400, f"Invalid attribute in URL query: {column_name}")
        yield cast(InstrumentedAttribute[Any], column)


def apply_filtering_v2(
    query_params: QueryParams,
    select_query: Select[Any],
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
) -> Select[Any]:
    """
    Apply filtering using direct field names and __suffixes for range/null filtering.
    """
    filters: dict[InstrumentedAttribute[Any], list[ColumnElement[Any]]] = defaultdict(
        list
    )
    all_joins: set[InstrumentedAttribute[Any]] = set()

    # Handle different parameter types
    standard_filters, standard_joins = _apply_standard_parameters_v2(
        query_params, select_query, model, schema_cls
    )
    suffix_filters, suffix_joins = _apply_suffix_parameters_v2(
        query_params, select_query, model, schema_cls
    )

    all_joins.update(standard_joins)
    all_joins.update(suffix_joins)

    for join in all_joins:
        select_query = select_query.join(join)

    # Merge all filters
    for column, clauses in standard_filters.items():
        filters[column].extend(clauses)
    for column, clauses in suffix_filters.items():
        filters[column].extend(clauses)

    # Apply all filters
    for column, or_clauses in filters.items():
        if len(or_clauses) == 1:
            and_clause = or_clauses[0]
        else:
            and_clause = sqlalchemy.and_(*or_clauses)
        select_query = select_query.where(and_clause)
    return select_query


def _apply_standard_parameters_v2(
    query_params: QueryParams,
    select_query: Select[Any],
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
) -> tuple[
    dict[InstrumentedAttribute[Any], list[ColumnElement[Any]]],
    set[InstrumentedAttribute[Any]],
]:
    """Handle standard field parameters (no suffix)."""
    filters: dict[InstrumentedAttribute[Any], list[ColumnElement[Any]]] = defaultdict(
        list
    )
    joins: set[InstrumentedAttribute[Any]] = set()

    for key, raw_value in query_params.multi_items():
        if key in ("page", "page_size", "order_by") or "__" in key:
            continue

        # Standard field parameter (eq operator)
        column_name = key
        column_joins, column = _get_sqlalchemy_column_v2(model, column_name, schema_cls)
        joins.update(column_joins)

        parser = functools.partial(_parse_value_v2, schema_cls, column_name)
        split_values = raw_value.split(",")
        clauses = [_make_where_clause_v2(column, v, "eq", parser) for v in split_values]
        if len(clauses) == 1:
            or_clause = clauses[0]
        else:
            or_clause = sqlalchemy.or_(*clauses)
        filters[column].append(or_clause)

    return filters, joins


def _apply_suffix_parameters_v2(
    query_params: QueryParams,
    select_query: Select[Any],
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
) -> tuple[
    dict[InstrumentedAttribute[Any], list[ColumnElement[Any]]],
    set[InstrumentedAttribute[Any]],
]:
    """Handle parameters with __suffixes (gte, lte, gt, lt, isnull, contains, etc.)."""
    filters: dict[InstrumentedAttribute[Any], list[ColumnElement[Any]]] = defaultdict(
        list
    )
    joins: set[InstrumentedAttribute[Any]] = set()

    for key, raw_value in query_params.multi_items():
        if key in ("page", "page_size", "order_by") or "__" not in key:
            continue

        # Parse suffixes
        column_name, op = key.split("__", 1)
        column_joins, column = _get_sqlalchemy_column_v2(model, column_name, schema_cls)
        joins.update(column_joins)

        parser = functools.partial(_parse_value_v2, schema_cls, column_name)

        if op == "isnull":
            try:
                value = pydantic.TypeAdapter(bool).validate_python(raw_value)
            except pydantic.ValidationError as exc:
                raise HTTPException(
                    400, f"Invalid attribute in URL query: {key}"
                ) from exc
            clause = column.is_(None) if value else column.isnot(None)
            filters[column].append(clause)
            continue

        # For contains, split by whitespace; for other operators, split by comma
        if op == "contains":
            split_values = [v for v in raw_value.split() if v.strip()]
        else:
            split_values = raw_value.split(",")
        clauses = [_make_where_clause_v2(column, v, op, parser) for v in split_values]
        if not clauses:
            continue
        if len(clauses) == 1:
            or_clause = clauses[0]
        else:
            or_clause = sqlalchemy.or_(*clauses)
        filters[column].append(or_clause)

    return filters, joins


def _parse_value_v2(schema_cls: SchemaType, column_name: str, value: str) -> Any:
    if "." in column_name:
        relation, _, column_part = column_name.partition(".")
        field = schema_cls.model_fields.get(relation)
        schema = _get_nested_schema_v2(field)
        if not schema:
            raise HTTPException(400, f"Invalid attribute in URL query: {column_name}")
        return _parse_value_v2(schema, column_part, value)

    # Check if populate_by_name is enabled
    config = getattr(schema_cls, "model_config", pydantic.ConfigDict())
    populate_by_name = config.get("populate_by_name", False)

    # Try to find the field by alias first
    field_name = None
    for name, field in schema_cls.model_fields.items():
        if field.alias == column_name:
            field_name = name
            break

    # If not found by alias and populate_by_name is True, try field name
    if field_name is None and populate_by_name:
        if column_name in schema_cls.model_fields:
            field_name = column_name

    # If still not found and populate_by_name is False, try field name for schemas without aliases
    if field_name is None and not populate_by_name:
        # Check if this schema has any aliases
        has_aliases = any(field.alias for field in schema_cls.model_fields.values())
        if not has_aliases and column_name in schema_cls.model_fields:
            field_name = column_name
        # Also allow fields that don't have aliases (like 'age' in our test)
        elif has_aliases:
            # Check if this field doesn't have an alias
            for name, field in schema_cls.model_fields.items():
                if name == column_name and not field.alias:
                    field_name = name
                    break

    # If still not found, raise error
    if field_name is None:
        raise HTTPException(400, f"Invalid attribute in URL query: {column_name}")

    try:
        obj = schema_cls.__pydantic_validator__.validate_assignment(
            schema_cls.model_construct(), field_name, value
        )
        return getattr(obj, field_name)
    except Exception:
        raise HTTPException(400, f"Invalid attribute in URL query: {column_name}")


def _get_nested_schema_v2(field: FieldInfo | None) -> SchemaType | None:
    if field is None:
        return None
    annotation = _unwrap_optional_annotation(field.annotation)
    if isinstance(annotation, type) and issubclass(annotation, pydantic.BaseModel):
        return annotation
    return None


def _make_where_clause_v2(
    column: InstrumentedAttribute[Any], filter_value: str, op: str, parser: Callable
) -> ColumnElement[Any]:
    if op == "gte":
        value = parser(filter_value)
        return column >= value
    elif op == "lte":
        value = parser(filter_value)
        return column <= value
    elif op == "gt":
        value = parser(filter_value)
        return column > value
    elif op == "lt":
        value = parser(filter_value)
        return column < value
    elif op == "ne":
        value = parser(filter_value)
        return column != value
    elif op == "contains":
        # For contains, we don't need to parse the value since it's just a string
        escaped = _escape_like_value(filter_value)
        return column.ilike(f"%{escaped}%", escape="\\")
    else:  # eq
        value = parser(filter_value)
        return column == value
