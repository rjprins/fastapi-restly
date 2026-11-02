"""fr.models.PydanticJSON: a JSON column that holds a Pydantic type.

The column validates the document when it loads a row, and validates and
dumps it by its type on a write. In all else it works as SQLAlchemy's JSON.
"""

import asyncio
import dataclasses
import math
import pickle
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Generic, Literal, NamedTuple, Optional, TypeVar
from uuid import UUID, uuid4

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
from .conftest import create_tables, needs_nan_in_responses


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
        address: Mapped[Address] = mapped_column(fr.models.PydanticJSON(Address))
        branches: Mapped[dict[str, Address]] = mapped_column(
            fr.models.PydanticJSON(dict[str, Address]), default_factory=dict
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
            pydantic.BaseModel: fr.models.PydanticJSONB(
                pydantic.BaseModel, none_as_null=True
            )
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
        address: Mapped[Address] = mapped_column(fr.models.PydanticJSON(Address))

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


def test_a_subclass_is_stored_as_the_type_of_the_column(engine):
    """The dump goes by the type of the column, as a field of that type does
    in ``model_dump()``. So a model with ``extra="forbid"`` reads it back."""

    class Strict(pydantic.BaseModel):
        model_config = pydantic.ConfigDict(extra="forbid")
        street: str

    class StrictHome(Strict):
        rooms: int = 1

    class Shop(fr.IDMixin, SharedBase):
        address: Mapped[Strict] = mapped_column(fr.models.PydanticJSON(Strict))

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Shop(address=StrictHome(street="Main", rooms=3)))
        session.commit()
        assert _stored(session, "shop", "address") == ['{"street": "Main"}']
    with Session(engine) as session:
        address = session.scalars(sqlalchemy.select(Shop)).one().address
        assert address == Strict(street="Main")


def test_a_serializer_in_the_type_runs(engine):
    """The dump goes by the type of the column, so a serializer that the type
    declares runs, as in ``TypeAdapter(type_).dump_python()``."""
    Money = Annotated[Decimal, pydantic.PlainSerializer(float, when_used="json")]

    class Prices(fr.IDMixin, SharedBase):
        prices: Mapped[dict[str, Decimal]] = mapped_column(
            fr.models.PydanticJSON(dict[str, Money])
        )

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Prices(prices={"tea": Decimal("1.50")}))
        session.commit()
        assert _stored(session, "prices", "prices") == ['{"tea": 1.5}']


def test_a_query_dumps_a_value_as_the_write_does(engine):
    """A value in a query is dumped by the type of the column too. So the
    serializer runs, and a subclass loses its own fields. Then the query
    finds the row that the write stored."""
    Money = Annotated[Decimal, pydantic.PlainSerializer(float, when_used="json")]

    class Shop(fr.IDMixin, SharedBase):
        prices: Mapped[dict[str, Decimal]] = mapped_column(
            fr.models.PydanticJSON(dict[str, Money])
        )
        address: Mapped[Address]

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Shop(prices={"tea": Decimal("1.50")}, address=Home(street="Main")))
        session.commit()
        prices = sqlalchemy.select(Shop.id).where(
            Shop.prices == {"tea": Decimal("1.50")}
        )
        address = sqlalchemy.select(Shop.id).where(
            Shop.address == Home(street="Main", rooms=3)
        )
        assert session.scalars(prices).all() == [1]
        assert session.scalars(address).all() == [1]


def test_a_strict_model_reads_back(engine):
    """The stored document is JSON data, and the column reads it in lax
    mode. So a strict model takes text for a datetime, a UUID or a Decimal,
    and a list for a tuple."""

    class Entry(pydantic.BaseModel):
        model_config = pydantic.ConfigDict(strict=True)
        at: datetime
        ref: UUID
        amount: Decimal = pydantic.Field(strict=True)
        tags: tuple[str, ...]

    class Ledger(fr.IDMixin, SharedBase):
        entry: Mapped[Entry]

    entry = Entry(
        at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ref=uuid4(),
        amount=Decimal("1.50"),
        tags=("a", "b"),
    )
    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Ledger(entry=entry))
        session.commit()
    with Session(engine) as session:
        assert session.scalars(sqlalchemy.select(Ledger)).one().entry == entry


