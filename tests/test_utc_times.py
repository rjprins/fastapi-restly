"""A time without an offset is UTC, in filters and in request bodies alike.

Before the database sees a time from a filter or a request body, Restly
gives it the form of its column, so a filter and the stored value never
disagree:

- A time without an offset, for a column with a time zone, gets the offset
  of UTC.
- A time with an offset, for a column without a time zone, becomes the same
  time in UTC, without the offset.

A request body gets the form of its columns when Restly copies it onto the
model. A field of type ``fr.UTCDatetime`` holds each time in UTC, so the
business methods and the responses have it in UTC too. A generated schema
and ``fr.TimestampsSchemaMixin`` use that type. A validator on the field that
makes the time local first opts the field out. The PostgreSQL legs check the
drivers, which otherwise reject such a time or read it in a time zone of the
server.
"""

import json
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


def _meeting_classes(time_type: Any = datetime) -> tuple[type[Any], type[Any]]:
    """A model with both kinds of column, and a schema with ``time_type``
    for both times."""

    class Meeting(fr.IDMixin, SharedBase):
        name: Mapped[str]
        # a column without a time zone
        starts_at: Mapped[datetime] = mapped_column(DateTime())
        # a column with a time zone, from fr.ModelDefaults
        ends_at: Mapped[datetime]

    class MeetingSchema(fr.IDSchema):
        name: str
        starts_at: time_type
        ends_at: time_type

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


def test_a_react_admin_filter_compares_a_time_with_an_offset_in_utc(api):
    """The filter of a react-admin view follows the same rule."""
    Meeting, MeetingSchema = _meeting_classes()

    @fr.include_view(api.client.app)
    class MeetingAdminView(api.admin_view):
        prefix = "/admin/meetings"
        model = Meeting
        schema = MeetingSchema

    api.create_tables()
    for name, starts_at in [
        ("early", "2024-01-01T09:00:00"),
        ("late", "2024-01-01T10:00:00"),
    ]:
        api.client.post(
            "/admin/meetings",
            json={"name": name, "starts_at": starts_at, "ends_at": starts_at + "Z"},
        )

    def names(filter_: dict[str, Any]) -> list[str]:
        params = {"filter": json.dumps(filter_)}
        return [
            row["name"]
            for row in api.client.get("/admin/meetings", params=params).json()
        ]

    assert names({"starts_at": "2024-01-01T11:00:00+01:00"}) == ["late"]
    assert names(
        {"starts_at": ["2024-01-01T10:00:00+01:00", "2024-01-01T11:00:00+01:00"]}
    ) == ["early", "late"]
    assert names({"starts_at": "2024-01-01T10:00:00"}) == ["late"]
    # with an offset, for the column with a time zone: SQLite drops offsets
    assert names({"ends_at": "2024-01-01T11:00:00+01:00"}) == ["late"]

    late = "9999-12-31T23:30:00-01:00"
    response = api.client.get(
        "/admin/meetings",
        params={"filter": json.dumps({"starts_at": late})},
        assert_status_code=400,
    )
    assert response.json()["detail"] == (
        f"Invalid filter value for 'starts_at': '{late}'. "
        "Value error, In UTC, the time is not between the years 1 and 9999."
    )


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


