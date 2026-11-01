"""fr.PydanticJSON: a JSON column that holds a Pydantic type.

The column validates the document when it loads a row, and validates and
dumps it on a write, with the dump that Restly uses for a plain JSON column.
In all else it works as SQLAlchemy's JSON.
"""

import asyncio
import dataclasses
import math
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from typing import Any, NamedTuple, Optional

import pydantic
import pytest
import sqlalchemy
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from typing_extensions import TypedDict

import fastapi_restly as fr
from fastapi_restly.db._config import _fr_config
from fastapi_restly.exc import RestlyConfigurationError
from fastapi_restly.schemas import derive_schema
from fastapi_restly.testing._client import RestlyTestClient

from ._models import SharedBase
from .conftest import create_tables


class Address(pydantic.BaseModel):
    street: str
    number: int = 0
    since: datetime | None = None


class Home(Address):
    rooms: int = 1


@pytest.fixture
def sync_client(sync_db) -> Iterator[RestlyTestClient]:
    yield RestlyTestClient(FastAPI())


@pytest.fixture
def engine(sync_db) -> sqlalchemy.Engine:
    return sync_db[0]


def _stored(session: Session, table: str, column: str) -> list[Any]:
    """The text in ``column`` of each row, as the database has it."""
    sql = f"select {column} from {table} order by id"
    return list(session.connection().exec_driver_sql(sql).scalars())


def test_a_row_reads_back_as_the_type(engine):
    class Shop(fr.IDMixin, SharedBase):
        address: Mapped[Address] = mapped_column(fr.PydanticJSON(Address))
        branches: Mapped[dict[str, Address]] = mapped_column(
            fr.PydanticJSON(dict[str, Address]), default_factory=dict
        )

    SharedBase.metadata.create_all(engine)
    since = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    with Session(engine) as session:
        session.add(
            Shop(
                address=Address(street="Main", since=since),
                branches={"north": Address(street="Dock", number=2)},
            )
        )
        session.commit()
        assert _stored(session, "shop", "address") == [
            '{"street": "Main", "number": 0, "since": "2026-01-02T03:04:05Z"}'
        ]

    with Session(engine) as session:
        shop = session.scalars(sqlalchemy.select(Shop)).one()
        assert shop.address == Address(street="Main", since=since)
        assert shop.branches == {"north": Address(street="Dock", number=2)}


def test_a_model_annotation_alone_gives_a_document_column(engine):
    """On a base with ``fr.ModelDefaults``, ``Mapped[Address]`` needs no
    column type. Each model gets its own type. An explicit type wins."""

    class Shop(fr.IDMixin, SharedBase):
        address: Mapped[Address]
        plain: Mapped[Address] = mapped_column(sqlalchemy.JSON)
        home: Mapped[Home | None] = None

    columns = Shop.__table__.c
    assert repr(columns.address.type) == "PydanticJSON(Address)"
    assert repr(columns.home.type) == "PydanticJSON(Home)"
    assert type(columns.plain.type) is sqlalchemy.JSON
    assert columns.home.nullable and not columns.address.nullable
    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Shop(
                address=Address(street="Main"),
                plain={"street": "Main"},
                home=Home(street="Dock", rooms=3),
            )
        )
        session.commit()
    with Session(engine) as session:
        shop = session.scalars(sqlalchemy.select(Shop)).one()
        assert shop.address == Address(street="Main")
        assert shop.home == Home(street="Dock", rooms=3)
        assert shop.plain == {"street": "Main"}


def test_your_own_type_map_entry_for_models_wins():
    class Base(fr.ModelDefaults, DeclarativeBase):
        type_annotation_map = {
            pydantic.BaseModel: fr.PydanticJSONB(pydantic.BaseModel, none_as_null=True)
        }

    class Shop(fr.IDMixin, Base):
        address: Mapped[Address]

    column_type = Shop.__table__.c.address.type
    assert repr(column_type) == "PydanticJSONB(Address)"
    assert column_type.none_as_null


def test_a_write_validates_a_dict(engine):
    """A dict is validated, so the column gets the defaults, and a typo
    fails the flush instead of each later load of the row."""

    class Shop(fr.IDMixin, SharedBase):
        address: Mapped[Address] = mapped_column(fr.PydanticJSON(Address))

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Shop(address={"street": "Main"}))
        session.commit()
        assert _stored(session, "shop", "address") == [
            '{"street": "Main", "number": 0, "since": null}'
        ]

        session.add(Shop(address={"stret": "Main"}))
        with pytest.raises(StatementError) as excinfo:
            session.flush()
        assert isinstance(excinfo.value.orig, pydantic.ValidationError)


