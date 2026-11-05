"""Each generated filter key has the type of its field.

A bad value is a ``422`` in FastAPI's form, whose location names the key. The
type also shows in OpenAPI. Bounds do not count: a filter compares a value, and
does not store it. Only ``__in`` and ``__not_in`` take several values.
"""

import asyncio
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

import pydantic
import pytest
from fastapi import FastAPI
from pydantic import Field, HttpUrl, field_validator
from sqlalchemy import ForeignKey, String, select
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


def test_the_limit_comes_before_the_range_of_an_integer_column():
    with pytest.raises(pydantic.ValidationError) as exc_info:
        gauge_params().model_validate({"count": str(10**60)})

    (error,) = exc_info.value.errors()
    assert "1e50" in error["msg"]


def test_a_filter_key_of_your_own_caps_a_number():
    key = fr.query.FilterKey(Gauge, GaugeSchema, "ratio")

    with pytest.raises(fr.exc.BadQueryParam, match="between -1e50 and 1e50"):
        key.parse("1e400")
