"""The rule for a time that a client sends: a time without an offset is UTC.

A filter value follows it, so that the database compares the time that the
client means.
"""

import datetime as _dt
from typing import Any


def column_time(column_type: Any, value: Any) -> Any:
    """``value`` as a column of ``column_type`` takes it.

    A datetime without an offset gets the offset of UTC when the column
    stores a time zone. Any other value comes back as it is.
    """
    if (
        isinstance(value, _dt.datetime)
        and value.utcoffset() is None
        and getattr(column_type, "timezone", False)
    ):
        return value.replace(tzinfo=_dt.timezone.utc)
    return value
