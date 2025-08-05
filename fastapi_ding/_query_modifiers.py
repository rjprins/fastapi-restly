import functools
from collections import defaultdict
from types import UnionType
from typing import Any, Callable, Iterator, Optional, cast, get_args, get_origin

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


def create_query_param_schema(schema_cls: SchemaType) -> SchemaType:
    """
    Create a pydantic model class that describes and validates all possible query parameters.
    """
    fields = {
        "limit": (int | None, None),
        "offset": (int | None, None),
        "sort": (str | None, None),
    }
    for name, field in _iter_fields_including_nested(schema_cls):
        filter = f"filter[{name}]"
        fields[filter] = (Optional[field.annotation], None)

        # Add contains parameter for string fields
        if _is_string_field(field):
            contains = f"contains[{name}]"
            fields[contains] = (Optional[str], None)

        # TODO: Implement matching as OR-filters
        # match = f"match[{name}]"
        # fields[match] = (Optional[field.annotation], None)

    schema_name = "QueryParam" + schema_cls.__name__
    query_param_schema = pydantic.create_model(schema_name, **fields)
    return query_param_schema


def apply_query_modifiers(
    query_params: QueryParams,
    select_query: Select,
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
) -> Select:
    """
    Apply pagination, sorting, and filtering through URL query parameters on a SQL query.

    Roughly follows JSONAPI for the format of query parameters
    See https://jsonapi.org/format/#query-parameters-families

    For pagination use these two parameters:
     * limit=100
     * offset=200

    For sorting use the 'sort' parameter. Multiple fields are supported. The minus
    sign ('-') can be used to reverse the order.
     * sort=field1,-field2

    Filtering is best described with some examples:

    Combine filters:
    > filter[foo_id]=1&filter[name]=Bob
    WHERE foo_id = 1 AND name = 'Bob'

    Filter on multliple OR values:
    > filter[id]=1,2,3
    WHERE id = 1 OR id = 2 OR id = 3

    Filter on mutliple AND values, and use NOT:
    > filter[name]=!Bob&filter[name]=!Alice
    WHERE name != 'Bob AND name != 'Alice'

    Filter on ranges:
    > filter[created_at]=>=2024-01-01&filter[created_at]=<2025-01-01
    WHERE created_at >= '2024-01-01' AND created_at < '2025-01-01'

    Filter on NULL values:
    > filter[foo_id]=!null
    WHERE foo_id IS NOT NULL

    For string fields, use contains for case-insensitive substring matching:
    > contains[name]=john&contains[email]=example
    WHERE name ILIKE '%john%' AND email ILIKE '%example%'

    Multiple contains values for the same field (OR logic):
    > contains[name]=john,jane
    WHERE name ILIKE '%john%' OR name ILIKE '%jane%'
    """
    select_query = apply_pagination(query_params, select_query)
    select_query = apply_sorting(query_params, select_query, model)
    select_query = apply_filtering(query_params, select_query, model, schema_cls)
    return select_query


def _iter_fields_including_nested(
    schema_cls: SchemaType, prefix: str = ""
) -> Iterator[tuple[str, FieldInfo]]:
    for name, field in schema_cls.model_fields.items():
        full_name = f"{prefix}.{name}" if prefix else name
        nested = _get_nested_schema(field)
        if nested:
            yield from _iter_fields_including_nested(nested, full_name)
        else:
            yield full_name, field


def _is_string_field(field: FieldInfo) -> bool:
    """Check if a field is a string type."""
    annotation = field.annotation

    # Handle Optional[str] (i.e., Union[str, None])
    origin = get_origin(annotation)
    if origin is UnionType:
        args = get_args(annotation)
        non_none_args = [arg for arg in args if arg is not type(None)]
        if len(non_none_args) == 1:
            annotation = non_none_args[0]

    return annotation is str


def apply_pagination(query_params: QueryParams, select_query: Select) -> Select:
    limit = _get_int(query_params, "limit")
    if limit:
        select_query = select_query.limit(limit)
    offset = _get_int(query_params, "offset")
    if offset:
        select_query = select_query.offset(offset)
    return select_query


def _get_int(query_params: QueryParams, param_name: str) -> int | None:
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


