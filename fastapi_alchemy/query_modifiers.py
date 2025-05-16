import functools
from collections import defaultdict
from typing import Any, Callable, Optional, cast

import pydantic
import sqlalchemy
from fastapi import HTTPException
from sqlalchemy import ColumnElement, Select
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.orm.properties import ColumnProperty
from starlette.datastructures import QueryParams


def create_query_param_schema(
    schema_cls: type[pydantic.BaseModel],
) -> type[pydantic.BaseModel]:
    """
    Create a pydantic model class that describes and validates all possible query parameters.
    """
    fields = {
        "limit": (int | None, None),
        "offset": (int | None, None),
        "sort": (str | None, None),
    }
    for name, info in schema_cls.model_fields.items():
        filter = f"filter[{name}]"
        match = f"match[{name}]"
        fields[filter] = (Optional[info.annotation], None)
        fields[match] = (Optional[info.annotation], None)

    schema_name = "QueryParam" + schema_cls.__name__
    query_param_schema = pydantic.create_model(schema_name, **fields)
    return query_param_schema


def apply_query_modifiers(
    query_params: QueryParams,
    select_query: Select,
    model: type[DeclarativeBase],
    schema_cls: type[pydantic.BaseModel],
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
    """
    select_query = apply_pagination(query_params, select_query)
    select_query = apply_sorting(query_params, select_query, model)
    select_query = apply_filtering(query_params, select_query, model, schema_cls)
    return select_query


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
        return select_query

    for column_name in sort_string.split(","):
        order = sqlalchemy.asc
        if column_name.startswith("-"):
            order = sqlalchemy.desc
            column_name = column_name[1:]
        column = _get_sqlalchemy_column(model, column_name)
        select_query = select_query.order_by(order(column))
    return select_query


def _get_sqlalchemy_column(
    model: type[DeclarativeBase], column_name: str
) -> InstrumentedAttribute:
    attribute = getattr(model, column_name, None)
    is_column = (
        attribute is not None
        and isinstance(attribute, InstrumentedAttribute)
        and isinstance(attribute.property, ColumnProperty)
    )
    if is_column:
        return cast(InstrumentedAttribute, attribute)
    else:
        raise HTTPException(
            400, f"Invalid attribute in URL query parameters: {column_name}"
        )


def apply_filtering(
    query_params: QueryParams,
    select_query: Select,
    model: type[DeclarativeBase],
    schema_cls: type[pydantic.BaseModel],
) -> Select:
    filters: dict[InstrumentedAttribute, list[ColumnElement]] = defaultdict(list)
    for key, raw_value in query_params.multi_items():
        if not (key.startswith("filter[") and key.endswith("]")):
            continue
        column_name = key[7:-1]
        column = _get_sqlalchemy_column(model, column_name)

        # Create a parser/validator for the filter values. Which is user input after all.
        parser = functools.partial(_parse_value, schema_cls, column_name)
        split_values = raw_value.split(",")  # Filtering on empty strings is also OK
        clauses = [_make_where_clause(column, v, parser) for v in split_values]
        if len(clauses) == 1:
            or_clause = clauses[0]
        else:
            or_clause = sqlalchemy.or_(*clauses)
        filters[column].append(or_clause)

    for column, or_clauses in filters.items():
        if len(or_clauses) == 1:
            and_clause = or_clauses[0]
        else:
            and_clause = sqlalchemy.and_(*or_clauses)  # CNF > DNF
        select_query = select_query.where(and_clause)

    return select_query


def _parse_value(
    schema_cls: type[pydantic.BaseModel], column_name: str, value: str
) -> Any:
    """Parse and validate a value on which will be filtered."""
    # Hacky stuff to validate (pydantic slang for _parse_!) a single field.
    # https://github.com/pydantic/pydantic/discussions/7367
    # Been using it for a year now. Using v2 now. I still think pydantic is badly designed.
    obj = schema_cls.__pydantic_validator__.validate_assignment(
        schema_cls.model_construct(), column_name, value
    )
    return getattr(obj, column_name)


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
