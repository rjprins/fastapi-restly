import functools
from collections import defaultdict
from typing import Any, Callable, Iterator, Optional, Union, cast, get_args, get_origin

import pydantic
import sqlalchemy
from fastapi import HTTPException
from pydantic.fields import FieldInfo
from sqlalchemy import ColumnElement, Select
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.orm.properties import ColumnProperty
from starlette.datastructures import QueryParams

SchemaType = type[pydantic.BaseModel]


def _is_string_field_v2(field: FieldInfo) -> bool:
    """Check if a field is a string type."""
    annotation = field.annotation

    # Handle Optional[str] (i.e., Union[str, None])
    origin = get_origin(annotation)
    if origin is Union:
        args = get_args(annotation)
        non_none_args = [arg for arg in args if arg is not type(None)]
        if len(non_none_args) == 1:
            annotation = non_none_args[0]

    return annotation is str


def create_query_param_schema_v2(schema_cls: SchemaType) -> SchemaType:
    """
    Create a pydantic model class that describes and validates all possible query parameters
    for the v2 interface (direct field names, __gte, __lte, etc.).
    """
    fields = {
        "page": (Optional[int], None),
        "page_size": (Optional[int], None),
        "order_by": (Optional[str], None),
    }
    for name, field in _iter_fields_including_nested_v2(schema_cls):
        field_type = _get_field_type_for_schema(field)
        fields[name] = (Optional[field_type], None)
        # Add range and null filters
        for suffix in ["__gte", "__lte", "__gt", "__lt", "__isnull"]:
            fields[f"{name}{suffix}"] = (Optional[field_type], None)

        # Add contains filter for string fields
        if _is_string_field_v2(field):
            fields[f"{name}__contains"] = (Optional[str], None)

    schema_name = "QueryParamV2" + schema_cls.__name__
    query_param_schema = pydantic.create_model(schema_name, **fields)  # type: ignore
    return query_param_schema


def apply_query_modifiers_v2(
    query_params: QueryParams,
    select_query: Select[Any],
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
) -> Select[Any]:
    """
    Apply pagination, sorting, and filtering through URL query parameters on a SQL query.
    Uses a more standard interface:
      - Pagination: page, page_size
      - Sorting: order_by=name,-created_at
      - Filtering: ?name=Bob&status=active&created_at__gte=2024-01-01
      - Contains (string fields): ?name__contains=john&email__contains=example
    """
    select_query = apply_filtering_v2(query_params, select_query, model, schema_cls)
    select_query = apply_sorting_v2(query_params, select_query, model, schema_cls)
    select_query = apply_pagination_v2(query_params, select_query)
    return select_query


def apply_pagination_v2(
    query_params: QueryParams, select_query: Select[Any]
) -> Select[Any]:
    """
    Apply pagination using page and page_size parameters.
    """
    page = _get_int_v2(query_params, "page") or 1
    page_size = _get_int_v2(query_params, "page_size") or 100
    offset = (page - 1) * page_size
    select_query = select_query.limit(page_size).offset(offset)
    return select_query


def _get_field_type_for_schema(field: FieldInfo) -> type:
    annotation = field.annotation
    origin = get_origin(annotation)
    if origin is Union:
        args = get_args(annotation)
        non_none_args = [arg for arg in args if arg is not type(None)]
        if len(non_none_args) == 1:
            annotation = non_none_args[0]
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
    value = query_params.get(param_name)
    if not value:
        return None

    # Handle string format issue
    if value.startswith("['") and value.endswith("']"):
        value = value[2:-2]  # Remove [' and ']

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

    # Handle string format issue
    if sort_string.startswith("['") and sort_string.endswith("']"):
        sort_string = sort_string[2:-2]  # Remove [' and ']

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
) -> tuple[list[type[DeclarativeBase]], InstrumentedAttribute[Any]]:
    *models, column = _resolve_sqlalchemy_column_v2(model, column_path, schema_cls)
    return cast(list[type[DeclarativeBase]], models), cast(
        InstrumentedAttribute[Any], column
    )


