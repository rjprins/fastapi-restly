"""Typing fixture: ``fr.UTCDatetime`` is statically a ``datetime``.

A field of this type reads and writes as a plain ``datetime``, also with
``| None`` and inside ``ReadOnly``. ``assert_type`` makes the type check fail
if a field stops resolving to ``datetime``.
"""

from datetime import datetime
from typing import TYPE_CHECKING, cast

from typing_extensions import assert_type

import fastapi_restly as fr


class MeetingSchema(fr.IDSchema):
    starts_at: fr.UTCDatetime
    ends_at: fr.UTCDatetime | None = None
    booked_at: fr.ReadOnly[fr.UTCDatetime]


if TYPE_CHECKING:
    _meeting = cast(MeetingSchema, None)
    assert_type(_meeting.starts_at, datetime)
    assert_type(_meeting.ends_at, datetime | None)
    assert_type(_meeting.booked_at, datetime)
