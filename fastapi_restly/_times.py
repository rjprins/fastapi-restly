"""The rule for a time that a client sends: a time without an offset is UTC.

:func:`in_utc` gives a time its meaning: a field of type ``fr.UTCDatetime``
holds each time in UTC with it. :func:`column_time` gives a time from a
filter or a request body the form of its column before the database sees it,
so that a filter and the stored value never disagree. Without it, the drivers
decide: asyncpg rejects a time with an offset for a column without a time
zone, and reads a time without one in the time zone of the app's Python
process. psycopg leaves both to the time zone of the database session, and
SQLite drops the offset.
"""

import datetime as _dt
from typing import Any

from sqlalchemy import DateTime, TypeDecorator


def in_utc(value: _dt.datetime) -> _dt.datetime:
    """``value`` as the same time in UTC. A time without an offset is UTC.

    This is the rule of
    :data:`fr.UTCDatetime <fastapi_restly.schemas.UTCDatetime>`, as a
    function. Give a time from the database to it before you compare it with
    a time from a schema, as in ``data.ends_at > fr.in_utc(obj.starts_at)``.
    A time from a column without a time zone has no offset, and on SQLite no
    time has one. See :ref:`utc-times`.

    :raises ValueError: the time in UTC is not between the years 1 and 9999.
    """
    if value.utcoffset() is None:
        return value.replace(tzinfo=_dt.timezone.utc)
    try:
        return value.astimezone(_dt.timezone.utc)
    except OverflowError:
        raise ValueError(
            "In UTC, the time is not between the years 1 and 9999."
        ) from None


def value_error_reason(exc: ValueError) -> str:
    """The reason for ``exc`` as Pydantic words it for the validator of
    ``fr.UTCDatetime``, so that a field and a column give one text."""
    return f"Value error, {exc}"


def column_time(column_type: Any, value: Any) -> Any:
    """``value`` as a column of ``column_type`` takes it.

    A datetime without an offset gets the offset of UTC when the column
    stores a time zone. A datetime with an offset becomes the same time in
    UTC, and loses its offset when the column is a ``DateTime`` without a
    time zone. Any other value comes back as it is.

    A ``TypeDecorator``, and a type with a variant for one database, get a
    datetime with an offset as it is, after a check of its range: the
    ``TypeDecorator`` may convert the time itself, as SQLAlchemy's recipe that
    stores UTC in a column without a time zone does, and the variant may store
    a time zone where the type does not.

    :raises ValueError: the time in UTC is not between the years 1 and 9999.
    """
    if not isinstance(value, _dt.datetime):
        return value
    if value.utcoffset() is None:
        if getattr(column_type, "timezone", False):
            return in_utc(value)
        return value
    if isinstance(column_type, TypeDecorator) or (
        isinstance(column_type, DateTime)
        and getattr(column_type, "_variant_mapping", None)
    ):
        in_utc(value)
        return value
    if isinstance(column_type, DateTime):
        utc = in_utc(value)
        return utc if column_type.timezone else utc.replace(tzinfo=None)
    return value