def test_a_model_with_the_same_fields_is_written_by_its_attributes(engine):
    """A schema of a view can have a class of its own for the document. The
    column reads such a value by its attributes."""

    class AddressSchema(fr.BaseSchema):
        street: str

    class Shop(fr.IDMixin, SharedBase):
        address: Mapped[Address]

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Shop(address=AddressSchema(street="Main")))
        session.commit()
    with Session(engine) as session:
        address = session.scalars(sqlalchemy.select(Shop)).one().address
        assert address == Address(street="Main")


def test_a_model_that_dumps_by_alias_is_stored_by_names(engine):
    """``serialize_by_alias=True`` does not change the keys in the column."""

    class Camel(pydantic.BaseModel):
        model_config = pydantic.ConfigDict(serialize_by_alias=True)
        street_name: str = pydantic.Field(alias="streetName")

    class Shop(fr.IDMixin, SharedBase):
        address: Mapped[Camel]

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Shop(address=Camel(streetName="Main")))
        session.commit()
        assert _stored(session, "shop", "address") == ['{"street_name": "Main"}']


def test_a_nan_is_stored_as_text_and_reads_back_as_a_nan(engine):
    """JSON has no number for NaN or infinity, so the column stores the text
    that PostgreSQL's ``to_jsonb()`` writes. A ``float`` reads it back."""

    class Series(fr.IDMixin, SharedBase):
        points: Mapped[list[float]] = mapped_column(fr.models.PydanticJSON(list[float]))

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Series(points=[1.0, math.nan, math.inf, -math.inf]))
        session.commit()
        assert _stored(session, "series", "points") == [
            '[1.0, "NaN", "Infinity", "-Infinity"]'
        ]
    with Session(engine) as session:
        points = session.scalars(sqlalchemy.select(Series.points)).one()
    assert points[0] == 1.0
    assert math.isnan(points[1])
    assert points[2:] == [math.inf, -math.inf]


def test_a_text_nan_in_an_untyped_part_stays_text(engine):
    """A part of type ``Any`` reads the stored text back as it is: only a
    ``float`` field turns it into a number."""

    class Reading(fr.IDMixin, SharedBase):
        values: Mapped[dict[str, Any]] = mapped_column(
            fr.models.PydanticJSON(dict[str, Any])
        )

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Reading(values={"x": math.nan, "y": 1.5}))
        session.commit()
    with Session(engine) as session:
        values = session.scalars(sqlalchemy.select(Reading.values)).one()
    assert values == {"x": "NaN", "y": 1.5}


class _Reading(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(ser_json_inf_nan="strings")

    value: float
    extra: dict[str, Any] = {}


def test_a_model_with_the_strings_setting_keeps_a_nan_in_an_untyped_part(engine):
    """A model dumps a NaN in a part of type ``Any`` by its own
    ``ser_json_inf_nan``, and Pydantic's default writes ``None``. With
    ``"strings"``, as the docs advise for a model of your own, the column
    stores the text."""

    class Sensor(fr.IDMixin, SharedBase):
        reading: Mapped[_Reading]

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Sensor(reading=_Reading(value=math.inf, extra={"x": math.nan})))
        session.commit()
        assert _stored(session, "sensor", "reading") == [
            '{"value": "Infinity", "extra": {"x": "NaN"}}'
        ]


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


class _WriteOnlyDict(TypedDict):
    token: fr.WriteOnly[str]


class _OptionalExcludedDict(TypedDict, total=False):
    note: Annotated[str, pydantic.Field(exclude=True)]


class _Stamped(pydantic.BaseModel):
    street: str
    verified_at: fr.ReadOnly[datetime | None] = None


class _OptionalStamped(pydantic.BaseModel):
    verified_at: fr.ReadOnly[datetime] | None = None


class _StampedTags(pydantic.BaseModel):
    tags: list[fr.ReadOnly[str]] = []


class _StampedDict(TypedDict):
    verified_at: fr.ReadOnly[datetime]


class _StampedTuple(NamedTuple):
    verified_at: fr.ReadOnly[datetime]


class _ShopSchema(fr.IDSchema):
    name: str


