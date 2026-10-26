"""A time without an offset is UTC, in filters and in request bodies alike.

Before the database sees a time from a filter or a request body, Restly
gives it the form of its column, so a filter and the stored value never
disagree:

- A time without an offset, for a column with a time zone, gets the offset
  of UTC.
- A time with an offset, for a column without a time zone, becomes the same
  time in UTC, without the offset.

A request body gets the form of its columns when Restly copies it onto the
model. A validator on the field that makes the time local first opts the
field out. The PostgreSQL legs check the drivers, which otherwise reject such
a time or read it in a time zone of the server.
"""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, NamedTuple
from zoneinfo import ZoneInfo

import pydantic
import pytest
from fastapi import FastAPI
from sqlalchemy import DateTime, TypeDecorator, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly._times import column_time
from fastapi_restly.testing import RestlyTestClient

from ._models import SharedBase
from .conftest import create_tables

PLUS_ONE = timezone(timedelta(hours=1))
MINUS_ONE = timezone(timedelta(hours=-1))
AMSTERDAM = ZoneInfo("Europe/Amsterdam")


class _Api(NamedTuple):
    client: RestlyTestClient
    #: fr.AsyncRestView or fr.RestView
    view: type[Any]
    #: fr.AsyncReactAdminView or fr.ReactAdminView
    admin_view: type[Any]
    create_tables: Callable[[], None]


@pytest.fixture(params=["async", "sync"])
def api(request: pytest.FixtureRequest) -> _Api:
    """A client, the view classes and the table setup, async and sync."""
    if request.param == "async":
        return _Api(
            request.getfixturevalue("client"),
            fr.AsyncRestView,
            fr.AsyncReactAdminView,
            create_tables,
        )
    engine, _ = request.getfixturevalue("sync_db")
    return _Api(
        RestlyTestClient(FastAPI()),
        fr.RestView,
        fr.ReactAdminView,
        lambda: SharedBase.metadata.create_all(engine),
    )


def _meeting_classes() -> tuple[type[Any], type[Any]]:
    class Meeting(fr.IDMixin, SharedBase):
        name: Mapped[str]
        # a column without a time zone
        starts_at: Mapped[datetime] = mapped_column(DateTime())
        # a column with a time zone, from fr.ModelDefaults
        ends_at: Mapped[datetime]

    class MeetingSchema(fr.IDSchema):
        name: str
        starts_at: datetime
        ends_at: datetime

    return Meeting, MeetingSchema


class _Seen(NamedTuple):
    #: what the business method got
    data: Any
    #: the times on the object that it returns, before the database sees them
    starts_at: datetime
    ends_at: datetime


def _recording_view(view: type[Any], seen: list[_Seen]) -> type[Any]:
    """A base view whose business methods add what they see to ``seen``."""
    if issubclass(view, fr.AsyncRestView):

        class AsyncRecording(fr.AsyncRestView):
            async def create(self, data: Any) -> Any:
                obj = await super().create(data)
                seen.append(_Seen(data, obj.starts_at, obj.ends_at))
                return obj

            async def update(self, obj: Any, data: Any) -> Any:
                obj = await super().update(obj, data)
                seen.append(_Seen(data, obj.starts_at, obj.ends_at))
                return obj

        return AsyncRecording

    class Recording(fr.RestView):
        def create(self, data: Any) -> Any:
            obj = super().create(data)
            seen.append(_Seen(data, obj.starts_at, obj.ends_at))
            return obj

        def update(self, obj: Any, data: Any) -> Any:
            obj = super().update(obj, data)
            seen.append(_Seen(data, obj.starts_at, obj.ends_at))
            return obj

    return Recording


