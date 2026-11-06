"""Each generated filter key has the type of its field.

A bad value is a ``422`` in FastAPI's form, whose location names the key. The
type also shows in OpenAPI. Bounds do not count: a filter compares a value, and
does not store it. Only ``__in`` and ``__not_in`` take several values.
"""

import asyncio
import dataclasses
import enum
import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

import pydantic
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import Field, HttpUrl, field_validator
from sqlalchemy import ForeignKey, Integer, String, TypeDecorator, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from starlette.datastructures import QueryParams

import fastapi_restly as fr
from fastapi_restly.query import apply_list_params, derive_list_params_schema
from fastapi_restly.testing import RestlyTestClient

from ._models import SharedBase
from .conftest import create_tables


class Base(DeclarativeBase):
    pass


class Vendor(fr.IDMixin, Base):
    __tablename__ = "key_types_vendor"
    name: Mapped[str]


class Product(fr.IDMixin, Base):
    __tablename__ = "key_types_product"
    name: Mapped[str]
    nick: Mapped[str] = mapped_column(default="abc")
    price: Mapped[Decimal] = mapped_column(default=Decimal("1.50"))
    cents: Mapped[int] = mapped_column(default=0)
    sold_on: Mapped[date] = mapped_column(default=date(2024, 1, 1))
    site: Mapped[str] = mapped_column(String, default="https://a.example/")
    vendor_id: Mapped[int | None] = mapped_column(
        ForeignKey("key_types_vendor.id"), default=None
    )


class ProductSchema(fr.IDSchema):
    name: str
    nick: Annotated[str, Field(min_length=3, pattern="^[a-z]+$")] = "abc"
    price: Annotated[Decimal, Field(gt=0, max_digits=6)] = Decimal("1.50")
    cents: int = 0
    sold_on: date = date(2024, 1, 1)
    site: HttpUrl = HttpUrl("https://a.example/")
    vendor_id: fr.IDRef[Vendor] | None = None

    @field_validator("cents", mode="before")
    @classmethod
    def _euros_to_cents(cls, value: Any, info: pydantic.ValidationInfo) -> Any:
        assert info.field_name == "cents"
        return int(value) * 100


@pytest.fixture
def api(client: RestlyTestClient) -> RestlyTestClient:
    @fr.include_view(client.app)
    class ProductView(fr.AsyncRestView):
        prefix = "/products"
        model = Product
        schema = ProductSchema

    asyncio.run(fr.db.async_create_all(Base))
    client.post("/products", json={"name": "cheap", "price": "1.50", "cents": 2})
    client.post("/products", json={"name": "dear", "price": "9.50", "cents": 9})
    return client


def names(client: RestlyTestClient, **params: Any) -> list[str]:
    rows = client.get("/products", params=params).json()["data"]
    return sorted(row["name"] for row in rows)


def parameters(client: RestlyTestClient) -> dict[str, dict[str, Any]]:
    operation = client.get("/openapi.json").json()["paths"]["/products"]["get"]
    return {parameter["name"]: parameter for parameter in operation["parameters"]}


def test_a_bad_value_is_a_422_that_names_the_key(api):
    response = api.get(
        "/products", params={"sold_on__gte": "x"}, assert_status_code=422
    )
    error = response.json()["detail"][0]
    assert error["loc"] == ["query", "sold_on__gte"]
    assert error["type"] == "date_from_datetime_parsing"
    assert error["input"] == "x"


def test_each_bad_value_of_a_request_is_named(api):
    response = api.get(
        "/products",
        params={"sold_on": "x", "id": "y", "page": "z"},
        assert_status_code=422,
    )
    locs = {tuple(error["loc"]) for error in response.json()["detail"]}
    assert locs == {("query", "sold_on"), ("query", "id"), ("query", "page")}