_V = TypeVar("_V")


class _GenericDict(TypedDict, Generic[_V]):
    value: _V


class _GenericTuple(NamedTuple, Generic[_V]):
    value: _V


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
        (_WriteOnlyDict, "_WriteOnlyDict.token is left out of the dump"),
        (
            list[_OptionalExcludedDict],
            "_OptionalExcludedDict.note is left out of the dump",
        ),
        (list[_Point], "_Point in list[_Point] is a dataclass"),
        (_Polygon, "_Point in _Polygon.points is a dataclass"),
        (Iterable[int], "Pydantic validates Iterable[int] to an iterator"),
        # a request writes the whole document, so it would replace the value
        # that the server set
        (_Stamped, "_Stamped.verified_at is ReadOnly"),
        (dict[str, _Stamped], "_Stamped.verified_at is ReadOnly"),
        (_OptionalStamped, "_OptionalStamped.verified_at is ReadOnly"),
        (_StampedTags, "str in _StampedTags.tags is ReadOnly"),
        (_StampedDict, "_StampedDict.verified_at is ReadOnly"),
        (_StampedTuple, "_StampedTuple.verified_at is ReadOnly"),
        (list[_ShopSchema], "_ShopSchema.id is ReadOnly"),
        # the arguments of a generic TypedDict or NamedTuple
        (
            _GenericDict[pydantic.SecretStr],
            "SecretStr in _GenericDict[SecretStr] is a secret",
        ),
        (
            dict[str, _GenericDict[_WithWriteOnly]],
            "_WithWriteOnly.token is left out of the dump",
        ),
        (_GenericDict[fr.ReadOnly[str]], "str in _GenericDict"),
        (
            _GenericTuple[pydantic.SecretStr],
            "SecretStr in _GenericTuple[SecretStr] is a secret",
        ),
        (
            tuple[_GenericDict[int], _GenericDict[pydantic.SecretStr]],
            "SecretStr in tuple[_GenericDict[int], _GenericDict[SecretStr]] is a",
        ),
    ],
)
def test_a_type_that_the_dump_would_change_is_refused(type_, message):
    """The dump masks a secret and leaves out an excluded field. The column
    refuses such a type when it is defined, not at the first write."""
    with pytest.raises(RestlyConfigurationError, match=message.replace("[", r"\[")):
        fr.models.PydanticJSON(type_)


@pytest.mark.skipif(
    not hasattr(pydantic.fields.FieldInfo, "exclude_if"),
    reason="Pydantic has exclude_if from version 2.12",
)
def test_a_field_with_exclude_if_is_refused():
    class Note(pydantic.BaseModel):
        text: str = pydantic.Field("", exclude_if=lambda value: value == "")

    class Tags(TypedDict):
        main: Annotated[str, pydantic.Field(exclude_if=lambda value: value == "")]

    with pytest.raises(RestlyConfigurationError, match=r"Note.text has exclude_if"):
        fr.models.PydanticJSON(list[Note])
    with pytest.raises(RestlyConfigurationError, match=r"Tags.main has exclude_if"):
        fr.models.PydanticJSON(Tags)


def test_an_excluded_field_in_a_named_tuple_is_accepted():
    """A dump keeps every field of a NamedTuple, also an excluded one."""

    class Pair(NamedTuple):
        left: int
        right: Annotated[int, pydantic.Field(exclude=True)]

    column_type = fr.models.PydanticJSON(Pair)
    assert column_type.process_bind_param(Pair(1, 2), None) == [1, 2]


def test_a_readonly_field_is_refused_in_a_mapped_annotation():
    """The message names the field. The fix is a column of its own."""

    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    with pytest.raises(
        RestlyConfigurationError,
        match=r"^PydanticJSON\(_Stamped\): _Stamped.verified_at is ReadOnly\. ",
    ):

        class Shop(fr.IDMixin, Base):
            address: Mapped[_Stamped]


