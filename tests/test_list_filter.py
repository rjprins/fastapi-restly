"""``@fr.list_filter``: a filter key of the view's own, from a method.

The type of the value becomes the type of the key in the list params, and
the method returns a condition, a clause or ``None``.
"""

import asyncio
from collections.abc import Iterator
from typing import Any

import pydantic
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import ColumnElement, ForeignKey, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.exc import (
    RestlyConfigurationError,
    RestlySkippedFilterMethodsWarning,
)
from fastapi_restly.query import apply_list_params
from fastapi_restly.testing import RestlyTestClient


class Base(DeclarativeBase):
    pass


class Vendor(Base):
    __tablename__ = "list_filter_vendor"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


class Item(Base):
    __tablename__ = "list_filter_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    view_count: Mapped[int]
    archived: Mapped[bool]
    vendor_id: Mapped[int] = mapped_column(ForeignKey(Vendor.id))
    vendor: Mapped[Vendor] = relationship()


class VendorSchema(fr.IDSchema):
    name: str


class ItemSchema(fr.IDSchema):
    name: str
    vendor: VendorSchema


class ItemClauses(fr.ClauseNamespace):
    @fr.where_clause
    def popular() -> ColumnElement[bool]:
        return Item.view_count > 1000


def rows() -> list[Any]:
    return [
        Vendor(id=1, name="acme"),
        Vendor(id=2, name="globex"),
        Item(id=1, name="Anvil", view_count=5000, archived=False, vendor_id=1),
        Item(id=2, name="axe", view_count=10, archived=False, vendor_id=2),
        Item(id=3, name="apple", view_count=2000, archived=True, vendor_id=1),
        Item(id=4, name="bread", view_count=1, archived=True, vendor_id=2),
    ]


@pytest.fixture(params=[fr.RestView, fr.AsyncRestView], ids=["sync", "async"])
def view_class(request: pytest.FixtureRequest) -> type:
    if request.param is fr.RestView:
        engine, make_session = request.getfixturevalue("sync_db")
        Base.metadata.create_all(engine)
        with make_session() as session:
            session.add_all(rows())
            session.commit()
    else:

        async def prepare() -> None:
            engine = fr.db.get_async_engine()
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine)() as session:
                session.add_all(rows())
                await session.commit()

        asyncio.run(prepare())
    return request.param


def client_for(view: type) -> RestlyTestClient:
    app = FastAPI()
    fr.include_view(app, view)
    return RestlyTestClient(app)


def names(client: RestlyTestClient, **params: Any) -> list[str]:
    payload = client.get("/items/", params=params).json()
    return [row["name"] for row in payload["data"]]


def query_parameter(client: RestlyTestClient, key: str) -> dict[str, Any]:
    operation = client.get("/openapi.json").json()["paths"]["/items"]["get"]
    [parameter] = [p for p in operation["parameters"] if p["name"] == key]
    return parameter


calls: list[Any] = []


@pytest.fixture
def client(view_class: type) -> Iterator[RestlyTestClient]:
    calls.clear()

    class ItemView(view_class):  # type: ignore[valid-type,misc]
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.list_filter("popular")
        def filter_popular(self, value: bool) -> ColumnElement[bool]:
            """Items with more than 1000 views, or with false the other items."""
            calls.append(value)
            popular = Item.view_count > 1000
            return popular if value else ~popular

        @fr.list_filter("allow_archived")
        def filter_allow_archived(
            self, value: bool = False
        ) -> ColumnElement[bool] | None:
            return None if value else ~Item.archived

        @fr.list_filter("names")
        def filter_names(self, value: list[str]) -> ColumnElement[bool]:
            return Item.name.in_(value)

        @fr.list_filter("name_like")
        def filter_name_like(self, value: str) -> Any:
            return Item.name.ilike(value)

    with client_for(ItemView) as client:
        yield client


def test_the_method_filters_the_list(client):
    assert names(client) == ["Anvil", "axe"]
    assert calls == []
    assert names(client, popular="true") == ["Anvil"]
    assert names(client, popular="false") == ["axe"]
    assert calls == [True, False]


def test_a_bad_value_gets_422(client):
    detail = client.get(
        "/items/", params={"popular": "maybe"}, assert_status_code=422
    ).json()["detail"]

    assert detail[0]["loc"] == ["query", "popular"]
    assert calls == []