def test_the_message_of_a_value_may_hold_braces(client):
    class Tag(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class TagSchema(fr.IDSchema):
        name: str

        @field_validator("name")
        @classmethod
        def _no_braces(cls, value: str) -> str:
            if "{" in value:
                raise ValueError("no {braces} here")
            return value

    @fr.include_view(client.app)
    class TagView(fr.AsyncRestView):
        prefix = "/tags"
        model = Tag
        schema = TagSchema

    create_tables()
    for params in ({"name": "{"}, {"name__in": "a,{"}):
        response = client.get("/tags", params=params, assert_status_code=422)
        assert response.json()["detail"][0]["msg"] == ("Value error, no {braces} here")


def test_bounds_do_not_count(api):
    assert names(api, price__gte="0") == ["cheap", "dear"]
    assert names(api, price="-1") == []
    assert names(api, price__in="-1,1.5") == ["cheap"]
    assert names(api, nick="AB") == []
    assert names(api, nick__ne="AB") == ["cheap", "dear"]


def test_what_changes_a_value_stays(api):
    # the column holds cents, and the field takes euros, on every key
    assert names(api, cents=2) == ["cheap"]
    assert names(api, cents__gte=3) == ["dear"]
    assert names(api, cents__in="2,9") == ["cheap", "dear"]


def test_a_rich_type_compares_as_a_response_shows_it(api):
    # the same text finds what a POST stored
    assert names(api, site="https://a.example") == ["cheap", "dear"]
    api.get("/products", params={"site": "nope"}, assert_status_code=422)


def test_the_list_params_hold_the_type_of_the_field():
    params_model = derive_list_params_schema(ProductSchema, Product)
    params = params_model.model_validate(
        {
            "price__gte": "1.50",
            "sold_on__in": "2024-01-01,2024-01-02",
            "vendor_id": "7",
            "name__icontains": "  ab ",
            "cents": "3",
        }
    )
    assert params.price__gte == Decimal("1.50")
    assert params.sold_on__in == [date(2024, 1, 1), date(2024, 1, 2)]
    # a reference gives its id
    assert params.vendor_id == 7
    assert params.name__icontains == "ab"
    assert params.cents == 300


def test_validators_run_once():
    params_model = derive_list_params_schema(ProductSchema, Product)
    params = params_model.model_validate({"cents": "3"})
    stmt = apply_list_params(select(Product), params, Product, ProductSchema)
    assert ".cents = 300" in sql_of(stmt)


def sql_of(stmt: Any) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def test_query_params_take_several_values_as_they_are():
    def where(query: str) -> str:
        stmt = apply_list_params(
            select(Product), QueryParams(query), Product, ProductSchema, pagination=None
        )
        return sql_of(stmt)

    assert "IN ('a', 'b')" in where("name__in=a,b")
    assert "IN ('a,b', 'c')" in where("name__in=a,b&name__in=c")


def test_outside_a_view_the_last_value_of_a_repeat_counts():
    stmt = apply_list_params(
        select(Product),
        QueryParams("name=a&name=b"),
        Product,
        ProductSchema,
        pagination=None,
    )
    sql = sql_of(stmt)
    assert ".name = 'b'" in sql
    assert "'a'" not in sql


def test_openapi_shows_the_type_of_the_field_without_bounds(api):
    found = parameters(api)

    assert found["cents__gte"]["schema"]["anyOf"] == [
        {"type": "integer"},
        {"type": "null"},
    ]
    assert found["sold_on"]["schema"]["anyOf"] == [
        {"type": "string", "format": "date"},
        {"type": "null"},
    ]
    assert found["id__in"]["schema"]["anyOf"][0]["type"] == "array"
    assert found["sold_on__not_in"]["schema"]["anyOf"][0] == {
        "type": "array",
        "items": {"type": "string", "format": "date"},
    }
    assert found["name__icontains"]["schema"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    assert found["vendor_id__isnull"]["schema"]["anyOf"][0] == {"type": "boolean"}

    price = str(found["price"]["schema"])
    assert "number" in price
    for bound in ("exclusiveMinimum", "maxDigits"):
        assert bound not in price
    nick = str(found["nick"]["schema"])
    assert "minLength" not in nick
    assert "pattern" not in nick
    # the reference has the type of its column
    assert {"type": "integer"} in found["vendor_id"]["schema"]["anyOf"]


def test_each_description_is_written_once(api):
    for parameter in parameters(api).values():
        assert "description" not in parameter["schema"]
    found = parameters(api)
    assert found["name__in"]["description"] == (
        "Rows where ``name`` is one of these values. Separate the values with "
        "commas, or repeat the key."
    )
    assert found["name__ne"]["description"] == "Rows where ``name`` is not this value."
    assert found["cents__gte"]["description"] == "``cents >= value``."


def test_a_repeat_is_a_422_on_list_params_of_your_own():
    class Search(fr.query.PagedListParams):
        q: str | None = None
        tags: list[str] = []

    app = FastAPI()

    @fr.include_view(app)
    class ProductView(fr.AsyncRestView):
        prefix = "/items"
        model = Product
        schema = ProductSchema
        list_params_schema = Search

    client = RestlyTestClient(app)
    for url in ("/items?q=a&q=b", "/items?page=1&page=2"):
        error = client.get(url, assert_status_code=422).json()["detail"][0]
        assert error["type"] == "repeated_key"
    # a list field reads every value
    params = client.get("/openapi.json").json()["paths"]["/items"]["get"]["parameters"]
    assert "tags" in [parameter["name"] for parameter in params]


def test_list_params_of_your_own_keep_their_types():
    class Search(pydantic.BaseModel):
        sort: list[str] = []
        name__icontains: list[str] = []
        cents__in: list[int] = []
        sold_on__gte: date | None = None

    stmt = apply_list_params(
        select(Product),
        Search(
            sort=["-name", "id"],
            name__icontains=["a", "b"],
            cents__in=[1, 2],
            sold_on__gte=date(2024, 1, 2),
        ),
        Product,
        ProductSchema,
        pagination=None,
    )
    sql = sql_of(stmt)
    assert "ORDER BY" in sql and ".name DESC" in sql
    # each term of a text search must match
    assert sql.count("LIKE") == 2
    # the validator of the field runs on a value of your own
    assert "IN (100, 200)" in sql
    assert "'2024-01-02'" in sql


def test_an_own_filter_key_cannot_repeat(client):
    @fr.include_view(client.app)
    class ProductView(fr.AsyncRestView):
        prefix = "/products"
        model = Product
        schema = ProductSchema

        @fr.list_filter("q")
        def filter_q(self, value: str) -> Any:
            return Product.name.icontains(value, autoescape=True)

    asyncio.run(fr.db.async_create_all(Base))
    error = client.get("/products?q=a&q=b", assert_status_code=422).json()["detail"][0]
    assert error["type"] == "repeated_key"
    assert error["loc"] == ["query", "q"]
    assert error["input"] == ["a", "b"]


# ---------------------------------------------------------------------------
# Restly builds the type of a key when something first uses it
# ---------------------------------------------------------------------------


def test_a_view_registers_before_a_schema_that_its_schema_names():
    from . import _forward_ref_view as module

    asyncio.run(fr.db.async_create_all(module.Base))
    client = RestlyTestClient(module.app)
    client.post("/authors", json={"name": "Ada"})

    assert [row["name"] for row in client.get("/authors?name=Ada").json()["data"]] == [
        "Ada"
    ]
    client.get("/authors?id=x", assert_status_code=422)
    operation = client.get("/openapi.json").json()["paths"]["/authors"]["get"]
    found = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert found["name"]["schema"]["anyOf"] == [{"type": "string"}, {"type": "null"}]


def test_a_key_that_the_settings_leave_out_builds_nothing(monkeypatch):
    from fastapi_restly.query import _impl

    built: list[str] = []
    original = _impl._checked_reader

    def checked_reader(
        schema_cls: Any, field_name: str, column: Any, **kwargs: Any
    ) -> Any:
        built.append(field_name)
        return original(schema_cls, field_name, column, **kwargs)

    monkeypatch.setattr(_impl, "_checked_reader", checked_reader)
    params_model = derive_list_params_schema(
        ProductSchema, Product, include_filters=("name",)
    )
    assert built == []
    params_model.model_validate({"name": "a"})
    assert built == ["name"]


def test_openapi_takes_the_parameters_of_a_route_of_your_own(client):
    @fr.include_view(client.app)
    class ProductView(fr.AsyncRestView):
        prefix = "/products"
        model = Product
        schema = ProductSchema
        get_many_endpoint = fr.modify_route(
            openapi_extra={
                "x-owner": "me",
                "parameters": [
                    {"name": "x-trace", "in": "header", "schema": {"type": "string"}}
                ],
            }
        )

    operation = client.get("/openapi.json").json()["paths"]["/products"]["get"]
    names = [parameter["name"] for parameter in operation["parameters"]]
    assert operation["x-owner"] == "me"
    assert names[-1] == "x-trace"
    assert "name__in" in names


class Tree(pydantic.BaseModel):
    value: int = 1
    child: "Tree | None" = None


#: Each type registers, and OpenAPI is written.
_UNUSUAL_TYPES = {
    # a type that holds itself
    "tree": Tree | int,
    # a field that OpenAPI leaves out
    "skipped": pydantic.json_schema.SkipJsonSchema[str],
}


@pytest.mark.parametrize("annotation", _UNUSUAL_TYPES.values(), ids=_UNUSUAL_TYPES)
def test_an_unusual_type_registers(client, annotation):
    class Note(fr.IDMixin, SharedBase):
        text: Mapped[str]

    NoteSchema = pydantic.create_model(
        "NoteSchema", __base__=fr.IDSchema, text=(annotation, "x")
    )

    @fr.include_view(client.app)
    class NoteView(fr.AsyncRestView):
        prefix = "/notes"
        model = Note
        schema = NoteSchema

    assert "text__in" in NoteView.list_params_schema.model_fields
    client.get("/openapi.json")


def test_no_bound_stays_on_a_type_alias_or_in_the_config(client):
    from typing_extensions import TypeAliasType

    Positive = TypeAliasType("Positive", Annotated[int, Field(gt=0)])

    class Stock(fr.IDMixin, SharedBase):
        code: Mapped[str]
        count: Mapped[int] = mapped_column(default=1)
        minimum: Mapped[int] = mapped_column(default=1)

    class StockSchema(fr.IDSchema):
        model_config = pydantic.ConfigDict(str_max_length=3)
        code: str
        # two fields with the alias make it a definition in the core schema
        count: Positive = 1
        minimum: Positive = 1

    @fr.include_view(client.app)
    class StockView(fr.AsyncRestView):
        prefix = "/stock"
        model = Stock
        schema = StockSchema

    create_tables()
    client.post("/stock", json={"code": "abc"})
    for params in ({"count__gte": "0"}, {"minimum": "-1"}, {"code": "abcdef"}):
        client.get("/stock", params=params, assert_status_code=200)
    operation = client.get("/openapi.json").json()["paths"]["/stock"]["get"]
    found = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert "exclusiveMinimum" not in str(found["count"]["schema"])


def test_a_decimal_that_allows_nan_keeps_it():
    class Reading(fr.IDMixin, SharedBase):
        value: Mapped[Decimal]

    class ReadingSchema(fr.IDSchema):
        value: Annotated[Decimal, Field(allow_inf_nan=True)]

    params_model = derive_list_params_schema(ReadingSchema, Reading)
    assert params_model.model_validate({"value": "NaN"}).value.is_nan()


# ---------------------------------------------------------------------------
# A value that a validator turns into None
# ---------------------------------------------------------------------------


def _empty_to_none(value: Any) -> Any:
    return value or None


class Memo(fr.IDMixin, Base):
    __tablename__ = "key_types_memo"
    note: Mapped[str | None] = mapped_column(default=None)
    rank: Mapped[int] = mapped_column(default=0)


class MemoSchema(fr.IDSchema):
    note: Annotated[str | None, pydantic.BeforeValidator(_empty_to_none)] = None
    rank: Annotated[int | None, pydantic.BeforeValidator(_empty_to_none)] = 0


@pytest.fixture
def memos(client: RestlyTestClient) -> RestlyTestClient:
    @fr.include_view(client.app)
    class MemoView(fr.AsyncRestView):
        prefix = "/memos"
        model = Memo
        schema = MemoSchema

    asyncio.run(fr.db.async_create_all(Base))
    client.post("/memos", json={"note": "x", "rank": 1})
    client.post("/memos", json={"note": "y", "rank": 2})
    client.post("/memos", json={"note": None, "rank": 3})
    return client


def test_a_value_that_becomes_none_is_a_422_that_names_isnull(memos):
    response = memos.get("/memos", params={"note": ""}, assert_status_code=422)

    (error,) = response.json()["detail"]
    assert error["loc"] == ["query", "note"]
    assert error["msg"] == (
        "The value becomes null after validation, and a filter needs a value. "
        "To find the rows where it is null, send note__isnull=true."
    )


def test_a_range_key_that_becomes_none_is_a_422(memos):
    response = memos.get("/memos", params={"rank__gte": ""}, assert_status_code=422)

    (error,) = response.json()["detail"]
    assert error["loc"] == ["query", "rank__gte"]
    # the column cannot be NULL, so the field has no __isnull
    assert error["msg"] == (
        "The value becomes null after validation, and a filter needs a value."
    )


def test_a_list_item_that_becomes_none_is_a_422(memos):
    response = memos.get(
        "/memos", params={"note__not_in": "x,"}, assert_status_code=422
    )

    (error,) = response.json()["detail"]
    assert error["loc"] == ["query", "note__not_in", 1]


def test_an_empty_text_search_is_no_filter(memos):
    rows = memos.get("/memos", params={"note__icontains": ""}).json()["data"]
    assert len(rows) == 3


def test_a_none_from_code_is_no_filter():
    params_model = derive_list_params_schema(MemoSchema, Memo, pagination=None)

    stmt = apply_list_params(select(Memo), params_model(note=None), Memo, MemoSchema)
    assert stmt.whereclause is None


def test_a_list_of_values_cannot_hold_none():
    params_model = derive_list_params_schema(MemoSchema, Memo, pagination=None)

    with pytest.raises(pydantic.ValidationError) as exc_info:
        params_model(note__in=["x", None])

    (error,) = exc_info.value.errors()
    assert error["loc"] == ("note__in", 1)
    assert error["msg"] == "A list of values cannot hold null."


def test_a_filter_key_of_your_own_refuses_a_value_that_becomes_none():
    key = fr.query.FilterKey(Memo, MemoSchema, "note")

    with pytest.raises(fr.exc.BadQueryParam, match="becomes null after validation"):
        key.parse("")
    assert key.parse(None) is None


# ---------------------------------------------------------------------------
# A number in a filter is at most 1e50, as in django-filter
# ---------------------------------------------------------------------------


class Gauge(fr.IDMixin, Base):
    __tablename__ = "key_types_gauge"
    amount: Mapped[Decimal]
    reading: Mapped[Decimal]
    ratio: Mapped[float]
    count: Mapped[int]


class GaugeSchema(fr.IDSchema):
    amount: Annotated[Decimal, Field(max_digits=12)]
    reading: Annotated[Decimal, Field(allow_inf_nan=True)]
    ratio: float
    count: int


def gauge_params() -> type[pydantic.BaseModel]:
    return derive_list_params_schema(GaugeSchema, Gauge, pagination=None)


@pytest.mark.parametrize(
    "key, value",
    [
        ("amount", "1e999999"),
        ("amount__gte", "-1e51"),
        ("reading", "Infinity"),
        ("ratio", "1e400"),
        ("ratio__lt", "-inf"),
        ("ratio__in", "1,1e51"),
    ],
)
def test_a_number_beyond_1e50_fails(key, value):
    with pytest.raises(pydantic.ValidationError) as exc_info:
        gauge_params().model_validate({key: value})

    (error,) = exc_info.value.errors()
    assert error["msg"] == "A number in a filter must be between -1e50 and 1e50."


@pytest.mark.parametrize(
    "key, value",
    [("amount", "1e50"), ("amount", "-1e50"), ("ratio", "1e50"), ("reading", "NaN")],
)
def test_a_number_up_to_1e50_works(key, value):
    gauge_params().model_validate({key: value})


@pytest.mark.parametrize(
    "key, value", [("amount", "1e-100000"), ("reading__gte", "0." + "0" * 60 + "1")]
)
def test_a_decimal_with_more_than_50_digits_after_the_point_fails(key, value):
    with pytest.raises(pydantic.ValidationError) as exc_info:
        gauge_params().model_validate({key: value})

    (error,) = exc_info.value.errors()
    assert error["msg"] == "A number in a filter has at most 50 digits after the point."


@pytest.mark.parametrize(
    "key, value", [("amount", "0." + "0" * 49 + "1"), ("ratio", "1e-100000")]
)
def test_50_digits_after_the_point_and_a_tiny_float_work(key, value):
    gauge_params().model_validate({key: value})


def test_the_limit_comes_before_the_range_of_an_integer_column():
    with pytest.raises(pydantic.ValidationError) as exc_info:
        gauge_params().model_validate({"count": str(10**60)})

    (error,) = exc_info.value.errors()
    assert "1e50" in error["msg"]


def test_a_filter_key_of_your_own_caps_a_number():
    key = fr.query.FilterKey(Gauge, GaugeSchema, "ratio")

    with pytest.raises(fr.exc.BadQueryParam, match="between -1e50 and 1e50"):
        key.parse("1e400")


# ---------------------------------------------------------------------------
# A mapping from code holds values, not text
# ---------------------------------------------------------------------------


@pytest.fixture
def crates(sync_db):
    engine, make_session = sync_db

    class Crate(fr.IDMixin, SharedBase):
        label: Mapped[str]
        note: Mapped[str | None] = mapped_column(default=None)

    class CrateSchema(fr.IDSchema):
        label: str
        note: str | None = None

    class CrateView(fr.RestView):
        prefix = "/crates"
        model = Crate
        schema = CrateSchema

    SharedBase.metadata.create_all(engine)
    with make_session() as session:
        session.add_all(
            [Crate(label="a"), Crate(label="b", note="None"), Crate(label="c")]
        )
        session.flush()
        view = CrateView()
        view.session = session
        yield view


def labels(view: Any, list_params: dict[str, Any]) -> list[str]:
    return sorted(row.label for row in view.get_many(list_params).objects)


def test_an_empty_list_on_in_matches_no_rows(crates):
    assert labels(crates, {"id__in": []}) == []
    assert labels(crates, {"id__not_in": []}) == ["a", "b", "c"]


def test_a_set_or_a_tuple_holds_several_values(crates):
    assert labels(crates, {"label__in": {"a", "b"}}) == ["a", "b"]
    assert labels(crates, {"label__in": ("a",)}) == ["a"]
    assert labels(crates, {"label": ["a"]}) == ["a"]


@pytest.mark.parametrize(
    "key, isnull_key",
    [("note", "note__isnull"), ("id__in", None), ("label", None), ("sort", None)],
)
def test_none_in_a_mapping_fails(crates, key, isnull_key):
    # leaving the key out would return every row, such as for an id list
    # that the code did not compute
    with pytest.raises(RequestValidationError) as exc_info:
        crates.get_many({key: None})

    (error,) = exc_info.value.errors()
    assert error["loc"] == ("query", key)
    assert "Leave the key out for no filter" in error["msg"]
    # only a column that can be NULL has an __isnull key
    assert ("__isnull" in error["msg"]) == (isnull_key is not None)
    if isnull_key:
        assert f"use {isnull_key!r}: True" in error["msg"]


def test_none_in_a_mapping_fails_outside_a_view(crates):
    stmt = select(crates.model)
    with pytest.raises(fr.exc.BadQueryParam, match="Leave the key out for no filter"):
        apply_list_params(stmt, {"note": None}, crates.model, crates.schema)


def test_a_value_stays_a_value(crates):
    assert labels(crates, {"id": 1}) == ["a"]


@pytest.mark.parametrize("key", ["label", "label__icontains", "id"])
def test_an_empty_list_on_a_key_that_takes_one_value_fails(crates, key):
    with pytest.raises(RequestValidationError) as exc_info:
        crates.get_many({key: []})

    (error,) = exc_info.value.errors()
    assert error["loc"] == ("query", key)


def test_an_unknown_key_with_none_fails(crates):
    with pytest.raises(RequestValidationError):
        crates.get_many({"lable": None})


# ---------------------------------------------------------------------------
# Labels that are numbers read the number from the text
# ---------------------------------------------------------------------------


class Level(enum.Enum):
    low = 1
    high = 2


class Prio(enum.IntEnum):
    low = 1
    high = 2


class Task(fr.IDMixin, Base):
    __tablename__ = "key_types_task"
    lit: Mapped[int] = mapped_column(default=1)
    level: Mapped[Level] = mapped_column(default=Level.low)
    prio: Mapped[int] = mapped_column(default=1)
    share: Mapped[float] = mapped_column(default=1.5)
    maybe: Mapped[int | None] = mapped_column(default=None)


class TaskSchema(fr.IDSchema):
    lit: Literal[1, 2] = 1
    level: Level = Level.low
    prio: Prio = Prio.low
    share: Literal[1.5, 2.5] = 1.5
    maybe: Literal[1, 2] | None = None


@pytest.fixture
def tasks(client: RestlyTestClient) -> RestlyTestClient:
    @fr.include_view(client.app)
    class TaskView(fr.AsyncRestView):
        prefix = "/tasks"
        model = Task
        schema = TaskSchema

    asyncio.run(fr.db.async_create_all(Base))
    client.post("/tasks", json={})
    client.post(
        "/tasks", json={"lit": 2, "level": 2, "prio": 2, "share": 2.5, "maybe": 2}
    )
    return client


@pytest.mark.parametrize(
    "params",
    [
        {"lit": "2"},
        {"lit__in": "2"},
        {"level__not_in": "1"},
        {"level": "2"},
        {"level__ne": "1"},
        {"prio": "2"},
        {"share": "2.5"},
        {"maybe": "2"},
    ],
)
def test_a_label_that_is_a_number_comes_as_text(tasks, params):
    rows = tasks.get("/tasks", params=params).json()["data"]
    assert [row["lit"] for row in rows] == [2]


@pytest.mark.parametrize("value", ["3", "x", "1.5"])
def test_a_number_that_is_no_label_fails(tasks, value):
    response = tasks.get("/tasks", params={"lit": value}, assert_status_code=422)

    (error,) = response.json()["detail"]
    assert error["loc"] == ["query", "lit"]
    assert error["input"] == value


def test_mixed_labels_stay_as_they_are():
    from fastapi_restly.query._impl import _has_number_labels

    assert _has_number_labels(Literal[1, 2.5])
    assert _has_number_labels(Level)
    assert not _has_number_labels(Literal[1, "1"])
    assert not _has_number_labels(Literal[True, False])
    assert not _has_number_labels(Literal[1, True])
    assert not _has_number_labels(int)


# ---------------------------------------------------------------------------
# How the values of list params reach the filters
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Money:
    cents: int


class MoneyType(TypeDecorator[Money]):
    impl = Integer
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        return None if value is None else value.cents

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        return None if value is None else Money(value)


def _money_from_text(value: Any) -> Any:
    return Money(int(value)) if isinstance(value, str) else value


class LabelSort(str, enum.Enum):
    up = "label"
    down = "-label"


@pytest.fixture
def shelves(sync_db):
    engine, make_session = sync_db

    class Shelf(fr.IDMixin, SharedBase):
        label: Mapped[str]
        price: Mapped[Money] = mapped_column(MoneyType)
        stocked: Mapped[str]

    class ShelfSchema(fr.IDSchema):
        label: str
        price: Annotated[Money, pydantic.BeforeValidator(_money_from_text)]
        stocked: str

    SharedBase.metadata.create_all(engine)
    with make_session() as session:
        session.add_all(
            [
                Shelf(label="a", price=Money(5), stocked="2024-01-31"),
                Shelf(label="b", price=Money(7), stocked="2024-02-01"),
            ]
        )
        session.commit()

    def labels_of(list_params: Any) -> list[str]:
        stmt = apply_list_params(select(Shelf), list_params, Shelf, ShelfSchema)
        with make_session() as session:
            return [row.label for row in session.scalars(stmt)]

    return Shelf, ShelfSchema, labels_of


def test_a_generated_key_keeps_the_value_of_its_field(shelves):
    Shelf, ShelfSchema, labels_of = shelves
    params_model = derive_list_params_schema(ShelfSchema, Shelf, pagination=None)

    # a dump would make the dataclass a dict
    assert labels_of(params_model(price="5")) == ["a"]


def test_list_params_of_your_own_give_their_json_form(shelves):
    class OwnParams(pydantic.BaseModel):
        sort: LabelSort | None = None
        stocked: date | None = None
        label__icontains: tuple[str, ...] = ()

    _, _, labels_of = shelves

    assert labels_of(OwnParams(sort=LabelSort.down)) == ["b", "a"]
    # the date goes to the text field as the text of its JSON form
    assert labels_of(OwnParams(stocked=date(2024, 1, 31))) == ["a"]
    assert labels_of(OwnParams(label__icontains=("a",))) == ["a"]


class Shade(enum.Enum):
    red = "R"
    blue = "B"


class Level(enum.Enum):
    low = 1
    high = 2


class Swapped(enum.Enum):
    # each value is the name of the other member
    red = "blue"
    blue = "red"


@pytest.mark.parametrize(
    "enum_class, member",
    [(Shade, Shade.red), (Level, Level.low), (Swapped, Swapped.red)],
)
def test_a_field_of_type_any_on_an_enum_column_takes_the_value_it_shows(
    sync_db, enum_class, member
):
    engine, _ = sync_db

    class Paint(fr.IDMixin, SharedBase):
        shade: Mapped[enum_class]  # type: ignore[valid-type]

    class PaintSchema(fr.IDSchema):
        shade: Any

    app = FastAPI()

    @fr.include_view(app)
    class PaintView(fr.RestView):
        prefix = "/paints"
        model = Paint
        schema = PaintSchema

    SharedBase.metadata.create_all(engine)
    client = RestlyTestClient(app)
    for each in enum_class:
        client.post("/paints/", json={"shade": each.name})

    shown = member.value
    for params in ({"shade": str(shown)}, {"shade__in": str(shown)}):
        found = client.get("/paints/", params=params).json()["data"]
        assert [paint["shade"] for paint in found] == [shown], params
    params = client.get("/openapi.json").json()["paths"]["/paints"]["get"]["parameters"]
    (shade,) = [param for param in params if param["name"] == "shade"]
    (shown_values, _) = shade["schema"]["anyOf"]
    assert shown in shown_values["enum"]


def test_a_filter_value_that_becomes_a_list_or_a_dict_fails(sync_db):
    engine, make_session = sync_db

    class Sticker(fr.IDMixin, SharedBase):
        tags: Mapped[str]
        meta: Mapped[str | None] = mapped_column(default=None)

    class StickerSchema(fr.IDSchema):
        tags: pydantic.Json[str | list[str]]
        meta: pydantic.Json | None = None

    SharedBase.metadata.create_all(engine)
    with make_session() as session:
        session.add(Sticker(tags="x"))
        session.commit()
    params_model = derive_list_params_schema(StickerSchema, Sticker)

    stmt = apply_list_params(
        select(Sticker), params_model(tags='"x"'), Sticker, StickerSchema
    )
    with make_session() as session:
        assert [row.tags for row in session.scalars(stmt)] == ["x"]
    for key, value in [
        ("tags", '["x", "y"]'),
        ("tags__in", '["x"]'),
        ("tags__not_in", '["x"]'),
        ("meta", '{"a": 1}'),
    ]:
        with pytest.raises(pydantic.ValidationError, match="not a list or an object"):
            params_model.model_validate({key: value})
    key = fr.query.FilterKey(Sticker, StickerSchema, "tags")
    with pytest.raises(fr.exc.BadQueryParam, match="not a list or an object"):
        key.parse('["x"]')


def test_a_field_of_type_any_gets_range_keys_by_its_column():
    class Token(fr.IDMixin, SharedBase):
        uid: Mapped[uuid.UUID]
        flag: Mapped[bool]
        count: Mapped[int]

    class TokenSchema(fr.IDSchema):
        uid: Any
        flag: Any
        count: Any

    fields = derive_list_params_schema(TokenSchema, Token).model_fields

    assert "count__gte" in fields
    for key in ("uid__gte", "flag__gte", "flag__lt"):
        assert key not in fields, key
    assert "uid" in fields
    assert "flag" in fields
