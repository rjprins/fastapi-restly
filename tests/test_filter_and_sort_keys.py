"""``fr.query.FilterKey`` and ``fr.query.SortKey``: one key of list params of
your own.

Each finds the field of the response class that a client's key names, and
the column of the model. As in the generated list params, a field with an
alias has only its alias as a key, and a field that a response never shows
is no key. A hybrid property is a key too, when it has SQL.
``FilterKey.parse`` gives a value the validation of its field and the form
of its column, as the generated filters do. The react-admin views use both,
so a view with list params of its own can do what they do.
"""

import dataclasses
import enum
import json
from datetime import datetime, timezone
from typing import Annotated, Any

import pydantic
import pytest
import sqlalchemy
from sqlalchemy import DateTime, ForeignKey, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, composite, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.exc import BadQueryParam

from ._models import SharedBase
from .conftest import create_tables

KEY_CLASSES = pytest.mark.parametrize(
    ("key_class", "kind"),
    [(fr.query.FilterKey, "filter"), (fr.query.SortKey, "sort")],
    ids=["filter", "sort"],
)


@KEY_CLASSES
def test_a_key_names_a_field_by_its_name_or_by_its_alias(key_class, kind):
    """As in the generated list params, a field with an alias has only its
    alias as a key."""

    class Gadget(fr.IDMixin, SharedBase):
        name: Mapped[str]
        size: Mapped[int]

    class GadgetSchema(fr.IDSchema):
        name: str
        size: int = pydantic.Field(alias="gadgetSize")

    assert key_class(Gadget, GadgetSchema, "name").column is Gadget.name
    assert key_class(Gadget, GadgetSchema, "gadgetSize").column is Gadget.size
    assert key_class(Gadget, GadgetSchema, "gadgetSize").key == "gadgetSize"
    with pytest.raises(BadQueryParam) as caught:
        key_class(Gadget, GadgetSchema, "size")
    assert caught.value.detail == f"Unknown {kind} key: 'size'"