def test_a_document_with_aliases_reads_back(engine):
    """The column stores the names of the fields, as for a plain JSON column.
    It reads a document by the names or by the aliases."""

    class Camel(pydantic.BaseModel):
        street_name: str = pydantic.Field(alias="streetName")

    class Shop(fr.IDMixin, SharedBase):
        address: Mapped[Camel]

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Shop(address=Camel(streetName="Main")))
        session.commit()
        assert _stored(session, "shop", "address") == ['{"street_name": "Main"}']
        session.add(Shop(address={"streetName": "Dock"}))
        session.commit()
    with Session(engine) as session:
        stmt = sqlalchemy.select(Shop.address).order_by(Shop.id)
        assert session.scalars(stmt).all() == [
            Camel(streetName="Main"),
            Camel(streetName="Dock"),
        ]


def test_a_subclass_keeps_its_fields_in_the_column(engine):
    """The dump goes by the type of the value, as for a plain JSON column.
    The load gives the type of the column."""

    class Shop(fr.IDMixin, SharedBase):
        address: Mapped[Address] = mapped_column(fr.PydanticJSON(Address))

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Shop(address=Home(street="Main", rooms=3)))
        session.commit()
        assert '"rooms": 3' in _stored(session, "shop", "address")[0]
    with Session(engine) as session:
        assert type(session.scalars(sqlalchemy.select(Shop)).one().address) is Address


def test_a_nan_reads_back_as_a_nan(engine):
    class Series(fr.IDMixin, SharedBase):
        points: Mapped[list[float]] = mapped_column(fr.PydanticJSON(list[float]))

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Series(points=[1.0, math.nan, math.inf]))
        session.commit()
    with Session(engine) as session:
        points = session.scalars(sqlalchemy.select(Series.points)).one()
    assert points[0] == 1.0
    assert math.isnan(points[1])
    assert points[2] == math.inf


class _WithSecret(pydantic.BaseModel):
    password: pydantic.SecretStr


class _WithWriteOnly(pydantic.BaseModel):
    token: fr.WriteOnly[str] = ""


class _WithExcluded(pydantic.BaseModel):
    note: str = pydantic.Field("", exclude=True)


class _Node(pydantic.BaseModel):
    name: str
    children: list["_Node"] = []
    secret: Optional[pydantic.SecretStr] = None


@dataclasses.dataclass
class _Point:
    x: int


class _Polygon(pydantic.BaseModel):
    points: dict[str, list[_Point]]


class _SecretDict(TypedDict):
    password: pydantic.SecretStr


class _SecretTuple(NamedTuple):
    password: pydantic.SecretStr


@pytest.mark.parametrize(
    "type_, message",
    [
        (_WithSecret, "_WithSecret.password is a secret"),
        (dict[str, _WithSecret], "_WithSecret.password is a secret"),
        (list[pydantic.SecretStr], "SecretStr in list[SecretStr] is a secret"),
        (pydantic.Secret[int], "Secret[int] is a secret"),
        (_Node, "_Node.secret is a secret"),
        (_SecretDict, "_SecretDict.password is a secret"),
        (_SecretTuple, "_SecretTuple.password is a secret"),
        (list[_WithWriteOnly | None], "_WithWriteOnly.token is left out of the dump"),
        (_WithExcluded, "_WithExcluded.note is left out of the dump"),
        (list[_Point], "_Point in list[_Point] is a dataclass"),
        (_Polygon, "_Point in _Polygon.points is a dataclass"),
        (Iterable[int], "Pydantic validates Iterable[int] to an iterator"),
    ],
)
def test_a_type_that_the_dump_would_change_is_refused(type_, message):
    """The dump masks a secret and leaves out an excluded field. The column
    refuses such a type when it is defined, not at the first write."""
    with pytest.raises(RestlyConfigurationError, match=message.replace("[", r"\[")):
        fr.PydanticJSON(type_)


@pytest.mark.skipif(
    not hasattr(pydantic.fields.FieldInfo, "exclude_if"),
    reason="Pydantic has exclude_if from version 2.12",
)
def test_a_field_with_exclude_if_is_refused():
    class Note(pydantic.BaseModel):
        text: str = pydantic.Field("", exclude_if=lambda value: value == "")

    with pytest.raises(RestlyConfigurationError, match=r"Note.text has exclude_if"):
        fr.PydanticJSON(list[Note])