def test_a_default_runs_the_filter_on_every_request(client):
    assert names(client, allow_archived="true") == ["Anvil", "axe", "apple", "bread"]
    assert names(client, allow_archived="true", popular="true") == ["Anvil", "apple"]


def test_a_list_reads_the_repeated_key(client):
    assert names(client, names=["axe", "Anvil", "bread"]) == ["Anvil", "axe"]


def test_the_key_works_with_the_generated_keys(client):
    assert names(client, name_like="a%", popular="false") == ["axe"]
    assert names(client, name__icontains="AN") == ["Anvil"]


def test_openapi_shows_the_type_and_the_docstring(client):
    popular = query_parameter(client, "popular")
    assert popular["required"] is False
    assert popular["schema"]["anyOf"] == [{"type": "boolean"}, {"type": "null"}]
    assert popular["description"] == (
        "Items with more than 1000 views, or with false the other items."
    )
    allow_archived = query_parameter(client, "allow_archived")
    assert allow_archived["schema"]["type"] == "boolean"
    assert allow_archived["schema"]["default"] is False
    names_schema = query_parameter(client, "names")["schema"]
    assert {"type": "array", "items": {"type": "string"}} in names_schema["anyOf"]


def test_a_method_can_return_a_clause(view_class):
    class ItemView(view_class):  # type: ignore[valid-type,misc]
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.list_filter("popular")
        def filter_popular(self, value: bool) -> fr.WhereClause | None:
            return ItemClauses.popular if value else None

    with client_for(ItemView) as client:
        assert names(client, popular="true") == ["Anvil", "apple"]
        assert names(client, popular="false") == ["Anvil", "axe", "apple", "bread"]


@pytest.mark.parametrize("view_class", [fr.AsyncRestView], indirect=True)
def test_an_async_method_can_load_data(view_class):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.list_filter("vendor")
        async def filter_vendor(self, value: str) -> ColumnElement[bool]:
            """Items of the vendor with this name."""
            vendor_ids = await self.session.scalars(
                select(Vendor.id).where(Vendor.name == value)
            )
            return Item.vendor_id.in_(list(vendor_ids))

    with client_for(ItemView) as client:
        assert names(client, vendor="globex") == ["axe", "bread"]


def test_an_async_method_on_a_sync_view_fails():
    with pytest.raises(RestlyConfigurationError) as exc_info:

        class ItemView(fr.RestView):
            @fr.list_filter("vendor")
            async def filter_vendor(self, value: str) -> Any:
                return None

    assert str(exc_info.value) == (
        "ItemView.filter_vendor is 'async def', but a sync view calls it "
        "without await. Write 'def filter_vendor', or use fr.AsyncRestView."
    )


class PopularView(fr.AsyncRestView):
    prefix = "/items"
    model = Item
    schema = ItemSchema

    @fr.list_filter("popular")
    def filter_popular(self, value: bool) -> ColumnElement[bool]:
        return Item.view_count > 1000 if value else Item.view_count <= 1000


def own_keys(view: type) -> set[str]:
    fr.include_view(FastAPI(), view)
    return set(view.schema_list_params.model_fields) & {"popular", "hot"}


def test_a_subclass_inherits_the_key():
    class ItemView(PopularView):
        pass

    assert own_keys(ItemView) == {"popular"}


def test_a_subclass_drops_the_key_with_none():
    class ItemView(PopularView):
        filter_popular = None  # type: ignore[assignment]

    assert own_keys(ItemView) == set()


def test_a_subclass_changes_the_key_with_the_decorator():
    class ItemView(PopularView):
        @fr.list_filter("hot")
        def filter_popular(self, value: bool) -> ColumnElement[bool]:
            return Item.view_count > 5000

    assert own_keys(ItemView) == {"hot"}


def test_an_override_without_the_decorator_fails():
    with pytest.raises(RestlyConfigurationError) as exc_info:

        class ItemView(PopularView):
            def filter_popular(self, value: bool) -> ColumnElement[bool]:
                return Item.view_count > 5000

    assert str(exc_info.value) == (
        "ItemView.filter_popular overrides the filter method of PopularView "
        "without @fr.list_filter. Add @fr.list_filter('popular') to keep the "
        "key, or set filter_popular = None to drop it."
    )