def test_create_reads_a_time_without_an_offset_as_utc(api):
    """The object gets the time for the column with a time zone as UTC. The
    time for the column without one stays as it is."""
    Meeting, MeetingSchema = _meeting_classes()
    seen: list[_Seen] = []

    @fr.include_view(api.client.app)
    class MeetingView(_recording_view(api.view, seen)):
        prefix = "/meetings"
        model = Meeting
        schema = MeetingSchema

    api.create_tables()
    api.client.post(
        "/meetings",
        json={
            "name": "standup",
            "starts_at": "2024-01-01T09:30:00",
            "ends_at": "2024-01-01T09:45:00",
        },
    )

    (made,) = seen
    assert made.ends_at == datetime(2024, 1, 1, 9, 45, tzinfo=timezone.utc)
    assert made.ends_at.utcoffset() == timedelta(0)
    assert made.starts_at == datetime(2024, 1, 1, 9, 30)
    assert made.starts_at.tzinfo is None


def test_create_stores_a_time_with_an_offset_in_utc(api):
    """The object gets each time in UTC: without the offset for the column
    without a time zone, and with the offset of UTC for the column with one.
    The rows store them so, also on SQLite, which drops an offset. ``create``
    gets the times of the client."""
    Meeting, MeetingSchema = _meeting_classes()
    seen: list[_Seen] = []

    @fr.include_view(api.client.app)
    class MeetingView(_recording_view(api.view, seen)):
        prefix = "/meetings"
        model = Meeting
        schema = MeetingSchema

    api.create_tables()
    created = api.client.post(
        "/meetings",
        json={
            "name": "standup",
            "starts_at": "2024-01-01T10:30:00+01:00",
            "ends_at": "2024-01-01T10:45:00+01:00",
        },
    ).json()

    (made,) = seen
    assert made.starts_at == datetime(2024, 1, 1, 9, 30)
    assert made.starts_at.tzinfo is None
    assert made.ends_at == datetime(2024, 1, 1, 9, 45, tzinfo=timezone.utc)
    assert made.ends_at.utcoffset() == timedelta(0)
    assert made.data.starts_at == datetime(2024, 1, 1, 10, 30, tzinfo=PLUS_ONE)
    assert created["starts_at"] == "2024-01-01T09:30:00"
    assert created["ends_at"] == "2024-01-01T09:45:00"


def test_update_stores_each_time_in_the_form_of_its_column(api):
    Meeting, MeetingSchema = _meeting_classes()
    seen: list[_Seen] = []

    @fr.include_view(api.client.app)
    class MeetingView(_recording_view(api.view, seen)):
        prefix = "/meetings"
        model = Meeting
        schema = MeetingSchema

    api.create_tables()
    created = api.client.post(
        "/meetings",
        json={
            "name": "standup",
            "starts_at": "2024-01-01T09:30:00",
            "ends_at": "2024-01-01T09:45:00Z",
        },
    ).json()
    seen.clear()
    updated = api.client.patch(
        f"/meetings/{created['id']}",
        json={
            "starts_at": "2024-01-02T08:00:00-05:00",
            "ends_at": "2024-01-02T14:00:00",
        },
    ).json()

    (made,) = seen
    assert made.starts_at == datetime(2024, 1, 2, 13, 0)
    assert made.ends_at == datetime(2024, 1, 2, 14, 0, tzinfo=timezone.utc)
    assert made.ends_at.utcoffset() == timedelta(0)
    assert updated["starts_at"] == "2024-01-02T13:00:00"


