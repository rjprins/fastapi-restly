"""A value from a client that its column cannot hold is the client's error.

The generic SQLAlchemy type of a column is the contract on every database:
an ``Integer`` holds 32 bits, a ``String(5)`` stores five characters, and
text holds no NUL character. PostgreSQL refuses such a value with a data
error, which was a 500, and SQLite stored it, so the tests of an app on
SQLite did not see it. A text column takes text, so a field of another type
over it stores the text that a response shows. Any other type gets the value
as it is, as in SQLAlchemy. See ``tests/postgres`` for the same requests on
PostgreSQL.
"""

import asyncio
import datetime
import decimal
import enum
import ipaddress
import json
import pathlib
import uuid

import pydantic
import pytest
import sqlalchemy
from fastapi import HTTPException
from sqlalchemy import BigInteger, ForeignKey, SmallInteger, String, Text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly._columns import column_value
from fastapi_restly.objects import async_make_new_object, make_new_object

from ._models import SharedBase
from .conftest import create_tables


@pytest.mark.parametrize(
    ("column_type", "low", "high"),
    [
        (SmallInteger(), -(2**15), 2**15 - 1),
        (sqlalchemy.Integer(), -(2**31), 2**31 - 1),
        (BigInteger(), -(2**63), 2**63 - 1),
        (sqlalchemy.SMALLINT(), -(2**15), 2**15 - 1),
        (sqlalchemy.INTEGER(), -(2**31), 2**31 - 1),
        (sqlalchemy.BIGINT(), -(2**63), 2**63 - 1),
    ],
)
def test_a_number_fits_the_bits_of_its_integer_type(column_type, low, high):
    assert column_value(column_type, low) == low
    assert column_value(column_type, high) == high
    for value in (low - 1, high + 1):
        with pytest.raises(ValueError) as caught:
            column_value(column_type, value)
        assert str(caught.value) == (
            f"The number is out of range: the column holds numbers from {low} "
            f"to {high}."
        )


def test_stored_text_fits_the_length_of_its_column_and_text_holds_no_nul():
    """A filter only compares text, and PostgreSQL compares text of any
    length, so the length counts only for text that the column stores."""
    assert column_value(String(5), "abcde", stored=True) == "abcde"
    assert column_value(String(5), "abcdef") == "abcdef"
    with pytest.raises(ValueError, match="at most 5 characters"):
        column_value(String(5), "abcdef", stored=True)
    with pytest.raises(ValueError, match="at most 5 characters"):
        column_value(sqlalchemy.VARCHAR(5), "abcdef", stored=True)
    for column_type in (String(5), Text(), String(), sqlalchemy.TEXT()):
        with pytest.raises(ValueError, match="NUL character"):
            column_value(column_type, "a\x00b")
    # a CHAR without a length holds one character, as in SQL
    assert column_value(sqlalchemy.CHAR(), "a", stored=True) == "a"
    with pytest.raises(ValueError, match="at most 1 character\\.$"):
        column_value(sqlalchemy.CHAR(), "ab", stored=True)


def test_another_type_gets_the_value_as_it_is():
    """Restly checks only the generic types by their exact class. A type of
    a dialect, a subclass, an Enum, a TypeDecorator and a type from
    with_variant() get the value as in SQLAlchemy."""
    from sqlalchemy.dialects import mysql, oracle, postgresql

    class Shout(sqlalchemy.TypeDecorator[str]):
        impl = String(3)
        cache_ok = True

    class Name(String):
        pass

    for column_type, value in (
        (mysql.INTEGER(unsigned=True), 2**32),
        (mysql.TINYINT(), 2**40),
        (oracle.NUMBER(10), 2**33),
        (postgresql.CITEXT(), "a\x00b"),
        (Name(3), "a\x00b and longer"),
        (sqlalchemy.Enum("draft", name="e"), "a\x00b"),
        (Shout(), "a\x00b and longer"),
        (sqlalchemy.Integer().with_variant(BigInteger(), "postgresql"), 2**40),
        (sqlalchemy.Float(), 2**40),
        (sqlalchemy.Integer(), True),
    ):
        assert column_value(column_type, value, stored=True) is value


class _Color(enum.Enum):
    red = "red"


class _Size(enum.Enum):
    big = 1


class _Level(enum.IntEnum):
    high = 3