def apply_sorting(
    query_params: QueryParams, select_query: Select, model: type[DeclarativeBase]
) -> Select:
    sort_string = query_params.get("sort")
    if not sort_string:
        # Try to apply a default ordering
        id_column = getattr(model, "id", None)
        # TODO: Maybe check if this is a UUID and dont sort in that case?
        if id_column:
            return select_query.order_by(id_column)
        else:
            return select_query  # Unordered

    for column_name in sort_string.split(","):
        order = sqlalchemy.asc
        if column_name.startswith("-"):
            order = sqlalchemy.desc
            column_name = column_name[1:]
        joins, column = _get_sqlalchemy_column(model, column_name)
        for join in joins:
            select_query = select_query.join(join)
        select_query = select_query.order_by(order(column))
    return select_query


def _get_sqlalchemy_column(
    model: type[DeclarativeBase], column_path: str
) -> tuple[list[InstrumentedAttribute], InstrumentedAttribute]:
    """Get SQLAlchemy column and joins needed for a column path."""
    joins, column = _resolve_sqlalchemy_column(model, column_path)
    return joins, column


def _resolve_sqlalchemy_column(
    model: type[DeclarativeBase], column_name: str
) -> tuple[list[InstrumentedAttribute], InstrumentedAttribute]:
    """
    Recursively resolve a dot-separated column path to its SQLAlchemy column.

    Returns a tuple of (joins, column) where joins is a list of relationship attributes
    that need to be joined, and column is the final InstrumentedAttribute representing the column.

    Example:
        For column_name="upload.created_by.email", returns:
            - joins: [upload.created_by] (relationship attribute)
            - column: CreatedBy.email (InstrumentedAttribute)
    """
    joins = []
    current_model = model

    while "." in column_name:
        relation, _, column_part = column_name.partition(".")
        rel = getattr(current_model, relation, None)
        if not isinstance(rel, InstrumentedAttribute) or not hasattr(
            rel.property, "mapper"
        ):
            # Fail if it is not a relation
            raise HTTPException(400, f"Invalid attribute in URL query: {column_name}")

        # Add the relationship attribute to joins
        joins.append(rel)

        # Move to the related model
        related_model = rel.property.mapper.class_
        current_model = related_model
        column_name = column_part

    # Get the final column
    column = getattr(current_model, column_name, None)
    if (
        column is None
        or not isinstance(column, InstrumentedAttribute)
        or not isinstance(column.property, ColumnProperty)
    ):
        raise HTTPException(400, f"Invalid attribute in URL query: {column_name}")

    return joins, cast(InstrumentedAttribute, column)


def apply_filtering(
    query_params: QueryParams,
    select_query: Select,
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
) -> Select:
    """
    Apply filtering through URL query parameters on a SQL query.
    """
    filters: dict[InstrumentedAttribute, list[ColumnElement]] = defaultdict(list)
    all_joins: set[InstrumentedAttribute] = set()

    # Handle filter parameters
    filter_filters, filter_joins = _apply_filter_parameters(
        query_params, select_query, model, schema_cls
    )

    # Handle contains parameters
    contains_filters, contains_joins = _apply_contains_parameters(
        query_params, select_query, model, schema_cls
    )

    # Collect all joins
    all_joins.update(filter_joins)
    all_joins.update(contains_joins)

    # Apply all joins
    for join in all_joins:
        select_query = select_query.join(join)

    # Merge all filters
    for column, clauses in filter_filters.items():
        filters[column].extend(clauses)
    for column, clauses in contains_filters.items():
        filters[column].extend(clauses)

    # Apply all filters
    for column, or_clauses in filters.items():
        if len(or_clauses) == 1:
            and_clause = or_clauses[0]
        else:
            and_clause = sqlalchemy.and_(*or_clauses)  # CNF > DNF
        select_query = select_query.where(and_clause)

    return select_query