@pytest.mark.parametrize("time_type", [datetime, fr.UTCDatetime])
@pytest.mark.parametrize(
    "column_type",
    [DateTime(), DateTime(timezone=True), _TZDateTime()],
    ids=["without-time-zone", "with-time-zone", "type-of-your-own"],
)
def test_a_time_out_of_range_in_utc_is_a_client_error(api, column_type, time_type):
    """The time in UTC is after the year 9999: the filter gets 400 and the
    body 422, both with the name that the client uses. Each kind of column
    checks the range, also a type of your own, and gives the same errors as
    ``fr.UTCDatetime``."""

    class Deadline(fr.IDMixin, SharedBase):
        due_at: Mapped[datetime] = mapped_column(column_type)

    class DeadlineSchema(fr.IDSchema):
        due_at: time_type = pydantic.Field(alias="dueAt")

    @fr.include_view(api.client.app)
    class DeadlineView(api.view):
        prefix = "/deadlines"
        model = Deadline
        schema = DeadlineSchema

    api.create_tables()
    late = "9999-12-31T23:30:00-01:00"
    reason = "Value error, In UTC, the time is not between the years 1 and 9999."

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
            "msg": reason,
            "input": late,
            "ctx": {"error": {}},
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
    through the validator too, also in the filter of a react-admin view. A
    field without the validator follows the rule."""

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

    @fr.include_view(api.client.app)
    class BookingAdminView(api.admin_view):
        prefix = "/admin/bookings"
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
    filter_ = json.dumps({"local_at": "2024-07-01T17:30:00.000Z"})
    rows = api.client.get("/admin/bookings", params={"filter": filter_}).json()
    assert [row["name"] for row in rows] == ["dinner"]


# ---------------------------------------------------------------------------
# fr.UTCDatetime: a field that holds each time in UTC, so the business
# methods and the responses have it in UTC too.
# ---------------------------------------------------------------------------


def test_utc_datetime_holds_each_time_in_utc():
    class Meeting(pydantic.BaseModel):
        starts_at: fr.UTCDatetime
        ends_at: fr.UTCDatetime | None = None

    for sent in [
        "2024-01-01T09:30:00",
        "2024-01-01T10:30:00+01:00",
        "2024-01-01T09:30:00Z",
        1704101400,
    ]:
        meeting = Meeting(starts_at=sent)
        assert meeting.starts_at.isoformat() == "2024-01-01T09:30:00+00:00"

    meeting = Meeting(starts_at="2024-01-01T09:30:00", ends_at="2024-01-01T09:45")
    assert meeting.model_dump_json() == (
        '{"starts_at":"2024-01-01T09:30:00Z","ends_at":"2024-01-01T09:45:00Z"}'
    )
    # OpenAPI shows a plain date-time
    properties = Meeting.model_json_schema()["properties"]
    assert properties["starts_at"]["format"] == "date-time"


def test_in_utc_is_the_rule_as_a_function():
    """``fr.in_utc`` reads a time without an offset as UTC, and changes a time
    with an offset to the same time in UTC, as ``fr.UTCDatetime`` does."""
    utc = datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc)

    assert fr.in_utc(datetime(2024, 1, 1, 9, 30)) == utc
    assert fr.in_utc(datetime(2024, 1, 1, 9, 30)).utcoffset() == timedelta(0)
    new_york = fr.in_utc(
        datetime(2024, 1, 1, 4, 30, tzinfo=ZoneInfo("America/New_York"))
    )
    assert new_york.isoformat() == "2024-01-01T09:30:00+00:00"
    assert fr.in_utc(utc) == utc
    with pytest.raises(ValueError, match="not between the years 1 and 9999"):
        fr.in_utc(datetime(9999, 12, 31, 23, 30, tzinfo=MINUS_ONE))
    assert fr.schemas.in_utc is fr.in_utc


def test_in_utc_compares_a_time_from_the_database_with_one_from_the_schema(api):
    """SQLite gives each time back without an offset, and a time from the
    schema has one. With ``fr.in_utc``, ``update`` can compare them, as the
    docs show."""
    Meeting, MeetingSchema = _meeting_classes(fr.UTCDatetime)
    seen: list[bool] = []

    if issubclass(api.view, fr.AsyncRestView):

        class AsyncChecking(fr.AsyncRestView):
            async def update(self, obj: Any, data: Any) -> Any:
                seen.append(data.ends_at > fr.in_utc(obj.starts_at))
                return await super().update(obj, data)

        base: type[Any] = AsyncChecking
    else:

        class Checking(fr.RestView):
            def update(self, obj: Any, data: Any) -> Any:
                seen.append(data.ends_at > fr.in_utc(obj.starts_at))
                return super().update(obj, data)

        base = Checking

    @fr.include_view(api.client.app)
    class MeetingView(base):
        prefix = "/meetings"
        model = Meeting
        schema = MeetingSchema

    api.create_tables()
    created = api.client.post(
        "/meetings",
        json={
            "name": "standup",
            "starts_at": "2024-01-01T09:30:00Z",
            "ends_at": "2024-01-01T09:45:00Z",
        },
    ).json()
    for ends_at in ("2024-01-01T10:00:00+01:00", "2024-01-01T11:00:00+01:00"):
        api.client.patch(f"/meetings/{created['id']}", json={"ends_at": ends_at})

    # 10:00+01:00 is 09:00 in UTC, before the start; 11:00+01:00 is after it
    assert seen == [False, True]


def test_utc_datetime_gives_the_business_methods_and_the_response_utc(api):
    """``create`` and ``update`` get each time in UTC, and a response shows it
    with ``Z``, also from the column without a time zone. Each column stores
    the time in its own form."""
    Meeting, MeetingSchema = _meeting_classes(fr.UTCDatetime)
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
            "ends_at": "2024-01-01T09:45:00",
        },
    ).json()
    updated = api.client.patch(
        f"/meetings/{created['id']}", json={"ends_at": "2024-01-01T05:00:00-05:00"}
    ).json()

    made, changed = seen
    assert made.data.starts_at.isoformat() == "2024-01-01T09:30:00+00:00"
    assert made.data.ends_at.isoformat() == "2024-01-01T09:45:00+00:00"
    assert changed.data.ends_at.isoformat() == "2024-01-01T10:00:00+00:00"
    assert made.starts_at == datetime(2024, 1, 1, 9, 30)
    assert made.starts_at.tzinfo is None
    assert created["starts_at"] == "2024-01-01T09:30:00Z"
    assert created["ends_at"] == "2024-01-01T09:45:00Z"
    assert updated["ends_at"] == "2024-01-01T10:00:00Z"
    listed = api.client.get(
        "/meetings", params={"starts_at__gte": "2024-01-01T10:15:00+01:00"}
    ).json()
    assert [row["starts_at"] for row in listed["data"]] == ["2024-01-01T09:30:00Z"]


def test_a_filter_method_gets_its_value_as_its_type_gives_it(api):
    """A filter method of your own with an ``fr.UTCDatetime`` value gets the
    time in UTC. For a column without a time zone, it takes the offset off."""
    Meeting, MeetingSchema = _meeting_classes()
    seen: list[datetime] = []

    @fr.include_view(api.client.app)
    class MeetingView(api.view):
        prefix = "/meetings"
        model = Meeting
        schema = MeetingSchema

        @fr.list_filter("starts_after")
        def filter_starts_after(self, value: fr.UTCDatetime) -> Any:
            seen.append(value)
            return Meeting.starts_at > value.replace(tzinfo=None)

    api.create_tables()
    for name, starts_at in [
        ("early", "2024-01-01T09:00:00"),
        ("late", "2024-01-01T10:00:00"),
    ]:
        api.client.post(
            "/meetings",
            json={"name": name, "starts_at": starts_at, "ends_at": starts_at},
        )

    rows = api.client.get(
        "/meetings", params={"starts_after": "2024-01-01T10:30:00+01:00"}
    ).json()

    assert seen[0].isoformat() == "2024-01-01T09:30:00+00:00"
    assert [row["name"] for row in rows["data"]] == ["late"]


def test_utc_datetime_gives_a_column_type_of_your_own_an_offset(api):
    """SQLAlchemy's recipe refuses a time without an offset. A field of type
    ``fr.UTCDatetime`` gives every time the offset of UTC, so the recipe
    takes it, in a body and in a filter, also in the filter of a react-admin
    view. That filter gave the recipe the text before, which was a 500."""

    class Stamp(fr.IDMixin, SharedBase):
        stamped_at: Mapped[datetime] = mapped_column(_TZDateTime())

    class StampSchema(fr.IDSchema):
        stamped_at: fr.UTCDatetime

    @fr.include_view(api.client.app)
    class StampView(api.view):
        prefix = "/stamps"
        model = Stamp
        schema = StampSchema

    @fr.include_view(api.client.app)
    class StampAdminView(api.admin_view):
        prefix = "/admin/stamps"
        model = Stamp
        schema = StampSchema

    api.create_tables()
    created = api.client.post(
        "/stamps", json={"stamped_at": "2024-01-01T09:30:00"}
    ).json()
    rows = api.client.get(
        "/stamps", params={"stamped_at__gte": "2024-01-01T09:00:00"}
    ).json()

    assert created["stamped_at"] == "2024-01-01T09:30:00Z"
    assert len(rows["data"]) == 1
    for sent in ("2024-01-01T09:30:00", "2024-01-01T10:30:00+01:00"):
        filter_ = json.dumps({"stamped_at": sent})
        rows = api.client.get("/admin/stamps", params={"filter": filter_}).json()
        assert len(rows) == 1, sent


# ---------------------------------------------------------------------------
# A generated schema and fr.TimestampsSchemaMixin use fr.UTCDatetime.
# ---------------------------------------------------------------------------


def test_a_generated_schema_holds_each_time_in_utc(api):
    """A view without a schema gives ``create`` each time in UTC, and the
    response shows it with ``Z``, also from the column without a time zone."""
    Meeting, _ = _meeting_classes()
    seen: list[_Seen] = []

    @fr.include_view(api.client.app)
    class MeetingView(_recording_view(api.view, seen)):
        prefix = "/meetings"
        model = Meeting

    api.create_tables()
    created = api.client.post(
        "/meetings",
        json={
            "name": "standup",
            "starts_at": "2024-01-01T10:30:00+01:00",
            "ends_at": "2024-01-01T09:45:00",
        },
    ).json()

    (made,) = seen
    assert made.data.starts_at.isoformat() == "2024-01-01T09:30:00+00:00"
    assert made.data.ends_at.isoformat() == "2024-01-01T09:45:00+00:00"
    assert created["starts_at"] == "2024-01-01T09:30:00Z"
    assert created["ends_at"] == "2024-01-01T09:45:00Z"


def test_the_timestamps_schema_mixin_gives_the_times_in_utc(api):
    """SQLite gives ``created_at`` back without an offset. The response
    shows it in UTC."""

    class Note(fr.TimestampsMixin, fr.IDMixin, SharedBase):
        text: Mapped[str]

    class NoteSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
        text: str

    @fr.include_view(api.client.app)
    class NoteView(api.view):
        prefix = "/notes"
        model = Note
        schema = NoteSchema

    api.create_tables()
    created = api.client.post("/notes", json={"text": "hello"}).json()
    read = api.client.get(f"/notes/{created['id']}").json()

    assert created["created_at"].endswith("Z")
    assert read["created_at"].endswith("Z")
    assert read["updated_at"].endswith("Z")
