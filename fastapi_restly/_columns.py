"""A value from a client, checked against its column before the database
sees it.

The SQLAlchemy type of a column is the contract, on every database. A value
that the column cannot hold is then the client's error: a 422 or a 400 that
names the field. Without the check, PostgreSQL refuses the value with a data
error, which is a 500, and SQLite stores what PostgreSQL would refuse, so
the tests of an app on SQLite would not see it.
"""

import datetime as _dt
from typing import Any

from sqlalchemy import BigInteger, Enum, Integer, SmallInteger, String, TypeDecorator

from ._times import column_time

#: The bits of each integer type, as PostgreSQL stores it. A subclass comes
#: before ``Integer``, which it extends.
_INTEGER_BITS: tuple[tuple[type[Integer], int], ...] = (
    (SmallInteger, 16),
    (BigInteger, 64),
    (Integer, 32),
)


def column_value(column_type: Any, value: Any) -> Any:
    """``value`` as a column of ``column_type`` takes it.

    A time or a duration goes through :func:`column_time`. A number must fit
    the integer type of the column: 16 bits for ``SmallInteger``, 32 for
    ``Integer`` and 64 for ``BigInteger``, as on PostgreSQL, also on SQLite.
    An unsigned type of MySQL holds no negative numbers and twice as many
    positive ones. Text must fit the length of a ``String(n)`` column, and
    must hold no NUL character. A ``TypeDecorator`` converts the value
    itself, and a type from ``with_variant()`` is another type on another
    database, so both get the value as it is.

    :raises ValueError: the value does not fit the column. The message says
        why, for the client.
    """
    if isinstance(value, _dt.datetime | _dt.timedelta):
        return column_time(column_type, value)
    if isinstance(column_type, TypeDecorator) or getattr(
        column_type, "_variant_mapping", None
    ):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        _check_integer(column_type, value)
    elif isinstance(value, str) and isinstance(column_type, String):
        # an Enum column checks its labels itself
        if not isinstance(column_type, Enum):
            check_text(value)
            if column_type.length is not None and len(value) > column_type.length:
                raise ValueError(
                    "The text is too long: the column holds at most "
                    f"{column_type.length} characters."
                )
    return value


def _check_integer(column_type: Any, value: int) -> None:
    """:raises ValueError: ``value`` is out of the range of the column's
    integer type."""
    for integer_type, bits in _INTEGER_BITS:
        if isinstance(column_type, integer_type):
            if getattr(column_type, "unsigned", False):
                low, high = 0, 2**bits - 1
            else:
                low, high = -(2 ** (bits - 1)), 2 ** (bits - 1) - 1
            if not low <= value <= high:
                raise ValueError(
                    "The number is out of range: the column holds numbers from "
                    f"{low} to {high}."
                )
            return


def check_text(value: str) -> None:
    """:raises ValueError: ``value`` holds a NUL character, which PostgreSQL
    cannot store in text. Restly refuses it on every database."""
    if "\x00" in value:
        raise ValueError(
            "The text holds a NUL character (\\x00), which PostgreSQL cannot store."
        )
