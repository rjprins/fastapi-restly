"""A value from a client, checked against its column before the database
sees it.

Restly checks the generic integer and text types of SQLAlchemy, used as they
are, on every database: a number must fit the integer type, text must hold
no NUL character, and text that the column stores must fit its length.
SQLite stores such a value, and PostgreSQL refuses it with a data error,
which is a 500, so the tests of an app on SQLite would not see it. A value
that does not fit is then the client's error: a 422 or a 400 that names the
field.

A text column takes text. A field of another type, such as ``HttpUrl`` over
``Mapped[str]``, gives a value that the database driver cannot take. It goes
to the column as the text that a response shows, so that value also finds
its row in a filter.

Any other type gets the value as it is, as in SQLAlchemy: a type of a
dialect, a subclass, a ``TypeDecorator``, or a type with variants from
``with_variant()``. A constraint on the field of the schema checks such a
value in a request body. A filter leaves out the constraints of the field,
and takes a number only up to ``1e50``, with at most 50 digits after the
point.
"""

import datetime as _dt
import enum
import json
from typing import Any

from pydantic_core import PydanticSerializationError, to_jsonable_python
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

    A type in ``_TEXT_TYPES``, by its exact class and without variants, takes
    text: a value of another type becomes the text that a response shows.
    A time or a duration for any other column goes through
    :func:`column_time`. A type in ``_INTEGER_BITS`` or ``_TEXT_TYPES``, by
    its exact class and without variants, is checked. A number must fit the
    integer type: 16 bits for ``SmallInteger``, 32 for ``Integer`` and 64 for
    ``BigInteger``, as on PostgreSQL, also on SQLite. Text must hold no NUL
    character, and text that the column stores must fit the length of
    ``String(n)``. A ``CHAR`` without a length holds one character, as in
    SQL. Any other type gets the value as it is.

    :param stored: The column stores the value, as for a request body. A
        filter only compares a value with the column, and PostgreSQL compares
        text of any length.
    :raises ValueError: the value does not fit the column. The message says
        why, for the client.
    """
    variants = getattr(column_type, "_variant_mapping", None)
    kind = type(column_type)
    if value is not None and kind in _TEXT_TYPES and not variants:
        value = _as_text(value)
    if isinstance(value, _dt.datetime | _dt.timedelta):
        return column_time(column_type, value)
    if variants:
        # another type on another database
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        bits = _INTEGER_BITS.get(kind)
        if bits is not None:
            _check_integer(bits, value)
    elif isinstance(value, str) and kind in _TEXT_TYPES:
        check_text(value)
        length = column_type.length
        if length is None and kind in (CHAR, NCHAR):
            length = 1
        if stored and length is not None and len(value) > length:
            unit = "character" if length == 1 else "characters"
            raise ValueError(
                f"The text is too long: the column holds at most {length} {unit}."
            )
    return value


def _as_text(value: Any) -> Any:
    """``value`` as the text that a response shows: the text of a URL, a UUID
    or a date, the value of an enum, and ``"7"`` or ``"true"`` for a number or
    a bool. Text comes back as it is, and so does a value that has no such
    text, such as a dict. The database driver then refuses it, as it does
    without Restly, because the schema does not fit the column.
    """
    if isinstance(value, str):
        return value
    try:
        plain = to_jsonable_python(value)
    except PydanticSerializationError:
        return value
    if isinstance(plain, str):
        return plain
    if isinstance(plain, bool | int | float) and not _plain_enum(value):
        return json.dumps(plain)
    return value


def _plain_enum(value: Any) -> bool:
    """Whether ``value`` is a member of an enum that does not also inherit
    from ``int``. Pydantic does not read such a member back from the text of
    its number."""
    return isinstance(value, enum.Enum) and not isinstance(value, int)


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