def test_a_key_of_a_generated_filter_that_is_on_fails():
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.list_filter("name")
        def filter_name(self, value: str) -> ColumnElement[bool]:
            return Item.name.ilike(value)

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(FastAPI(), ItemView)

    assert str(exc_info.value) == (
        "ItemView.filter_name: the key 'name' is also a generated filter key. "
        "To replace it, leave it out with exclude_filters = ('name',). Then "
        "choose what happens to name__in and the other keys of the field."
    )


@pytest.mark.parametrize("view_class", [fr.AsyncRestView], indirect=True)
def test_a_key_can_replace_a_generated_key_that_is_off(view_class):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        exclude_filters = ("name",)

        @fr.list_filter("name")
        def filter_name(self, value: str) -> ColumnElement[bool]:
            """The name, in any case."""
            return Item.name.ilike(value)

    with client_for(ItemView) as client:
        assert names(client, name="ANVIL") == ["Anvil"]
        assert query_parameter(client, "name")["description"] == (
            "The name, in any case."
        )


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("a__b", "'__' is for the suffixes of the generated filters"),
        ("a.b", "'__' is for the suffixes of the generated filters"),
        ("_private", "cannot start with '_'"),
        ("copy", "be the name of a Pydantic model attribute"),
        ("", "needs a key as a non-empty string"),
    ],
)
def test_a_key_follows_the_rules_of_a_query_parameter(key, message):
    with pytest.raises(RestlyConfigurationError, match=message):
        fr.list_filter(key)


def test_a_condition_reads_a_context_member(view_class):
    class Current(fr.ContextNamespace):
        min_views: fr.ContextParam[int]

    async def bind_min_views():
        with Current.bind(min_views=1000):
            yield

    class ItemView(view_class):  # type: ignore[valid-type,misc]
        prefix = "/items"
        model = Item
        schema = ItemSchema
        dependencies = [Depends(bind_min_views)]

        @fr.list_filter("popular")
        def filter_popular(self, value: bool) -> ColumnElement[bool]:
            return Item.view_count > Current.min_views

    with client_for(ItemView) as client:
        assert names(client, popular=True) == ["Anvil", "apple"]


def test_a_key_of_a_page_parameter_fails():
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.list_filter("page")
        def filter_page(self, value: int) -> Any:
            return None

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(FastAPI(), ItemView)
    assert str(exc_info.value) == (
        "ItemView.filter_page: the key 'page' is a page parameter or the sort "
        "parameter. Choose another key."
    )

    class UnpagedItemView(ItemView):
        pagination = None

    fr.include_view(FastAPI(), UnpagedItemView)
    assert "page" in UnpagedItemView.schema_list_params.model_fields


def test_the_key_of_the_sort_parameter_is_free_after_a_rename():
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.list_filter("sort")
        def filter_sort(self, value: str) -> Any:
            return None

    with pytest.raises(RestlyConfigurationError, match="the sort parameter"):
        fr.include_view(FastAPI(), ItemView)

    class OrderedItemView(ItemView):
        sort_query_param = "order_by"

    fr.include_view(FastAPI(), OrderedItemView)
    fields = OrderedItemView.schema_list_params.model_fields
    assert {"sort", "order_by"} <= set(fields)


def test_two_methods_with_one_key_fail():
    class ItemView(PopularView):
        @fr.list_filter("popular")
        def filter_hot(self, value: bool) -> Any:
            return None

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(FastAPI(), ItemView)

    assert str(exc_info.value) == (
        "ItemView: filter_popular and filter_hot are both the filter for the "
        "key 'popular'."
    )