#: A field of a type other than text over a text column, and a value for it.
_RICH_FIELDS = {
    "url": (pydantic.HttpUrl, "https://a.example"),
    "any_url": (pydantic.AnyUrl, "https://a.example/x"),
    "ipv4": (ipaddress.IPv4Address, "10.0.0.1"),
    "ipv6": (ipaddress.IPv6Address, "::1"),
    "ip_any": (pydantic.IPvAnyAddress, "10.0.0.1"),
    "network": (ipaddress.IPv4Network, "10.0.0.0/8"),
    "path": (pathlib.Path, "/tmp/a"),
    "token": (uuid.UUID, "00000000-0000-0000-0000-000000000001"),
    "color": (_Color, "red"),
    "level": (_Level, 3),
    "day": (datetime.date, "2026-01-02"),
    "at": (datetime.time, "03:04:05"),
    "moment": (datetime.datetime, "2026-01-02T03:04:05Z"),
    "span": (datetime.timedelta, "PT1H"),
    "amount": (decimal.Decimal, "1.50"),
    "ratio": (float, 1.5),
    "count": (int, 7),
    "flag": (bool, True),
}


@pytest.mark.parametrize("name", _RICH_FIELDS)
def test_a_text_column_takes_the_text_that_a_response_shows(client, name):
    """A value from a response finds its row, and a body and a filter agree."""
    field_type, value = _RICH_FIELDS[name]

    class Rich(fr.IDMixin, SharedBase):
        value: Mapped[str]

    schema = pydantic.create_model(
        "RichSchema", __base__=fr.IDSchema, value=(field_type, ...)
    )

    @fr.include_view(client.app)
    class RichView(fr.AsyncRestView):
        prefix = "/rich"
        model = Rich
        response_schema = schema
        create_schema = pydantic.create_model(
            "RichCreateSchema", value=(field_type, ...)
        )
        update_schema = create_schema

    create_tables()
    row = client.post("/rich", json={"value": value}, assert_status_code=201).json()
    shown = row["value"]
    assert client.get(f"/rich/{row['id']}").json()["value"] == shown
    found = client.get("/rich", params={"value": shown}).json()
    assert [item["id"] for item in found["data"]] == [row["id"]]
    assert client.patch(f"/rich/{row['id']}", json={"value": value}).json() == row


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (
            pydantic.TypeAdapter(pydantic.HttpUrl).validate_python("https://a.example"),
            "https://a.example/",
        ),
        (uuid.UUID(int=1), "00000000-0000-0000-0000-000000000001"),
        (_Color.red, "red"),
        (_Level.high, "3"),
        (datetime.date(2026, 1, 2), "2026-01-02"),
        (decimal.Decimal("1.50"), "1.50"),
        (7, "7"),
        (True, "true"),
        ("text", "text"),
    ],
)
def test_the_text_of_a_value_for_a_text_column(value, text):
    assert column_value(Text(), value) == text
    assert column_value(String(), value, stored=True) == text


@pytest.mark.parametrize("value", [None, _Size.big, {"a": 1}, ["a"], object()])
def test_a_value_without_text_stays_as_it_is(value):
    """The driver refuses it: the field does not fit the column. A plain enum
    with number values stays too, because Pydantic cannot read it back from
    the text of its number. An error from the serializer is not the client's
    fault, so it must not become a 422."""
    assert column_value(Text(), value) is value


def test_the_text_of_a_value_must_fit_the_column(client):
    class Link(fr.IDMixin, SharedBase):
        url: Mapped[str] = mapped_column(String(10))

    class LinkSchema(fr.IDSchema):
        url: pydantic.HttpUrl

    @fr.include_view(client.app)
    class LinkView(fr.AsyncRestView):
        prefix = "/links"
        model = Link
        schema = LinkSchema

    create_tables()
    response = client.post(
        "/links", json={"url": "https://a-long-host.example"}, assert_status_code=422
    )
    error = response.json()["detail"][0]
    assert error["loc"] == ["body", "url"]
    assert "at most 10 characters" in error["msg"]


def test_a_reference_with_a_text_id_is_not_turned_to_text(client):
    class Group(SharedBase):
        __tablename__ = "text_group"
        code: Mapped[str] = mapped_column(primary_key=True)

    class Member(fr.IDMixin, SharedBase):
        group_code: Mapped[str] = mapped_column(ForeignKey("text_group.code"))

    class MemberSchema(fr.IDSchema):
        group_code: fr.MustExist[str, Group]

    class GroupSchema(pydantic.BaseModel):
        code: str

    @fr.include_view(client.app)
    class GroupView(fr.AsyncRestView):
        prefix = "/groups"
        model = Group
        schema = GroupSchema

    @fr.include_view(client.app)
    class MemberView(fr.AsyncRestView):
        prefix = "/members"
        model = Member
        schema = MemberSchema

    create_tables()
    client.post("/groups", json={"code": "a"}, assert_status_code=201)
    row = client.post(
        "/members", json={"group_code": "a"}, assert_status_code=201
    ).json()
    assert row["group_code"] == "a"
    client.post("/members", json={"group_code": "b"}, assert_status_code=404)