@KEY_CLASSES
def test_a_key_is_unknown_for_a_field_without_a_value_to_show(key_class, kind):
    """A field that a response never shows has no key, or the order or the
    filter would let a client find out its value. Neither has a field
    without SQL, a relationship, or a field of a related row."""

    class Maker(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Gadget(fr.IDMixin, SharedBase):
        name: Mapped[str]
        secret: Mapped[str] = mapped_column(default="")
        hidden: Mapped[str] = mapped_column(default="")
        password: Mapped[str] = mapped_column(default="")
        maker_id: Mapped[int | None] = mapped_column(
            ForeignKey("maker.id"), default=None
        )
        maker: Mapped[Maker | None] = relationship(default=None)
        label = ""

        @property
        def shout(self) -> str:
            return self.name.upper()

    class MakerSchema(fr.IDSchema):
        name: str

    class GadgetSchema(fr.IDSchema):
        name: str
        secret: fr.WriteOnly[str]
        hidden: str = pydantic.Field("", exclude=True)
        password: pydantic.SecretStr
        maker: MakerSchema | None = None
        label: fr.ReadOnly[str] = ""
        shout: fr.ReadOnly[str] = ""

    for key in (
        "secret",
        "hidden",
        "password",
        "maker",
        "label",
        "shout",
        "maker.name",
        "nope",
    ):
        with pytest.raises(BadQueryParam) as caught:
            key_class(Gadget, GadgetSchema, key)
        assert caught.value.status_code == 400
        assert caught.value.detail == f"Unknown {kind} key: {key!r}", key


@KEY_CLASSES
def test_a_key_names_a_hybrid_property(key_class, kind):
    """A hybrid property has SQL, so it filters and sorts."""

    class Lamp(fr.IDMixin, SharedBase):
        name: Mapped[str]

        @hybrid_property
        def loud(self) -> str:
            return self.name + "!"

    class LampSchema(fr.IDSchema):
        name: str
        loud: fr.ReadOnly[str] = ""

    key = key_class(Lamp, LampSchema, "loud")
    assert str(key.column) == str(Lamp.loud)
    if kind == "filter":
        assert key.parse("Desk!") == "Desk!"


@dataclasses.dataclass
class _Point:
    x: int
    y: int


@KEY_CLASSES
def test_a_key_is_unknown_for_a_hybrid_in_python_or_a_composite(key_class, kind):
    """A hybrid property that is plain Python is a Python value on the class,
    so ``==`` would give ``WHERE false`` and the wrong rows. A composite is
    several columns, and a sort on it would reverse only the last one."""

    class Gadget(fr.IDMixin, SharedBase):
        name: Mapped[str] = mapped_column(default="")
        x: Mapped[int] = mapped_column(default=0)
        y: Mapped[int] = mapped_column(default=0)
        deleted_at: Mapped[datetime | None] = mapped_column(default=None)
        point: Mapped[_Point] = composite("x", "y", init=False)

        @hybrid_property
        def is_deleted(self) -> bool:
            return self.deleted_at is not None

        @hybrid_property
        def shout(self) -> str:
            return f"{self.name}!"

    class GadgetSchema(fr.IDSchema):
        is_deleted: fr.ReadOnly[bool] = False
        shout: fr.ReadOnly[str] = ""
        point: fr.ReadOnly[Any] = None

    for key in ("is_deleted", "shout", "point"):
        with pytest.raises(BadQueryParam) as caught:
            key_class(Gadget, GadgetSchema, key)
        assert caught.value.detail == f"Unknown {kind} key: {key!r}", key


def test_parse_runs_the_validation_of_the_field():
    """A text from a URL becomes the type of the field, and the field's
    validators run. A number from JSON becomes text for a text field."""

    class Gadget(fr.IDMixin, SharedBase):
        name: Mapped[str]
        size: Mapped[int]

    class GadgetSchema(fr.IDSchema):
        name: Annotated[str, pydantic.AfterValidator(str.lower)]
        size: int

    name = fr.query.FilterKey(Gadget, GadgetSchema, "name")
    size = fr.query.FilterKey(Gadget, GadgetSchema, "size")

    assert name.parse("DESK") == "desk"
    assert name.parse(5) == "5"
    assert size.parse("5") == 5
    assert size.parse(5) == 5
    with pytest.raises(BadQueryParam) as caught:
        size.parse("many")
    assert caught.value.status_code == 400
    assert caught.value.detail == (
        "Invalid filter value for 'size': 'many'. "
        "Input should be a valid integer, unable to parse string as an integer"
    )


def test_parse_gives_a_reference_its_id_and_checks_the_labels_of_an_enum():
    class Maker(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Gadget(fr.IDMixin, SharedBase):
        maker_id: Mapped[int] = mapped_column(ForeignKey("maker.id"))
        status: Mapped[str] = mapped_column(
            sqlalchemy.Enum("draft", "published", name="gadget_status")
        )

    class GadgetSchema(fr.IDSchema):
        maker_id: fr.IDRef[Maker]
        status: str

    assert fr.query.FilterKey(Gadget, GadgetSchema, "maker_id").parse("5") == 5
    status = fr.query.FilterKey(Gadget, GadgetSchema, "status")
    assert status.parse("draft") == "draft"
    with pytest.raises(BadQueryParam) as caught:
        status.parse("bogus")
    assert caught.value.detail == (
        "Invalid filter value for 'status': 'bogus'. Use one of: draft, published."
    )


class _Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


class _Priority(enum.IntEnum):
    LOW = 1
    HIGH = 2


def test_parse_keeps_none_where_the_field_allows_it():
    """``None`` gives ``IS NULL``, also on a column of SQLAlchemy's ``Enum``
    type, which has no label for it. A field that does not allow ``None``
    refuses it."""

    class Gadget(fr.IDMixin, SharedBase):
        name: Mapped[str]
        color: Mapped[_Color | None] = mapped_column(default=None)
        note: Mapped[str | None] = mapped_column(default=None)

    class GadgetSchema(fr.IDSchema):
        name: str
        color: _Color | None = None
        note: str | None = None

    for key in ("color", "note"):
        assert fr.query.FilterKey(Gadget, GadgetSchema, key).parse(None) is None
    with pytest.raises(BadQueryParam) as caught:
        fr.query.FilterKey(Gadget, GadgetSchema, "name").parse(None)
    assert caught.value.detail.startswith("Invalid filter value for 'name': None.")


def test_parse_gives_the_member_for_a_number_of_an_int_enum():
    """A column of SQLAlchemy's ``Enum`` type holds the names of the members.
    SQLAlchemy binds a plain ``1`` as a number, so the value becomes the
    member that it equals, which binds as its name."""

    class Task(fr.IDMixin, SharedBase):
        priority: Mapped[_Priority]

    class TaskSchema(fr.IDSchema):
        priority: int

    priority = fr.query.FilterKey(Task, TaskSchema, "priority")
    assert priority.parse("1") is _Priority.LOW
    sql = select(Task.id).where(priority.column == priority.parse(2))
    compiled = sql.compile(compile_kwargs={"literal_binds": True})
    assert "task.priority = 'HIGH'" in str(compiled)


def test_parse_gives_json_data_for_a_model_in_a_json_column():
    """A field typed as a model validates to a model, and a ``JSON`` column
    binds through ``json.dumps``. The value becomes JSON data, as a write
    stores it."""

    class Settings(pydantic.BaseModel):
        theme: str
        size: int = 12

    class Profile(fr.IDMixin, SharedBase):
        settings: Mapped[dict[str, Any]] = mapped_column(sqlalchemy.JSON)

    class ProfileSchema(fr.IDSchema):
        settings: Settings

    key = fr.query.FilterKey(Profile, ProfileSchema, "settings")
    assert key.parse({"theme": "dark"}) == {"theme": "dark", "size": 12}


def test_parse_gives_a_time_the_form_of_its_column():
    """A time without an offset is UTC; see ``tests/test_utc_times.py``."""

    class Meeting(fr.IDMixin, SharedBase):
        # a column without a time zone
        starts_at: Mapped[datetime] = mapped_column(DateTime())
        # a column with a time zone, from fr.ModelDefaults
        ends_at: Mapped[datetime]

    class MeetingSchema(fr.IDSchema):
        starts_at: datetime
        ends_at: datetime

    starts_at = fr.query.FilterKey(Meeting, MeetingSchema, "starts_at")
    ends_at = fr.query.FilterKey(Meeting, MeetingSchema, "ends_at")

    assert starts_at.parse("2024-01-01T10:30:00+01:00") == datetime(2024, 1, 1, 9, 30)
    utc = ends_at.parse("2024-01-01T09:30:00")
    assert utc == datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc)
    assert utc.utcoffset() is not None


def test_a_sort_term_puts_the_nulls_where_sort_nulls_says():
    class Gadget(fr.IDMixin, SharedBase):
        name: Mapped[str]
        size: Mapped[int | None] = mapped_column(default=None)

    class GadgetSchema(fr.IDSchema):
        name: str
        size: int | None = None

    def order_by(term: Any) -> str:
        stmt = select(Gadget.id).order_by(term)
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        return sql.partition("ORDER BY ")[2]

    size = fr.query.SortKey(Gadget, GadgetSchema, "size")
    assert order_by(size.term()) == "gadget.size ASC NULLS LAST"
    assert order_by(size.term(descending=True)) == "gadget.size DESC NULLS LAST"
    assert order_by(size.term(sort_nulls="first")) == "gadget.size ASC NULLS FIRST"
    assert order_by(size.term(descending=True, sort_nulls=None)) == "gadget.size DESC"
    # a column that cannot be NULL gets a plain term
    name = fr.query.SortKey(Gadget, GadgetSchema, "name")
    assert order_by(name.term(descending=True)) == "gadget.name DESC"


def test_a_key_shows_the_model_the_schema_and_the_key():
    class Gadget(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class GadgetSchema(fr.IDSchema):
        name: str

    assert repr(fr.query.FilterKey(Gadget, GadgetSchema, "name")) == (
        "FilterKey(Gadget, GadgetSchema, 'name')"
    )
    assert repr(fr.query.SortKey(Gadget, GadgetSchema, "name")) == (
        "SortKey(Gadget, GadgetSchema, 'name')"
    )


def test_list_params_of_your_own_filter_and_sort_with_the_keys(client):
    """The example of the docs: a view with a JSON ``filter`` and a ``sort``
    of its own."""

    class Item(fr.IDMixin, SharedBase):
        name: Mapped[str]
        category: Mapped[str]
        secret: Mapped[str] = mapped_column(default="")

    class ItemSchema(fr.IDSchema):
        name: str
        category: str
        secret: fr.WriteOnly[str] = ""

    class ItemListParams(fr.query.PagedListParams):
        filter: pydantic.Json[dict[str, Any]] | None = None
        sort: str | None = None

    @fr.include_view(client.app)
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        schema_list_params = ItemListParams

        async def apply_filters(self, stmt, list_params: ItemListParams):
            for name, value in (list_params.filter or {}).items():
                key = fr.query.FilterKey(self.model, self.schema_response, name)
                stmt = stmt.where(key.column == key.parse(value))
            return stmt

        async def apply_list_params(self, stmt, list_params: ItemListParams):
            stmt = await self.apply_filters(stmt, list_params)
            sorted_on = []
            if list_params.sort:
                name = list_params.sort.removeprefix("-")
                key = fr.query.SortKey(self.model, self.schema_response, name)
                descending = list_params.sort.startswith("-")
                stmt = stmt.order_by(
                    key.term(descending=descending, sort_nulls=self.sort_nulls)
                )
                sorted_on.append(key.column)
            stmt = fr.query.order_by_primary_key(stmt, self.model, sorted_on=sorted_on)
            offset = (list_params.page - 1) * list_params.page_size
            return stmt.limit(list_params.page_size).offset(offset)

    create_tables()
    for name, category in [("axe", "tools"), ("saw", "tools"), ("pie", "food")]:
        client.post("/items", json={"name": name, "category": category})

    def names(**params: str) -> list[str]:
        rows = client.get("/items", params=params).json()["data"]
        return [row["name"] for row in rows]

    assert names(filter=json.dumps({"category": "tools"}), sort="-name") == [
        "saw",
        "axe",
    ]
    # the page fields page the rows, and the envelope tells the client so
    body = client.get(
        "/items", params={"sort": "name", "page": "2", "page_size": "2"}
    ).json()
    assert [row["name"] for row in body["data"]] == ["saw"]
    assert {key: body[key] for key in body if key != "data"} == {
        "total_count": 3,
        "page": 2,
        "page_size": 2,
        "total_pages": 2,
    }
    for params, detail in [
        ({"filter": json.dumps({"secret": ""})}, "Unknown filter key: 'secret'"),
        ({"sort": "secret"}, "Unknown sort key: 'secret'"),
    ]:
        response = client.get("/items", params=params, assert_status_code=400)
        assert response.json()["detail"] == detail