def test_a_reference_in_a_document_is_accepted():
    """The client sends the id of a reference, so its ReadOnly id does not
    count."""

    class Owner(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Line(pydantic.BaseModel):
        owner: fr.IDRef[Owner]
        approver: fr.IDSchema[Owner]

    column_type = fr.models.PydanticJSON(list[Line])
    stored = column_type.process_bind_param([{"owner": 1, "approver": {"id": 2}}], None)
    assert stored == [{"owner": 1, "approver": {"id": 2}}]
    (line,) = column_type.process_result_value(stored, None)
    assert (line.owner.id, line.approver.id) == (1, 2)


def test_a_readonly_field_in_a_part_of_type_any_fails_the_write(engine):
    class Vault(fr.IDMixin, SharedBase):
        items: Mapped[dict[str, Any]] = mapped_column(
            fr.models.PydanticJSON(dict[str, Any])
        )

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Vault(items={"home": _Stamped(street="Main")}))
        with pytest.raises(StatementError) as excinfo:
            session.flush()
    assert "_Stamped.verified_at is ReadOnly" in str(excinfo.value.orig)


def test_a_recursive_model_without_secrets_is_accepted():
    class Tree(pydantic.BaseModel):
        name: str
        children: list["Tree"] = []

    assert fr.models.PydanticJSON(Tree).type_ is Tree


def test_a_secret_in_a_part_of_type_any_fails_the_write(engine):
    """The type cannot tell what a part of type Any holds, so the write
    checks the value, as for a plain JSON column."""

    class Vault(fr.IDMixin, SharedBase):
        items: Mapped[dict[str, Any]] = mapped_column(
            fr.models.PydanticJSON(dict[str, Any])
        )

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
    Typed = define("Typed", fr.models.PydanticJSON(Address, none_as_null=none_as_null))
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
        address: Mapped[Address] = mapped_column(fr.models.PydanticJSON(Address))

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
            MutableDict.as_mutable(fr.models.PydanticJSON(dict[str, Address]))
        )

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Shop(branches={"north": Address(street="Main")}))
        session.commit()
    with Session(engine) as session:
        shop = session.scalars(sqlalchemy.select(Shop)).one()
        assert isinstance(shop.branches, MutableDict)
        shop.branches["north"].street = "Harbour"
        assert shop not in session.dirty
        shop.branches["south"] = Address(street="Dock")
        assert shop in session.dirty
        session.commit()
    with Session(engine) as session:
        shop = session.scalars(sqlalchemy.select(Shop)).one()
        # The new street of "north" was saved only because the commit wrote
        # the whole dict.
        assert shop.branches == {
            "north": Address(street="Harbour"),
            "south": Address(street="Dock"),
        }


def test_each_type_has_its_own_cache_key(engine):
    assert (
        fr.models.PydanticJSON(Address)._static_cache_key
        != fr.models.PydanticJSON(Home)._static_cache_key
    )
    assert (
        fr.models.PydanticJSON(Address)._static_cache_key
        == fr.models.PydanticJSON(Address)._static_cache_key
    )
    # none_as_null is in the key too, as in JSON. Without it, the second
    # statement would reuse the SQL of the first.
    with Session(engine) as session:
        for none_as_null in (False, True):
            column_type = fr.models.PydanticJSON(Address, none_as_null=none_as_null)
            stmt = sqlalchemy.select(sqlalchemy.literal(None, column_type).is_(None))
            assert session.scalar(stmt) is none_as_null
    adapted = fr.models.PydanticJSON(Address, none_as_null=True).adapt(
        fr.models.PydanticJSON
    )
    assert adapted.none_as_null


def test_the_type_must_be_hashable():
    with pytest.raises(RestlyConfigurationError, match="is not hashable"):
        fr.models.PydanticJSON(Annotated[list[int], {"unit": "cm"}])


def test_a_model_is_needed_in_the_annotation():
    with pytest.raises(RestlyConfigurationError, match="names no model"):

        class Shop(fr.IDMixin, SharedBase):
            address: Mapped[pydantic.BaseModel]


def test_the_label_keeps_text_in_quotes():
    column_type = fr.models.PydanticJSON(Literal["v1.2", 1.5])
    assert repr(column_type) == "PydanticJSON(Literal['v1.2', 1.5])"