def test_a_value_that_does_not_fit_is_a_client_error(client):
    class Shelf(fr.IDMixin, SharedBase):
        label: Mapped[str]

    class Thing(fr.IDMixin, SharedBase):
        name: Mapped[str] = mapped_column(String(5))
        count: Mapped[int] = mapped_column(default=0)
        shelf_id: Mapped[int | None] = mapped_column(
            ForeignKey("shelf.id"), default=None
        )

    class ThingSchema(fr.IDSchema):
        name: str
        count: int = 0
        shelf_id: fr.MustExist[int, Shelf] | None = None

    @fr.include_view(client.app)
    class ThingView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing
        schema = ThingSchema

    @fr.include_view(client.app)
    class ThingAdminView(fr.AsyncReactAdminView):
        prefix = "/admin/things"
        model = Thing
        schema = ThingSchema

    create_tables()
    client.post("/things", json={"name": "ok", "count": 2**31 - 1})

    def detail(response):
        body = response.json()["detail"]
        return body if isinstance(body, str) else (body[0]["loc"], body[0]["msg"])

    out_of_range = (
        "Value error, The number is out of range: the column holds numbers from "
        "-2147483648 to 2147483647."
    )
    nul = "Value error, The text holds a NUL character (\\x00), which PostgreSQL "
    nul += "cannot store."
    too_long = "Value error, The text is too long: the column holds at most 5 "
    too_long += "characters."

    response = client.post(
        "/things", json={"name": "a", "count": 2**31}, assert_status_code=422
    )
    assert detail(response) == (["body", "count"], out_of_range)
    response = client.post("/things", json={"name": "a\x00b"}, assert_status_code=422)
    assert detail(response) == (["body", "name"], nul)
    response = client.post("/things", json={"name": "toolong"}, assert_status_code=422)
    assert detail(response) == (["body", "name"], too_long)
    # a filter compares text of any length
    assert client.get("/things", params={"name": "toolong"}).json()["data"] == []

    response = client.get(
        "/things", params={"count__in": f"1,{2**40}"}, assert_status_code=422
    )
    # the position tells which value of the list fails
    assert detail(response) == (["query", "count__in", 1], out_of_range)
    response = client.get(
        "/things", params={"name__icontains": "a\x00b"}, assert_status_code=422
    )
    assert detail(response) == (["query", "name__icontains"], nul)
    response = client.get(
        "/admin/things",
        params={"filter": json.dumps({"count": 2**40})},
        assert_status_code=400,
    )
    assert response.json()["detail"].startswith("Invalid filter value for 'count'")

    response = client.get(f"/things/{2**31}", assert_status_code=422)
    assert detail(response) == (["path", "id"], out_of_range)
    client.get(f"/things/{2**31 - 1}", assert_status_code=404)

    # a reference that cannot fit names no row
    response = client.post(
        "/things", json={"name": "a", "shelf_id": 2**40}, assert_status_code=404
    )
    assert detail(response) == f"Id not found for shelf_id: {2**40}"


def test_a_reference_that_cannot_fit_is_not_found(sync_db):
    """The rows of a reference, as ``fr.objects.make_new_object`` and its
    async twin find them outside a view: an id that cannot fit the key column
    names no row. Without the check, SQLite fails on 2**64 too."""
    engine, make_session = sync_db

    class Label(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Bottle(fr.IDMixin, SharedBase):
        pass

    class BottleSchema(fr.BaseSchema):
        maker_id: fr.IDRef[Label] | None = None
        labels: list[fr.IDRef[Label]] = []

    cases = [
        (BottleSchema(labels=[2**64]), f"labels: [{2**64}]"),
        (BottleSchema(maker_id=2**64), f"maker_id: {2**64}"),
    ]
    SharedBase.metadata.create_all(engine)
    with make_session() as session:
        for data, shown in cases:
            with pytest.raises(HTTPException) as caught:
                make_new_object(session, Bottle, data)
            assert caught.value.status_code == 404
            assert caught.value.detail == f"Id not found for {shown}"

    async def run_async() -> None:
        async_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with async_sessionmaker(bind=async_engine)() as session:
            for data, shown in cases:
                with pytest.raises(HTTPException) as caught:
                    await async_make_new_object(session, Bottle, data)
                assert caught.value.detail == f"Id not found for {shown}"
        await async_engine.dispose()

    asyncio.run(run_async())
