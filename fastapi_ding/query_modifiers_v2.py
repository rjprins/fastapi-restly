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
    schema_name = "QueryParamV2" + schema_cls.__name__
    query_param_schema = pydantic.create_model(schema_name, **fields)  # type: ignore
    return query_param_schema


def _iter_fields_including_nested_v2(
    schema_cls: SchemaType, prefix: str = ""
) -> Iterator[tuple[str, FieldInfo]]:
    for name, field in schema_cls.model_fields.items():
        full_name = f"{prefix}.{name}" if prefix else name
        nested = _get_nested_schema_v2(field)
        if nested:
            yield from _iter_fields_including_nested_v2(nested, full_name)
        else:
            yield full_name, field


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
    """
    select_query = apply_filtering_v2(query_params, select_query, model, schema_cls)
    select_query = apply_sorting_v2(query_params, select_query, model)
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


def _get_int_v2(query_params: QueryParams, param_name: str) -> Optional[int]:
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
    query_params: QueryParams, select_query: Select[Any], model: type[DeclarativeBase]
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
        joins, column = _get_sqlalchemy_column_v2(model, column_name)
        for join in joins:
            select_query = select_query.join(join)
        select_query = select_query.order_by(order(column))
    return select_query


def _get_sqlalchemy_column_v2(
    model: type[DeclarativeBase], column_path: str
) -> tuple[list[type[DeclarativeBase]], InstrumentedAttribute[Any]]:
    *models, column = _resolve_sqlalchemy_column_v2(model, column_path)
    return cast(list[type[DeclarativeBase]], models), cast(
        InstrumentedAttribute[Any], column
    )


def _resolve_sqlalchemy_column_v2(
    model: type[DeclarativeBase], column_name: str
) -> Iterator[type[DeclarativeBase] | InstrumentedAttribute[Any]]:
    if "." in column_name:
        relation, _, column_part = column_name.partition(".")
        rel = getattr(model, relation, None)
        if not isinstance(rel, InstrumentedAttribute) or not hasattr(
            rel.property, "mapper"
        ):
            raise HTTPException(400, f"Invalid attribute in URL query: {column_name}")
        related_model = rel.property.mapper.class_
        yield related_model
        yield from _resolve_sqlalchemy_column_v2(related_model, column_part)
    else:
        column = getattr(model, column_name, None)
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
    for key, raw_value in query_params.multi_items():
        if key in ("page", "page_size", "order_by"):
            continue
        # Parse suffixes
        if "__" in key:
            column_name, op = key.split("__", 1)
        else:
            column_name, op = key, "eq"
        joins, column = _get_sqlalchemy_column_v2(model, column_name)
        for join in joins:
            select_query = select_query.join(join)
        parser = functools.partial(_parse_value_v2, schema_cls, column_name)
        if op == "isnull":
            value = raw_value.lower() in ("1", "true", "yes")
            clause = column.is_(None) if value else column.isnot(None)
            filters[column].append(clause)
            continue
        split_values = raw_value.split(",")
        clauses = [_make_where_clause_v2(column, v, op, parser) for v in split_values]
        if len(clauses) == 1:
            or_clause = clauses[0]
        else:
            or_clause = sqlalchemy.or_(*clauses)
        filters[column].append(or_clause)
    for column, or_clauses in filters.items():
        if len(or_clauses) == 1:
            and_clause = or_clauses[0]
        else:
            and_clause = sqlalchemy.and_(*or_clauses)
        select_query = select_query.where(and_clause)
    return select_query


def _parse_value_v2(schema_cls: SchemaType, column_name: str, value: str) -> Any:
    if "." in column_name:
        relation, _, column_part = column_name.partition(".")
        field = schema_cls.model_fields.get(relation)
        schema = _get_nested_schema_v2(field)
        if not schema:
            raise HTTPException(400, f"Invalid attribute in URL query: {column_name}")
        return _parse_value_v2(schema, column_part, value)
    try:
        obj = schema_cls.__pydantic_validator__.validate_assignment(
            schema_cls.model_construct(), column_name, value
        )
        return getattr(obj, column_name)
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
    else:  # eq
        value = parser(filter_value)
        return column == value