def test_the_method_takes_one_value_with_a_type():
    class NoType(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.list_filter("popular")
        def filter_popular(self, value):  # type: ignore[no-untyped-def]
            return None

    with pytest.raises(RestlyConfigurationError, match="needs a type on 'value'"):
        fr.include_view(FastAPI(), NoType)

    class TwoValues(NoType):
        @fr.list_filter("popular")
        def filter_popular(self, value: bool, other: bool) -> Any:  # type: ignore[override]
            return None

    with pytest.raises(RestlyConfigurationError, match="must take one value"):
        fr.include_view(FastAPI(), TwoValues)


def test_a_string_annotation_is_resolved():
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.list_filter("popular")
        def filter_popular(self, value: "bool") -> Any:
            return None

    fr.include_view(FastAPI(), ItemView)
    field = ItemView.schema_list_params.model_fields["popular"]
    assert field.annotation == bool | None


@pytest.mark.parametrize("view_class", [fr.AsyncRestView], indirect=True)
def test_a_condition_on_a_table_outside_the_statement_fails(view_class):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.list_filter("vendor")
        def filter_vendor(self, value: str) -> ColumnElement[bool]:
            # without a join, SQL would add a cross join outside the scope
            return Vendor.name == value

        @fr.list_filter("vendor_has")
        def filter_vendor_has(self, value: str) -> ColumnElement[bool]:
            return Item.vendor.has(Vendor.name == value)

    with client_for(ItemView) as client:
        assert names(client, vendor_has="acme") == ["Anvil", "apple"]
        with pytest.raises(TypeError) as exc_info:
            client.get("/items/", params={"vendor": "acme"})

    assert str(exc_info.value).startswith(
        "ItemView.filter_vendor: clause references table(s) not in the "
        "statement: list_filter_vendor"
    )


@pytest.mark.parametrize("view_class", [fr.AsyncRestView], indirect=True)
@pytest.mark.parametrize(
    ("result", "message"),
    [
        (True, "returned a bool, not a SQL expression"),
        ("name = 'x'", "must return a SQLAlchemy boolean expression, a clause"),
    ],
)
def test_a_method_must_return_a_condition(view_class, result, message):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.list_filter("popular")
        def filter_popular(self, value: bool) -> Any:
            return result

    with client_for(ItemView) as client, pytest.raises(TypeError, match=message):
        client.get("/items/", params={"popular": "true"})


@pytest.mark.parametrize("view_class", [fr.AsyncRestView], indirect=True)
def test_a_method_may_return_a_boolean_column(view_class):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.list_filter("archived")
        def filter_archived(self, value: bool) -> Any:
            # .where() takes a mapped column as it is
            return Item.archived if value else ~Item.archived

    with client_for(ItemView) as client:
        assert names(client, archived="true") == ["apple", "bread"]
        assert names(client, archived="false") == ["Anvil", "axe"]


def test_only_the_type_of_the_value_is_resolved():
    def filter_popular(self: Any, value: bool) -> Any:
        return None

    # as with 'from __future__ import annotations' and a return type that the
    # module imports under TYPE_CHECKING
    filter_popular.__annotations__ = {"value": "bool", "return": "NotImported"}

    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

    ItemView.filter_popular = fr.list_filter("popular")(filter_popular)
    with client_for(ItemView) as client:
        assert query_parameter(client, "popular")["schema"]["anyOf"][0] == {
            "type": "boolean"
        }


@pytest.mark.parametrize("view_class", [fr.RestView], indirect=True)
def test_a_subclass_that_is_not_registered_runs_its_own_filters(view_class, sync_db):
    class ItemView(fr.RestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

    client_for(ItemView)

    class LiveItemView(ItemView):
        @fr.list_filter("allow_archived")
        def filter_allow_archived(self, value: bool = False) -> Any:
            return None if value else ~Item.archived

    _, make_session = sync_db
    with make_session() as session:
        view = LiveItemView(request=None, session=session)
        stmt = view.apply_list_params(select(Item), {})
        assert sorted(item.name for item in session.scalars(stmt)) == ["Anvil", "axe"]


@pytest.mark.parametrize("view_class", [fr.RestView], indirect=True)
def test_other_list_params_skip_the_filter_methods_with_a_warning(view_class, sync_db):
    class Search(pydantic.BaseModel):
        name: str | None = None

    class ItemView(fr.RestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.list_filter("allow_archived")
        def filter_allow_archived(self, value: bool = False) -> Any:
            return None if value else ~Item.archived

    client_for(ItemView)
    _, make_session = sync_db
    with make_session() as session:
        view = ItemView(request=None, session=session)
        with pytest.warns(
            RestlySkippedFilterMethodsWarning,
            match="ItemView got list params of the class Search, so its filter "
            "method filter_allow_archived does not run.",
        ):
            stmt = view.apply_filters(select(Item), Search())
        assert len(session.scalars(stmt).all()) == 4
        # its own list params run the method, without a warning
        stmt = view.apply_filters(select(Item), ItemView.schema_list_params())
        assert sorted(item.name for item in session.scalars(stmt)) == ["Anvil", "axe"]


@pytest.mark.parametrize("view_class", [fr.AsyncRestView], indirect=True)
def test_a_sort_still_shares_the_join_of_a_filter(view_class):
    class ItemView(PopularView):
        include_filters = ("*", "*.*")
        include_sort_keys = ("*", "*.*")

        @fr.get("/sql")
        async def sql(self, list_params) -> str:
            return str(await self.apply_list_params(select(Item), list_params))

    with client_for(ItemView) as client:
        sql = client.get(
            "/items/sql",
            params={"vendor.name": "acme", "popular": "true", "sort": "vendor.name"},
        ).json()

    assert sql.count("JOIN") == 1
    assert "view_count >" in sql


@pytest.mark.parametrize("view_class", [fr.AsyncRestView], indirect=True)
def test_a_route_drops_a_key_with_none(view_class):
    class ItemView(PopularView):
        @fr.get("/without-popular")
        async def without_popular(self, list_params) -> list[str]:
            others = list_params.model_copy(update={"popular": None})
            stmt = await self.apply_filters(select(Item), others)
            return [item.name for item in await self.session.scalars(stmt)]

    with client_for(ItemView) as client:
        everything = client.get("/items/without-popular", params={"popular": "true"})

    assert everything.json() == ["Anvil", "axe", "apple", "bread"]


def test_the_query_function_rejects_an_own_key():
    fr.include_view(FastAPI(), PopularView)
    params_model = PopularView.schema_list_params

    stmt = apply_list_params(
        select(Item), params_model.model_validate({}), Item, ItemSchema
    )
    assert "WHERE" not in str(stmt)
    with pytest.raises(ValueError) as exc_info:
        apply_list_params(
            select(Item),
            params_model.model_validate({"popular": "true"}),
            Item,
            ItemSchema,
        )

    assert str(exc_info.value) == (
        "apply_list_params cannot apply the key 'popular': it is a filter of "
        "the view's own, from the method filter_popular. Call "
        "apply_list_params on the view instead."
    )


def test_declared_list_params_cannot_hold_the_own_keys():
    class ItemParams(pydantic.BaseModel):
        popular: bool | None = None

    class ItemView(PopularView):
        schema_list_params = ItemParams

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(FastAPI(), ItemView)

    assert str(exc_info.value) == (
        "ItemView has the filter method filter_popular (from PopularView), but "
        "its schema_list_params is written by hand. Restly adds the key "
        "'popular' of a filter method only to list params that it generates. "
        "Leave schema_list_params out to have it generated, or set "
        "filter_popular = None on ItemView to drop it."
    )


def test_a_subclass_with_a_filter_method_gets_new_list_params():
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        schema_list_params = fr.query.derive_schema_list_params(ItemSchema, Item)

    class PopularItemView(ItemView):
        @fr.list_filter("popular")
        def filter_popular(self, value: bool) -> Any:
            return None

    fr.include_view(FastAPI(), PopularItemView)

    assert "popular" in PopularItemView.schema_list_params.model_fields


@pytest.mark.parametrize("base", [fr.AsyncReactAdminView, fr.ReactAdminView])
def test_react_admin_views_reject_the_decorator(base):
    class FilterMixin:
        @fr.list_filter("popular")
        def filter_popular(self, value: bool) -> Any:
            return None

    view = type(
        "ItemAdmin",
        (FilterMixin, base),
        {"prefix": "/items", "model": Item, "schema": ItemSchema},
    )

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(FastAPI(), view)

    assert str(exc_info.value).startswith(
        "ItemAdmin.filter_popular (from FilterMixin) has @fr.list_filter, which "
        "a react-admin view does not use."
    )


def test_a_view_cannot_declare_the_list_params_of_another_view():
    fr.include_view(FastAPI(), PopularView)

    class OtherView(fr.AsyncRestView):
        prefix = "/others"
        model = Item
        schema = ItemSchema
        schema_list_params = PopularView.schema_list_params

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(FastAPI(), OtherView)

    assert str(exc_info.value) == (
        "OtherView.schema_list_params has the keys of filter methods that the "
        "view does not have: popular. Leave schema_list_params out to have it "
        "generated."
    )