def test_a_document_is_not_hashable_for_unique(engine):
    """As for JSON, SQLAlchemy cannot make the rows unique by a hash."""

    class Shop(fr.IDMixin, SharedBase):
        address: Mapped[Address]

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Shop(address=Address(street="Main")))
        session.commit()
        result = session.scalars(sqlalchemy.select(Shop.address)).unique()
        with pytest.raises(sqlalchemy.exc.InvalidRequestError, match="non-hashable"):
            result.all()


def test_a_subclass_can_use_a_type_decorator_as_its_impl(engine):
    class TextJSON(sqlalchemy.TypeDecorator):
        impl = sqlalchemy.JSON
        cache_ok = True

    class TextDocument(fr.models.PydanticJSON):
        impl = TextJSON
        cache_ok = True

    class Shop(fr.IDMixin, SharedBase):
        address: Mapped[Address] = mapped_column(TextDocument(Address))

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Shop(address=Address(street="Main")))
        session.commit()
        stmt = sqlalchemy.select(Shop.id).where(
            Shop.address["street"].as_string() == "Main"
        )
        assert session.scalars(stmt).all() == [1]


def test_the_metadata_pickles_after_a_comparison():
    """A comparison keeps the comparator on the column. Pickling the
    metadata then pickles the comparator too."""

    class Base(DeclarativeBase):
        pass

    class Shop(Base):
        __tablename__ = "shop"
        id: Mapped[int] = mapped_column(primary_key=True)
        address: Mapped[Address] = mapped_column(fr.models.PydanticJSON(Address))

    Shop.__table__.c.address.comparator  # noqa: B018
    metadata = pickle.loads(pickle.dumps(Base.metadata))
    assert repr(metadata.tables["shop"].c.address.type) == "PydanticJSON(Address)"


def test_pydantic_jsonb_is_a_jsonb_column():
    class Base(DeclarativeBase):
        pass

    class Shop(Base):
        __tablename__ = "shop"
        id: Mapped[int] = mapped_column(primary_key=True)
        address: Mapped[Address] = mapped_column(fr.models.PydanticJSONB(Address))
        plain: Mapped[Address] = mapped_column(fr.models.PydanticJSON(Address))

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
    assert fr.models.PydanticJSON(Address).python_type is Address
    assert fr.models.PydanticJSON(dict[str, Address]).python_type is dict
    short = Annotated[list[int], pydantic.Field(max_length=3)]
    assert fr.models.PydanticJSON(short).python_type is list
    with pytest.raises(NotImplementedError):
        fr.models.PydanticJSON(Address | None).python_type
    with pytest.raises(NotImplementedError):
        fr.models.PydanticJSON(Any).python_type


def test_a_column_without_annotation_gives_the_field_the_document_type():
    class Shop(fr.IDMixin, SharedBase):
        branches = mapped_column(
            fr.models.PydanticJSON(dict[str, Address]), nullable=False
        )
        home = mapped_column(fr.models.PydanticJSON(Home | None))

    fields = derive_schema(Shop).model_fields
    assert fields["branches"].annotation == dict[str, Address]
    assert fields["home"].annotation == Home | None


class _Cat(pydantic.BaseModel):
    meow: str


class _Dog(pydantic.BaseModel):
    woof: str


def test_a_union_document_keeps_every_member():
    class Pet(fr.IDMixin, SharedBase):
        pet: Mapped[_Cat | _Dog] = mapped_column(fr.models.PydanticJSON(_Cat | _Dog))
        other: Mapped[_Cat | _Dog | None] = mapped_column(
            fr.models.PydanticJSON(_Cat | _Dog | None)
        )
        bare = mapped_column(fr.models.PydanticJSON(_Cat | _Dog), nullable=False)
        plain: Mapped[int | str | None] = mapped_column(sqlalchemy.JSON)

    fields = derive_schema(Pet).model_fields
    assert fields["pet"].annotation == _Cat | _Dog
    assert fields["other"].annotation == _Cat | _Dog | None
    assert fields["bare"].annotation == _Cat | _Dog
    assert fields["plain"].annotation == int | str | None