def test_a_recursive_model_without_secrets_is_accepted():
    class Tree(pydantic.BaseModel):
        name: str
        children: list["Tree"] = []

    assert fr.PydanticJSON(Tree).type_ is Tree


def test_a_secret_in_a_part_of_type_any_fails_the_write(engine):
    """The type cannot tell what a part of type Any holds, so the write
    checks the value, as for a plain JSON column."""

    class Vault(fr.IDMixin, SharedBase):
        items: Mapped[dict[str, Any]] = mapped_column(fr.PydanticJSON(dict[str, Any]))

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Vault(items={"key": pydantic.SecretStr("hunter2")}))
        with pytest.raises(StatementError) as excinfo:
            session.flush()
    assert isinstance(excinfo.value.orig, RestlyConfigurationError)
    assert "a value in the document is a secret" in str(excinfo.value.orig)


@pytest.mark.parametrize("none_as_null", [False, True])
def test_none_is_stored_as_json_stores_it(engine, none_as_null):
    """``None``, ``JSON.NULL`` and ``null()`` are stored as JSON stores them,
    also with ``none_as_null``."""

    def define(name, type_):
        return type(
            name,
            (fr.IDMixin, SharedBase),
            {
                "__annotations__": {"body": Mapped[Any]},
                "body": mapped_column(type_, nullable=True),
            },
        )

    Plain = define("Plain", sqlalchemy.JSON(none_as_null=none_as_null))
    Typed = define("Typed", fr.PydanticJSON(Address, none_as_null=none_as_null))
    SharedBase.metadata.create_all(engine)

    stored = {}
    for model in (Plain, Typed):
        with Session(engine) as session:
            for value in (None, sqlalchemy.JSON.NULL, sqlalchemy.null(), None):
                session.add(model(body=value))
            session.flush()
            # An update to None.
            session.execute(
                sqlalchemy.update(model).where(model.id == 4).values(body=None)
            )
            stmt = sqlalchemy.select(model.body.is_(None)).order_by(model.id)
            stored[model] = list(session.scalars(stmt))
            session.expire_all()
            assert [row.body for row in session.scalars(sqlalchemy.select(model))] == [
                None
            ] * 4
    assert stored[Typed] == stored[Plain]
    assert stored[Plain] == [none_as_null, False, True, none_as_null]


def test_a_value_in_a_query_is_dumped_but_not_validated(engine):
    """A part of a document has the JSON type, as for a plain JSON column. A
    compared document is dumped, so a model works, and it is not validated,
    so a part of a document works too."""

    class Shop(fr.IDMixin, SharedBase):
        address: Mapped[Address] = mapped_column(fr.PydanticJSON(Address))

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Shop(address=Address(street="Main")))
        session.commit()

        def ids(condition):
            return list(session.scalars(sqlalchemy.select(Shop.id).where(condition)))

        street = Shop.address["street"]
        assert session.scalars(sqlalchemy.select(street)).all() == ["Main"]
        assert ids(street.as_string() == "Main") == [1]
        assert ids(Shop.address == Address(street="Main")) == [1]
        assert ids(Shop.address == {"street": "Main", "number": 0, "since": None}) == [
            1
        ]
        # As on a plain JSON column, a part of a document is not the whole one.
        assert ids(Shop.address == {"street": "Main"}) == []


def test_mutable_dict_tracks_a_new_key(engine):
    """``MutableDict.as_mutable`` tracks a change of the dict itself. A
    change inside a model in it is not tracked, as with SQLAlchemy's JSON."""

    class Shop(fr.IDMixin, SharedBase):
        branches: Mapped[dict[str, Address]] = mapped_column(
            MutableDict.as_mutable(fr.PydanticJSON(dict[str, Address]))
        )

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Shop(branches={"north": Address(street="Main")}))
        session.commit()
    with Session(engine) as session:
        shop = session.scalars(sqlalchemy.select(Shop)).one()
        assert isinstance(shop.branches, MutableDict)
        shop.branches["south"] = Address(street="Dock")
        assert shop in session.dirty
        session.commit()
    with Session(engine) as session:
        shop = session.scalars(sqlalchemy.select(Shop)).one()
        assert shop.branches == {
            "north": Address(street="Main"),
            "south": Address(street="Dock"),
        }