def _resolve_sqlalchemy_column_v2(
    model: type[DeclarativeBase], column_name: str, schema_cls: SchemaType | None = None
) -> Iterator[type[DeclarativeBase] | InstrumentedAttribute[Any]]:
    # Handle string format issue
    if column_name.startswith("['") and column_name.endswith("']"):
        column_name = column_name[2:-2]  # Remove [' and ']

    if "." in column_name:
        relation, _, column_part = column_name.partition(".")
        rel = getattr(model, relation, None)
        if not isinstance(rel, InstrumentedAttribute) or not hasattr(
            rel.property, "mapper"
        ):
            raise HTTPException(400, f"Invalid attribute in URL query: {column_name}")
        related_model = rel.property.mapper.class_
        yield related_model
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

            # If not found by alias, check if populate_by_name is enabled
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

    # Handle different parameter types
    standard_filters = _apply_standard_parameters_v2(
        query_params, select_query, model, schema_cls
    )
    suffix_filters = _apply_suffix_parameters_v2(
        query_params, select_query, model, schema_cls
    )

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
) -> dict[InstrumentedAttribute[Any], list[ColumnElement[Any]]]:
    """Handle standard field parameters (no suffix)."""
    filters: dict[InstrumentedAttribute[Any], list[ColumnElement[Any]]] = defaultdict(
        list
    )

    for key, raw_value in query_params.multi_items():
        if key in ("page", "page_size", "order_by") or "__" in key:
            continue

        # Standard field parameter (eq operator)
        column_name = key
        joins, column = _get_sqlalchemy_column_v2(model, column_name, schema_cls)
        for join in joins:
            select_query = select_query.join(join)

        parser = functools.partial(_parse_value_v2, schema_cls, column_name)
        split_values = raw_value.split(",")
        clauses = [_make_where_clause_v2(column, v, "eq", parser) for v in split_values]
        if len(clauses) == 1:
            or_clause = clauses[0]
        else:
            or_clause = sqlalchemy.or_(*clauses)
        filters[column].append(or_clause)

    return filters


def _apply_suffix_parameters_v2(
    query_params: QueryParams,
    select_query: Select[Any],
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
) -> dict[InstrumentedAttribute[Any], list[ColumnElement[Any]]]:
    """Handle parameters with __suffixes (gte, lte, gt, lt, isnull, contains, etc.)."""
    filters: dict[InstrumentedAttribute[Any], list[ColumnElement[Any]]] = defaultdict(
        list
    )

    for key, raw_value in query_params.multi_items():
        if key in ("page", "page_size", "order_by") or "__" not in key:
            continue

        # Parse suffixes
        column_name, op = key.split("__", 1)
        joins, column = _get_sqlalchemy_column_v2(model, column_name, schema_cls)
        for join in joins:
            select_query = select_query.join(join)

        parser = functools.partial(_parse_value_v2, schema_cls, column_name)

        if op == "isnull":
            value = raw_value.lower() in ("1", "true", "yes")
            clause = column.is_(None) if value else column.isnot(None)
            filters[column].append(clause)
            continue

        # For contains, split by whitespace; for other operators, split by comma
        if op == "contains":
            split_values = raw_value.split()
        else:
            split_values = raw_value.split(",")
        clauses = [_make_where_clause_v2(column, v, op, parser) for v in split_values]
        if len(clauses) == 1:
            or_clause = clauses[0]
        else:
            or_clause = sqlalchemy.or_(*clauses)
        filters[column].append(or_clause)

    return filters


def _parse_value_v2(schema_cls: SchemaType, column_name: str, value: str) -> Any:
    if "." in column_name:
        relation, _, column_part = column_name.partition(".")
        field = schema_cls.model_fields.get(relation)
        schema = _get_nested_schema_v2(field)
        if not schema:
            raise HTTPException(400, f"Invalid attribute in URL query: {column_name}")
        return _parse_value_v2(schema, column_part, value)

    # Handle value format - it might be a string representation of a list
    if value.startswith("['") and value.endswith("']"):
        # Extract the actual value from the string representation
        value = value[2:-2]  # Remove [' and ']

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
    annotation = field.annotation
    origin = get_origin(annotation)
    if origin is Union:
        args = get_args(annotation)
        non_none_args = [arg for arg in args if arg is not type(None)]
        if len(non_none_args) == 1:
            annotation = non_none_args[0]
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
        return column.ilike(f"%{filter_value}%")
    else:  # eq
        value = parser(filter_value)
        return column == value
