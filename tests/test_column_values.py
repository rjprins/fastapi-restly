"""A value from a client that its column cannot hold is the client's error.

The SQLAlchemy type of a column is the contract on every database: an
``Integer`` holds 32 bits, a ``String(5)`` five characters, and text holds
no NUL character. PostgreSQL refuses such a value with a data error, which
was a 500, and SQLite stored it, so the tests of an app on SQLite did not
see it. See ``tests/postgres`` for the same requests on PostgreSQL.
"""

import json

import pytest
import sqlalchemy
from sqlalchemy import BigInteger, ForeignKey, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly._columns import column_value

from ._models import SharedBase
from .conftest import create_tables


@pytest.mark.parametrize(
    ("column_type", "low", "high"),
    [
        (SmallInteger(), -(2**15), 2**15 - 1),
        (sqlalchemy.Integer(), -(2**31), 2**31 - 1),
        (BigInteger(), -(2**63), 2**63 - 1),
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


def test_an_unsigned_type_of_mysql_holds_no_negative_numbers():
    from sqlalchemy.dialects import mysql

    assert column_value(mysql.INTEGER(unsigned=True), 2**32 - 1) == 2**32 - 1
    assert column_value(mysql.BIGINT(unsigned=True), 2**64 - 1) == 2**64 - 1
    for column_type, value in (
        (mysql.INTEGER(unsigned=True), 2**32),
        (mysql.SMALLINT(unsigned=True), -1),
    ):
        with pytest.raises(ValueError, match="out of range"):
            column_value(column_type, value)


def test_text_fits_the_length_of_its_column_and_holds_no_nul():
    assert column_value(String(5), "abcde") == "abcde"
    with pytest.raises(ValueError, match="at most 5 characters"):
        column_value(String(5), "abcdef")
    for column_type in (String(5), Text(), String()):
        with pytest.raises(ValueError, match="NUL character"):
            column_value(column_type, "a\x00b")


def test_other_columns_and_values_stay_as_they_are():
    """An Enum column checks its labels itself, and a TypeDecorator converts
    the value itself. A type from with_variant() is another type on another
    database. A number for a text column is the field's matter."""

    class Shout(sqlalchemy.TypeDecorator[str]):
        impl = String(3)
        cache_ok = True

    assert column_value(sqlalchemy.Enum("draft", name="e"), "a\x00b") == "a\x00b"
    assert column_value(Shout(), "longer than three") == "longer than three"
    widened = sqlalchemy.Integer().with_variant(BigInteger(), "postgresql")
    assert column_value(widened, 2**40) == 2**40
    assert column_value(sqlalchemy.Float(), 2**40) == 2**40
    assert column_value(String(5), 2**40) == 2**40
    assert column_value(sqlalchemy.Integer(), True) is True


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

    response = client.get(
        "/things", params={"count__in": f"1,{2**40}"}, assert_status_code=400
    )
    assert detail(response) == (
        f"Invalid filter value for 'count': '{2**40}'. {out_of_range}"
    )
    response = client.get(
        "/things", params={"name__contains": "a\x00b"}, assert_status_code=400
    )
    assert detail(response) == f"Invalid filter value for 'name': 'a\\x00b'. {nul}"
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