def test_each_type_has_its_own_cache_key():
    assert (
        fr.PydanticJSON(Address)._static_cache_key
        != fr.PydanticJSON(Home)._static_cache_key
    )
    assert (
        fr.PydanticJSON(Address)._static_cache_key
        == fr.PydanticJSON(Address)._static_cache_key
    )


def test_pydantic_jsonb_is_a_jsonb_column():
    class Base(DeclarativeBase):
        pass

    class Shop(Base):
        __tablename__ = "shop"
        id: Mapped[int] = mapped_column(primary_key=True)
        address: Mapped[Address] = mapped_column(fr.PydanticJSONB(Address))
        plain: Mapped[Address] = mapped_column(fr.PydanticJSON(Address))

    ddl = str(
        sqlalchemy.schema.CreateTable(Shop.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "address JSONB NOT NULL" in ddl
    assert "plain JSON NOT NULL" in ddl
    jsonb = Shop.__table__.c.address.type
    assert jsonb.should_evaluate_none
    assert type(Shop.address["street"].type) is postgresql.JSONB


def test_python_type_is_the_class_of_the_document():
    assert fr.PydanticJSON(Address).python_type is Address
    assert fr.PydanticJSON(dict[str, Address]).python_type is dict
    with pytest.raises(NotImplementedError):
        fr.PydanticJSON(Address | None).python_type


def test_a_column_without_annotation_gives_the_field_the_document_type():
    class Shop(fr.IDMixin, SharedBase):
        branches = mapped_column(fr.PydanticJSON(dict[str, Address]), nullable=False)

    schema = derive_schema(Shop)
    assert schema.model_fields["branches"].annotation == dict[str, Address]


@pytest.mark.parametrize("asynchronous", [True, False], ids=["async", "sync"])
def test_a_view_writes_and_reads_the_document(request, asynchronous):
    """The derived schema has the type of the document. A create and an
    update give the model the validated object, and the column dumps it."""
    client = request.getfixturevalue("client" if asynchronous else "sync_client")

    class Shop(fr.IDMixin, SharedBase):
        address: Mapped[Address]
        old_address: Mapped[Address | None] = None

    @fr.include_view(client.app)
    class ShopView(fr.AsyncRestView if asynchronous else fr.RestView):
        prefix = "/shops"
        model = Shop

    fields = ShopView.schema.model_fields
    assert fields["address"].annotation is Address
    assert fields["old_address"].annotation == Address | None
    if asynchronous:
        create_tables()
    else:
        SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])

    client.post("/shops", json={"address": {}}, assert_status_code=422)
    created = client.post(
        "/shops", json={"address": {"street": "Main", "since": "2026-01-02T03:04:05"}}
    ).json()
    assert created["address"] == {
        "street": "Main",
        "number": 0,
        "since": "2026-01-02T03:04:05",
    }
    assert created["old_address"] is None
    updated = client.patch(
        "/shops/1", json={"old_address": {"street": "Dock", "number": 2}}
    ).json()
    assert updated["old_address"] == {"street": "Dock", "number": 2, "since": None}
    assert client.get("/shops/1").json() == updated
    assert client.get("/shops").json()["data"] == [updated]

    stmt = sqlalchemy.select(Shop.address, Shop.old_address)
    assert _rows(stmt, asynchronous=asynchronous) == [
        (
            Address(street="Main", since=datetime(2026, 1, 2, 3, 4, 5)),
            Address(street="Dock", number=2),
        )
    ]


def test_a_filter_key_compares_the_whole_document(sync_client):
    class Shop(fr.IDMixin, SharedBase):
        address: Mapped[Address] = mapped_column(fr.PydanticJSON(Address))

    class ShopSchema(fr.IDSchema):
        address: Address

    SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])
    with fr.open_session() as session:
        session.add_all([Shop(address=Address(street=s)) for s in ("Main", "Dock")])
        session.commit()

        key = fr.query.FilterKey(Shop, ShopSchema, "address")
        value = key.parse({"street": "Dock"})
        stmt = sqlalchemy.select(Shop.id).where(key.column == value)
        assert list(session.scalars(stmt)) == [2]


def _rows(stmt, *, asynchronous):
    """The rows of ``stmt``, read in a new session."""
    if not asynchronous:
        with fr.open_session() as session:
            return [tuple(row) for row in session.execute(stmt)]

    async def read():
        async with fr.open_async_session() as session:
            return [tuple(row) for row in await session.execute(stmt)]

    return asyncio.run(read())