def _apply_filter_parameters(
    query_params: QueryParams,
    select_query: Select,
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
) -> tuple[
    dict[InstrumentedAttribute, list[ColumnElement]], set[InstrumentedAttribute]
]:
    """Handle filter[field] parameters."""
    filters: dict[InstrumentedAttribute, list[ColumnElement]] = defaultdict(list)
    joins: set[InstrumentedAttribute] = set()

    for key, raw_value in query_params.multi_items():
        if not (key.startswith("filter[") and key.endswith("]")):
            continue
        column_name = key[7:-1]
        column_joins, column = _get_sqlalchemy_column(model, column_name)
        joins.update(column_joins)

        # Create a parser/validator for the filter values. Which is user input after all.
        parser = functools.partial(_parse_value, schema_cls, column_name)
        split_values = raw_value.split(",")  # Filtering on empty strings is also OK
        clauses = [_make_where_clause(column, v, parser) for v in split_values]
        if len(clauses) == 1:
            or_clause = clauses[0]
        else:
            or_clause = sqlalchemy.or_(*clauses)
        filters[column].append(or_clause)

    return filters, joins


def _apply_contains_parameters(
    query_params: QueryParams,
    select_query: Select,
    model: type[DeclarativeBase],
    schema_cls: SchemaType,
) -> tuple[
    dict[InstrumentedAttribute, list[ColumnElement]], set[InstrumentedAttribute]
]:
    """Handle contains[field] parameters for string fields."""
    filters: dict[InstrumentedAttribute, list[ColumnElement]] = defaultdict(list)
    joins: set[InstrumentedAttribute] = set()

    for key, raw_value in query_params.multi_items():
        if not (key.startswith("contains[") and key.endswith("]")):
            continue
        column_name = key[9:-1]  # Extract field name from contains[field]
        column_joins, column = _get_sqlalchemy_column(model, column_name)
        joins.update(column_joins)

        # For contains, we don't need to parse the value since it's just a string
        # Split by space for multiple contains values (AND logic)
        split_values = raw_value.split()
        clauses = [column.ilike(f"%{v}%") for v in split_values if v.strip()]
        if clauses:
            if len(clauses) == 1:
                clause = clauses[0]
            else:
                clause = sqlalchemy.and_(*clauses)
            filters[column].append(clause)

    return filters, joins


def _parse_value(schema_cls: SchemaType, column_name: str, value: str) -> Any:
    """Parse and validate a value on which will be filtered."""

    # Support nested fields, e.g. "blog.user.name"
    if "." in column_name:
        relation, _, column_part = column_name.partition(".")
        field = schema_cls.model_fields.get(relation)
        schema = _get_nested_schema(field)
        if not schema:
            raise HTTPException(400, f"Invalid attribute in URL query: {column_name}")
        return _parse_value(schema, column_part, value)

    # Hacky stuff to validate (i.e. parse) a single field.
    # https://github.com/pydantic/pydantic/discussions/7367
    obj = schema_cls.__pydantic_validator__.validate_assignment(
        schema_cls.model_construct(), column_name, value
    )
    return getattr(obj, column_name)


def _get_nested_schema(field: FieldInfo | None) -> SchemaType | None:
    if field is None:
        return None

    annotation = field.annotation

    # Handle Optional[NestedModel] (i.e., Union[NestedModel, None])
    origin = get_origin(annotation)
    if origin is UnionType:
        args = get_args(annotation)
        non_none_args = [arg for arg in args if arg is not type(None)]
        if len(non_none_args) == 1:
            annotation = non_none_args[0]

    if isinstance(annotation, type) and issubclass(annotation, pydantic.BaseModel):
        return annotation

    return None


def _make_where_clause(
    column: InstrumentedAttribute, filter_value: str, parser: Callable
) -> ColumnElement:
    if filter_value.startswith(">"):
        value = parser(filter_value[1:])
        return column > value
    elif filter_value.startswith("<"):
        value = parser(filter_value[1:])
        return column < value
    elif filter_value.startswith(">="):
        value = parser(filter_value[2:])
        return column >= value
    elif filter_value.startswith("<="):
        value = parser(filter_value[2:])
        return column <= value
    elif filter_value.startswith("!"):
        value = filter_value[1:]
        if value == "null":
            value = None
        value = parser(value)
        return column != value
    else:
        if filter_value == "null":
            value = None
        value = parser(filter_value)
        return column == value
