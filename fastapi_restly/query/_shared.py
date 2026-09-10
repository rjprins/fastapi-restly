from typing import Any

import sqlalchemy
from sqlalchemy import Select
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm.attributes import InstrumentedAttribute


def _escape_like_value(value: str) -> str:
    """Escape SQL LIKE wildcard characters for literal substring matching."""
    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace("%", "\\%")
    escaped = escaped.replace("_", "\\_")
    return escaped


def _primary_key_attrs(
    model: type[DeclarativeBase],
) -> list[InstrumentedAttribute[Any]]:
    """The mapped attributes of ``model``'s primary key, in mapper order."""
    mapper = sqlalchemy.inspect(model)
    return [
        getattr(model, mapper.get_property_by_column(column).key)
        for column in mapper.primary_key
    ]


def _append_pk_tiebreak(
    query: Select[Any],
    model: type[DeclarativeBase],
    sorted_on: list[InstrumentedAttribute[Any]],
) -> Select[Any]:
    """Append the primary key as the final ``ORDER BY`` terms.

    Without it, equal-valued rows on a non-unique sort can be skipped or
    repeated across pages. Key columns the caller already sorted on are not
    appended again.
    """
    for pk in _primary_key_attrs(model):
        if not any(pk is column for column in sorted_on):
            query = query.order_by(pk)
    return query
