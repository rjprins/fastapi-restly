"""A value from a client, checked against its column before the database
sees it.

Restly checks the generic integer and text types of SQLAlchemy, used as they
are, on every database: a number must fit the integer type, text must hold
no NUL character, and text that the column stores must fit its length.
SQLite stores such a value, and PostgreSQL refuses it with a data error,
which is a 500, so the tests of an app on SQLite would not see it. A value
that does not fit is then the client's error: a 422 or a 400 that names the
field.

Any other type gets the value as it is, as in SQLAlchemy: a type of a
dialect, a subclass, a ``TypeDecorator``, or a type with variants from
``with_variant()``. A constraint on the field of the schema checks its
values.
"""

import datetime as _dt
from typing import Any

from sqlalchemy import (
    BIGINT,
    CHAR,
    INTEGER,
    NCHAR,
    NVARCHAR,
    SMALLINT,
    TEXT,
    VARCHAR,
    BigInteger,
    Integer,
    SmallInteger,
    String,
    Text,
    Unicode,
    UnicodeText,
)

from ._times import column_time

#: The integer types that Restly checks, with their bits on PostgreSQL.
_INTEGER_BITS: dict[type[Any], int] = {
    SmallInteger: 16,
    SMALLINT: 16,
    Integer: 32,
    INTEGER: 32,
    BigInteger: 64,
    BIGINT: 64,
}

#: The text types that Restly checks.
_TEXT_TYPES: frozenset[type[Any]] = frozenset(
    {String, Text, Unicode, UnicodeText, CHAR, NCHAR, VARCHAR, NVARCHAR, TEXT}
)


def column_value(column_type: Any, value: Any, *, stored: bool = False) -> Any:
    """``value`` as a column of ``column_type`` takes it.

    A time or a duration goes through :func:`column_time`. A type in
    ``_INTEGER_BITS`` or ``_TEXT_TYPES``, by its exact class and without
    variants, is checked. A number must fit the integer type: 16 bits for
    ``SmallInteger``, 32 for ``Integer`` and 64 for ``BigInteger``, as on
    PostgreSQL, also on SQLite. Text must hold no NUL character, and text
    that the column stores must fit the length of ``String(n)``. Any other
    type gets the value as it is.

    :param stored: The column stores the value, as for a request body. A
        filter only compares a value with the column, and PostgreSQL compares
        text of any length.
    :raises ValueError: the value does not fit the column. The message says
        why, for the client.
    """
    if isinstance(value, _dt.datetime | _dt.timedelta):
        return column_time(column_type, value)
    if getattr(column_type, "_variant_mapping", None):
        # another type on another database
        return value
    kind = type(column_type)
    if isinstance(value, int) and not isinstance(value, bool):
        bits = _INTEGER_BITS.get(kind)
        if bits is not None:
            _check_integer(bits, value)
    elif isinstance(value, str) and kind in _TEXT_TYPES:
        check_text(value)
        length = column_type.length
        if stored and length is not None and len(value) > length:
            raise ValueError(
                f"The text is too long: the column holds at most {length} characters."
            )
    return value


def _check_integer(bits: int, value: int) -> None:
    """:raises ValueError: ``value`` does not fit a signed integer of
    ``bits`` bits."""
    low, high = -(2 ** (bits - 1)), 2 ** (bits - 1) - 1
    if not low <= value <= high:
        raise ValueError(
            "The number is out of range: the column holds numbers from "
            f"{low} to {high}."
        )


def check_text(value: str) -> None:
    """:raises ValueError: ``value`` holds a NUL character, which PostgreSQL
    cannot store in text. Restly refuses it on every database."""
    if "\x00" in value:
        raise ValueError(
            "The text holds a NUL character (\\x00), which PostgreSQL cannot store."
        )