def test_a_filter_compares_a_time_with_an_offset_in_utc(api):
    """A filter value with an offset is compared in UTC, for both kinds of
    column. A value without an offset is UTC."""
    Meeting, MeetingSchema = _meeting_classes()

    @fr.include_view(api.client.app)
    class MeetingView(api.view):
        prefix = "/meetings"
        model = Meeting
        schema = MeetingSchema

    api.create_tables()
    for name, starts_at in [
        ("early", "2024-01-01T09:00:00"),
        ("late", "2024-01-01T10:00:00"),
    ]:
        api.client.post(
            "/meetings",
            json={"name": name, "starts_at": starts_at, "ends_at": starts_at + "Z"},
        )

    def names(**params: str) -> list[str]:
        response = api.client.get("/meetings", params=params)
        return [row["name"] for row in response.json()["data"]]

    # 10:30+01:00 is 09:30 in UTC
    assert names(starts_at__gte="2024-01-01T10:30:00+01:00") == ["late"]
    assert names(starts_at__lt="2024-01-01T10:30:00+01:00") == ["early"]
    assert names(starts_at="2024-01-01T11:00:00+01:00") == ["late"]
    assert names(starts_at__ne="2024-01-01T11:00:00+01:00") == ["early"]
    assert names(
        starts_at__in="2024-01-01T10:00:00+01:00,2024-01-01T11:00:00+01:00"
    ) == ["early", "late"]
    # with an offset, for the column with a time zone: SQLite drops offsets
    assert names(ends_at__gte="2024-01-01T10:30:00+01:00") == ["late"]
    # without an offset: as it is, and UTC for the column with a time zone
    assert names(starts_at__gte="2024-01-01T09:30:00") == ["late"]
    assert names(ends_at__gte="2024-01-01T09:30:00") == ["late"]


