"""``apply_filters``: the client's filters without the sort and the page.

``apply_list_params`` on a view is three steps: ``apply_filters``, the sort,
then the page. On an async view both methods are async.
"""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import ForeignKey, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError
from fastapi_restly.testing import RestlyTestClient


class Base(DeclarativeBase):
    pass


class Vendor(Base):
    __tablename__ = "apply_filters_vendor"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


class Item(Base):
    __tablename__ = "apply_filters_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    category: Mapped[str]
    vendor_id: Mapped[int] = mapped_column(ForeignKey(Vendor.id))
    vendor: Mapped[Vendor] = relationship()


class VendorSchema(fr.IDSchema):
    name: str


class ItemSchema(fr.IDSchema):
    name: str
    category: str
    vendor: VendorSchema


def rows() -> list[Any]:
    return [
        Vendor(id=1, name="acme"),
        Vendor(id=2, name="globex"),
        Item(id=1, name="anvil", category="tools", vendor_id=1),
        Item(id=2, name="axe", category="tools", vendor_id=2),
        Item(id=3, name="apple", category="food", vendor_id=1),
        Item(id=4, name="bread", category="food", vendor_id=2),
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


def counts_view(view_class: type) -> type:
    """A view with a route that counts per category, under the other
    filters."""
    if view_class is fr.AsyncRestView:

        class AsyncItemView(fr.AsyncRestView):
            prefix = "/items"
            model = Item
            schema = ItemSchema
            include_filters = ("**",)
            include_sort_keys = ("**",)

            @fr.get("/category-counts")
            async def category_counts(self, list_params) -> dict[str, int]:
                await self.authorize(fr.Action.GET_MANY)
                stmt = select(Item.category, func.count()).group_by(Item.category)
                stmt = fr.apply_clauses(stmt, fr.resolve_scope(self))
                others = list_params.model_copy(update={"category": None})
                stmt = await self.apply_filters(stmt, others)
                return dict((await self.session.execute(stmt)).all())

            @fr.get("/sql")
            async def sql(self, list_params) -> str:
                stmt = await self.apply_list_params(select(Item), list_params)
                return str(stmt)

        return AsyncItemView

    class SyncItemView(fr.RestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        include_filters = ("**",)
        include_sort_keys = ("**",)

        @fr.get("/category-counts")
        def category_counts(self, list_params) -> dict[str, int]:
            self.authorize(fr.Action.GET_MANY)
            stmt = select(Item.category, func.count()).group_by(Item.category)
            stmt = fr.apply_clauses(stmt, fr.resolve_scope(self))
            others = list_params.model_copy(update={"category": None})
            stmt = self.apply_filters(stmt, others)
            return dict(self.session.execute(stmt).all())

        @fr.get("/sql")
        def sql(self, list_params) -> str:
            return str(self.apply_list_params(select(Item), list_params))

    return SyncItemView


@pytest.fixture
def client(view_class: type) -> Iterator[RestlyTestClient]:
    app = FastAPI()
    fr.include_view(app, counts_view(view_class))
    with RestlyTestClient(app) as client:
        yield client


def test_a_count_route_reads_the_other_filters(client):
    assert client.get("/items/category-counts").json() == {"food": 2, "tools": 2}
    assert client.get(
        "/items/category-counts",
        params={"name__in": "anvil,axe,apple", "category": "food"},
    ).json() == {"food": 1, "tools": 2}
    assert client.get(
        "/items/category-counts", params={"vendor.name": "acme"}
    ).json() == {"food": 1, "tools": 1}


def test_apply_filters_adds_no_sort_and_no_page(client):
    # PostgreSQL rejects ORDER BY id next to GROUP BY category
    counts = client.get(
        "/items/category-counts", params={"sort": "-name", "page": 2, "page_size": 1}
    ).json()

    assert counts == {"food": 2, "tools": 2}


def test_a_filter_and_a_sort_on_one_relationship_share_one_join(client):
    sql = client.get(
        "/items/sql", params={"vendor.name": "acme", "sort": "-vendor.name"}
    ).json()

    assert sql.count("JOIN") == 1
    assert "ORDER BY" in sql


@pytest.mark.parametrize("view_class", [fr.AsyncRestView], indirect=True)
def test_an_override_of_apply_filters_narrows_every_list(view_class):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        include_filters = ("**",)
        include_sort_keys = ("**",)

        async def apply_filters(self, stmt: Any, list_params: Any) -> Any:
            stmt = await super().apply_filters(stmt, list_params)
            return stmt.where(Item.category != "food")

    app = FastAPI()
    fr.include_view(app, ItemView)
    client = RestlyTestClient(app)

    payload = client.get(
        "/items/", params={"vendor.name__in": "acme,globex", "sort": "-vendor.name"}
    ).json()

    assert [row["name"] for row in payload["data"]] == ["axe", "anvil"]
    assert payload["total_count"] == 2


@pytest.mark.parametrize("view_class", [fr.AsyncRestView], indirect=True)
def test_raw_query_params_go_through_the_list_params(view_class):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.get("/raw")
        async def raw(self) -> list[str]:
            stmt = await self.apply_list_params(select(Item), self.request.query_params)
            return [item.name for item in (await self.session.scalars(stmt))]

    app = FastAPI()
    fr.include_view(app, ItemView)
    client = RestlyTestClient(app)

    names = client.get("/items/raw", params={"name__in": "axe,bread"}).json()
    assert names == ["axe", "bread"]
    detail = client.get(
        "/items/raw", params={"page_size": 5000}, assert_status_code=422
    ).json()["detail"]
    assert detail[0]["loc"] == ["query", "page_size"]
    detail = client.get(
        "/items/raw", params={"vendor.name": "acme"}, assert_status_code=422
    ).json()["detail"]
    assert detail == [
        {
            "type": "extra_forbidden",
            "loc": ["query", "vendor.name"],
            "msg": "Unknown query parameter 'vendor.name'",
            "input": "acme",
        }
    ]


@pytest.mark.parametrize("view_class", [fr.AsyncRestView], indirect=True)
def test_a_mapping_is_validated_like_query_params(view_class):
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.get("/mapping")
        async def mapping(self, nope: bool = False) -> list[str]:
            params = {"name": "axe", **({"nope": "1"} if nope else {})}
            stmt = await self.apply_filters(select(Item), params)
            return [item.name for item in (await self.session.scalars(stmt))]

    app = FastAPI()
    fr.include_view(app, ItemView)
    client = RestlyTestClient(app)

    assert client.get("/items/mapping").json() == ["axe"]
    detail = client.get(
        "/items/mapping", params={"nope": True}, assert_status_code=422
    ).json()["detail"]
    assert detail[0]["msg"] == "Unknown query parameter 'nope'"


@pytest.mark.parametrize("name", ["apply_filters", "apply_list_params"])
def test_an_async_view_needs_async_list_methods(name):
    def method(self: Any, stmt: Any, list_params: Any) -> Any:
        return stmt

    with pytest.raises(RestlyConfigurationError) as exc_info:
        type("ItemView", (fr.AsyncRestView,), {name: method})

    assert str(exc_info.value) == (
        f"ItemView.{name} must be 'async def' on an async view, which awaits "
        f"it. Write 'async def {name}', and 'await super().{name}(...)'."
    )


def test_a_mixin_with_a_sync_list_method_names_the_mixin():
    class FilterMixin:
        def apply_filters(self, stmt: Any, list_params: Any) -> Any:
            return stmt

    with pytest.raises(RestlyConfigurationError, match=r"\(from FilterMixin\)"):

        class ItemView(FilterMixin, fr.AsyncRestView):
            pass


def test_a_sync_view_needs_sync_list_methods():
    with pytest.raises(RestlyConfigurationError) as exc_info:

        class ItemView(fr.RestView):
            async def apply_filters(self, stmt: Any, list_params: Any) -> Any:
                return stmt

    assert str(exc_info.value) == (
        "ItemView.apply_filters is 'async def', but a sync view calls it "
        "without await. Write 'def apply_filters', or use fr.AsyncRestView."
    )


@pytest.mark.parametrize("view_class", [fr.AsyncRestView], indirect=True)
def test_a_react_admin_count_route_reads_its_filter(view_class):
    class ItemAdmin(fr.AsyncReactAdminView):
        prefix = "/items"
        model = Item
        schema = ItemSchema

        @fr.get("/category-counts")
        async def category_counts(self) -> dict[str, int]:
            await self.authorize(fr.Action.GET_MANY)
            stmt = select(Item.category, func.count()).group_by(Item.category)
            stmt = fr.apply_clauses(stmt, fr.resolve_scope(self))
            stmt = await self.apply_filters(stmt, self._parse_react_admin_params())
            return dict((await self.session.execute(stmt)).all())

    app = FastAPI()
    fr.include_view(app, ItemAdmin)
    client = RestlyTestClient(app)

    counts = client.get(
        "/items/category-counts", params={"filter": '{"id": [1, 3]}'}
    ).json()
    assert counts == {"food": 1, "tools": 1}