def test_a_document_of_any_type_has_only_isnull(sync_client):
    """A field on a JSON column has only ``__isnull``, also a field of type
    Any, and a ``FilterKey`` on it takes only ``None``."""

    class Item(fr.IDMixin, SharedBase):
        doc: Mapped[Any] = mapped_column(
            fr.models.PydanticJSON(Any, none_as_null=True), nullable=True
        )

    @fr.include_view(sync_client.app)
    class ItemView(fr.RestView):
        prefix = "/items"
        model = Item

    SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])
    sync_client.post("/items", json={"doc": 5})
    sync_client.post("/items", json={"doc": None})
    found = sync_client.get("/items?doc__isnull=false").json()["data"]
    assert found == [{"id": 1, "doc": 5}]
    sync_client.get("/items?doc=5", assert_status_code=422)
    key = fr.query.FilterKey(Item, ItemView.response_schema, "doc")
    assert key.parse(None) is None
    with pytest.raises(fr.exc.BadQueryParam):
        key.parse("5")


def test_a_view_schema_can_have_its_own_document_class(sync_client):
    class Shop(fr.IDMixin, SharedBase):
        address: Mapped[Address]

    class AddressSchema(fr.BaseSchema):
        street: str
        number: int = 0

    class ShopSchema(fr.IDSchema):
        address: AddressSchema

    @fr.include_view(sync_client.app)
    class ShopView(fr.RestView):
        prefix = "/shops"
        model = Shop
        schema = ShopSchema

    SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])
    created = sync_client.post("/shops", json={"address": {"street": "Main"}}).json()
    assert created == {"id": 1, "address": {"street": "Main", "number": 0}}
    assert sync_client.get("/shops/1").json() == created


class _Point(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(ser_json_inf_nan="strings")

    lat: float


@needs_nan_in_responses
def test_a_nan_in_a_document_makes_the_round_trip_as_text(sync_client):
    """A response writes a document by the setting of its own class, not by
    the setting of ``fr.BaseSchema``. With ``ser_json_inf_nan="strings"``, as
    the docs advise, the response shows the text, and a ``float`` field reads
    the text from the request."""

    class Place(fr.IDMixin, SharedBase):
        point: Mapped[_Point]

    @fr.include_view(sync_client.app)
    class PlaceView(fr.RestView):
        prefix = "/places"
        model = Place

    SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])
    created = sync_client.post("/places", json={"point": {"lat": "Infinity"}})
    assert created.json() == {"id": 1, "point": {"lat": "Infinity"}}
    assert sync_client.get("/places/1").json() == created.json()


def test_the_whole_column_can_be_readonly(sync_client):
    """A ReadOnly field in the document is refused, but ReadOnly around the
    whole column works as for any column: the server sets the document."""

    class Shop(fr.IDMixin, SharedBase):
        name: Mapped[str]
        address: Mapped[Address | None] = mapped_column(default=None)

    class ShopSchema(fr.IDSchema):
        name: str
        address: fr.ReadOnly[Address | None] = None

    @fr.include_view(sync_client.app)
    class ShopView(fr.RestView):
        prefix = "/shops"
        model = Shop
        schema = ShopSchema

    SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])
    sync_client.post("/shops", json={"name": "Corner"})
    with fr.open_session() as session:
        session.get(Shop, 1).address = Address(street="Main")
        session.commit()
    sync_client.patch("/shops/1", json={"name": "Dock", "address": {"street": "X"}})
    shop = sync_client.get("/shops/1").json()
    assert (shop["name"], shop["address"]["street"]) == ("Dock", "Main")


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


def test_a_filter_key_takes_only_none_but_a_query_compares_the_document(engine):
    """A ``FilterKey`` on a JSON column takes only ``None``, as the generated
    filters have only ``__isnull``. A condition of your own still compares
    the whole document."""

    class Shop(fr.IDMixin, SharedBase):
        address: Mapped[Address] = mapped_column(fr.models.PydanticJSON(Address))

    class ShopSchema(fr.IDSchema):
        address: Address

    key = fr.query.FilterKey(Shop, ShopSchema, "address")
    with pytest.raises(fr.exc.BadQueryParam):
        key.parse({"street": "Dock"})

    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([Shop(address=Address(street=s)) for s in ("Main", "Dock")])
        session.commit()
        stmt = sqlalchemy.select(Shop.id).where(Shop.address == Address(street="Dock"))
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