class _TZDateTime(TypeDecorator[datetime]):
    """SQLAlchemy's recipe that stores a time with an offset as UTC in a
    column without a time zone. It refuses a time without an offset."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is not None:
            if value.utcoffset() is None:
                raise TypeError("tzinfo is required")
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is not None:
            value = value.replace(tzinfo=timezone.utc)
        return value


def test_a_column_type_of_your_own_gets_the_offset(api):
    """A ``TypeDecorator`` converts the time itself, so it gets the time
    with its offset. A plain column without a time zone gets it in UTC."""

    class Stamp(fr.IDMixin, SharedBase):
        stamped_at: Mapped[datetime] = mapped_column(_TZDateTime())
        starts_at: Mapped[datetime] = mapped_column(DateTime())

    class StampSchema(fr.IDSchema):
        stamped_at: datetime
        starts_at: datetime

    @fr.include_view(api.client.app)
    class StampView(api.view):
        prefix = "/stamps"
        model = Stamp
        schema = StampSchema

    api.create_tables()
    created = api.client.post(
        "/stamps",
        json={
            "stamped_at": "2024-01-01T10:30:00+01:00",
            "starts_at": "2024-01-01T10:30:00+01:00",
        },
    ).json()

    assert created["stamped_at"] == "2024-01-01T09:30:00Z"
    assert created["starts_at"] == "2024-01-01T09:30:00"
    rows = api.client.get(
        "/stamps", params={"stamped_at__gte": "2024-01-01T10:00:00+01:00"}
    ).json()
    assert len(rows["data"]) == 1


@pytest.mark.parametrize(
    "column_type",
    [DateTime(), DateTime(timezone=True), _TZDateTime()],
    ids=["without-time-zone", "with-time-zone", "type-of-your-own"],
)
def test_a_time_out_of_range_in_utc_is_a_client_error(api, column_type):
    """The time in UTC is after the year 9999: the filter gets 400 and the
    body 422, both with the name that the client uses. Each kind of column
    checks the range, also a type of your own."""

    class Deadline(fr.IDMixin, SharedBase):
        due_at: Mapped[datetime] = mapped_column(column_type)

    class DeadlineSchema(fr.IDSchema):
        due_at: datetime = pydantic.Field(alias="dueAt")

    @fr.include_view(api.client.app)
    class DeadlineView(api.view):
        prefix = "/deadlines"
        model = Deadline
        schema = DeadlineSchema

    api.create_tables()
    late = "9999-12-31T23:30:00-01:00"
    reason = "In UTC, the time is not between the years 1 and 9999."

    response = api.client.get(
        "/deadlines", params={"dueAt__gte": late}, assert_status_code=400
    )
    assert response.json()["detail"] == (
        f"Invalid filter value for 'dueAt': '{late}'. {reason}"
    )

    response = api.client.post(
        "/deadlines", json={"dueAt": late}, assert_status_code=422
    )
    assert response.json()["detail"] == [
        {
            "type": "value_error",
            "loc": ["body", "dueAt"],
            "msg": f"Value error, {reason}",
            "input": late,
        }
    ]


def test_a_type_with_a_variant_gets_the_offset():
    """A type with a variant for one database may store a time zone there
    that the type itself does not, so it gets a time with an offset as it
    is, after the range check."""
    variant = DateTime().with_variant(postgresql.TIMESTAMP(timezone=True), "postgresql")
    sent = datetime(2024, 1, 1, 10, 30, tzinfo=PLUS_ONE)

    assert column_time(variant, sent) is sent
    with pytest.raises(ValueError, match="not between the years 1 and 9999"):
        column_time(variant, datetime(9999, 12, 31, 23, 30, tzinfo=MINUS_ONE))


def test_a_secret_time_gets_the_form_of_its_column(sync_db):
    """Restly reveals the time of a ``pydantic.Secret`` field before it gives
    the time the form of its column, on create and on update."""
    engine, make_session = sync_db

    class Appointment(fr.IDMixin, SharedBase):
        held_at: Mapped[datetime] = mapped_column(DateTime())

    class AppointmentSchema(fr.IDSchema):
        held_at: pydantic.Secret[datetime]

    class AppointmentView(fr.RestView):
        prefix = "/appointments"
        model = Appointment
        schema = AppointmentSchema

    app = FastAPI()
    fr.include_view(app, AppointmentView)
    SharedBase.metadata.create_all(engine)
    client = RestlyTestClient(app)

    def stored() -> datetime:
        with make_session() as session:
            return session.execute(select(Appointment.held_at)).scalar_one()

    client.post("/appointments", json={"held_at": "2024-01-01T10:30:00+01:00"})
    assert stored() == datetime(2024, 1, 1, 9, 30)
    client.patch("/appointments/1", json={"held_at": "2024-01-02T08:00:00-05:00"})
    assert stored() == datetime(2024, 1, 2, 13, 0)


def _amsterdam_time(value: datetime) -> datetime:
    """The validator of the docs that keeps local times."""
    if value.tzinfo is None:
        return value
    return value.astimezone(AMSTERDAM).replace(tzinfo=None)


def test_a_validator_on_the_field_keeps_local_times(api):
    """The opt-out of the docs: the validator makes a time local first, so
    Restly gets a time without an offset and keeps it. A filter value goes
    through the validator too. A field without the validator follows the
    rule."""

    class Booking(fr.IDMixin, SharedBase):
        name: Mapped[str]
        # a clock time in Amsterdam
        local_at: Mapped[datetime] = mapped_column(DateTime())
        # UTC
        utc_at: Mapped[datetime] = mapped_column(DateTime())

    class BookingSchema(fr.IDSchema):
        name: str
        local_at: Annotated[datetime, pydantic.AfterValidator(_amsterdam_time)]
        utc_at: datetime

    @fr.include_view(api.client.app)
    class BookingView(api.view):
        prefix = "/bookings"
        model = Booking
        schema = BookingSchema

    api.create_tables()
    created = api.client.post(
        "/bookings",
        json={
            "name": "dinner",
            "local_at": "2024-07-01T17:30:00Z",
            "utc_at": "2024-07-01T19:30:00+02:00",
        },
    ).json()

    # Amsterdam is two hours ahead of UTC in July
    assert created["local_at"] == "2024-07-01T19:30:00"
    assert created["utc_at"] == "2024-07-01T17:30:00"
    rows = api.client.get(
        "/bookings", params={"local_at": "2024-07-01T17:30:00Z"}
    ).json()
    assert [row["name"] for row in rows["data"]] == ["dinner"]
