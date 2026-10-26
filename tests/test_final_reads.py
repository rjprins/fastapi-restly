"""The reads are final loads.

``get_one`` and ``get_many`` start from ``self.select()``: ``select(model)``
with the scope and the loader options. A view shapes them with declarations
and with two hooks: ``where_id(id)`` turns an id into the WHERE, and
``not_found(id)`` is the error for an id that names no row. A view that
defines ``get_one``, ``get_many`` or ``select`` fails when Python creates the
class.

A custom route that declares the view's response answers through the view's
response step, also when it returns the row.
"""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import ForeignKey, event, func
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.db._config import _fr_config
from fastapi_restly.testing import RestlyTestClient
from fastapi_restly.views import ViewListResponse, ViewResponse

from ._models import SharedBase
from .conftest import create_tables


@pytest.fixture
def sync_client(sync_db) -> Iterator[RestlyTestClient]:
    yield RestlyTestClient(FastAPI())


def _models():
    class Shelf(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Book(fr.IDMixin, SharedBase):
        title: Mapped[str]
        slug: Mapped[str] = mapped_column(unique=True)
        hidden: Mapped[bool] = mapped_column(default=False)
        shelf_id: Mapped[int | None] = mapped_column(
            ForeignKey("shelf.id"), default=None
        )
        shelf: Mapped[Shelf | None] = relationship(init=False)

    class ShelfSchema(fr.IDSchema):
        name: str

    class BookSchema(fr.IDSchema):
        title: str
        slug: str
        shelf_id: int | None = None
        shelf: fr.ReadOnly[ShelfSchema | None] = None

    return Shelf, Book, ShelfSchema, BookSchema


def _seed(client: RestlyTestClient, prefix: str = "/books") -> None:
    client.post(f"{prefix}/", json={"title": "Dune", "slug": "dune"})
    client.post(f"{prefix}/", json={"title": "Emma", "slug": "emma"})


def _run_async(statement: Any) -> Any:
    """Run a statement outside the app, commit it, and return its result."""

    async def run() -> Any:
        async with _fr_config.async_make_session() as session:
            result = await session.execute(statement)
            await session.commit()
            return result

    return asyncio.run(run())


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base", [fr.RestView, fr.AsyncRestView])
@pytest.mark.parametrize("name", ["get_one", "get_many", "select"])
def test_a_view_that_defines_a_load_fails_when_python_creates_it(base, name):
    def method(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("never called")

    with pytest.raises(
        fr.exc.RestlyConfigurationError,
        match=f"BookView defines {name}, which is final",
    ):
        type("BookView", (base,), {name: method})


def test_the_guard_names_the_replacements():
    class LegacyReads:
        async def get_one(self, id: Any, *, scope: Any = None) -> Any:
            raise AssertionError("never called")

    with pytest.raises(fr.exc.RestlyConfigurationError) as raised:

        class BookView(LegacyReads, fr.AsyncRestView):
            pass

    message = str(raised.value)
    assert "BookView defines get_one (from LegacyReads), which is final" in message
    for replacement in (
        "where_id(id)",
        "not_found(id)",
        "get_loader_options",
        "to_single_response",
        "get_one_endpoint",
    ):
        assert replacement in message


def test_the_list_guard_names_the_list_replacements():
    with pytest.raises(fr.exc.RestlyConfigurationError) as raised:

        class BookView(fr.RestView):
            def get_many(self, list_params: Any, *, scope: Any = None) -> Any:
                raise AssertionError("never called")

    message = str(raised.value)
    for replacement in (
        "apply_list_params",
        "fr.list_filter",
        "get_loader_options",
        "to_single_response",
        "get_many_endpoint",
    ):
        assert replacement in message


@pytest.mark.parametrize("name", ["where_id", "not_found"])
def test_a_hook_name_that_holds_a_value_fails_when_python_creates_the_view(name):
    """A text such as ``not_found = "food not found"`` would otherwise fail
    only at the first 404."""
    with pytest.raises(
        fr.exc.RestlyConfigurationError,
        match=f"FoodView.{name} is a str, but the loads call {name}",
    ):
        type("FoodView", (fr.RestView,), {name: "food not found"})


def test_a_classmethod_or_staticmethod_hook_is_accepted(sync_client):
    """The loads call ``self.where_id(id)``, and that works for a
    ``classmethod`` and a ``staticmethod`` too."""
    Shelf, Book, ShelfSchema, BookSchema = _models()

    @fr.include_view(sync_client.app)
    class BookView(fr.RestView):
        prefix = "/books"
        model = Book
        schema = BookSchema
        id_type = str

        @classmethod
        def where_id(cls, id: str) -> sa.ColumnElement[bool]:
            return Book.slug == id

        @staticmethod
        def not_found(id: str | None) -> Exception:
            return HTTPException(410, f"{id} is gone")

    SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])
    _seed(sync_client)

    assert sync_client.get("/books/emma").json()["title"] == "Emma"
    gone = sync_client.get("/books/nope", assert_status_code=410)
    assert gone.json()["detail"] == "nope is gone"


def test_the_old_name_of_the_loader_options_is_refused():
    """``get_relationship_loader_options`` is now ``get_loader_options``. A
    view that still defines the old name would load nothing extra, so the
    class fails when Python creates it."""
    Shelf, Book, ShelfSchema, BookSchema = _models()

    with pytest.raises(fr.exc.RestlyConfigurationError) as raised:

        class BookView(fr.AsyncRestView):
            model = Book
            schema = BookSchema

            def get_relationship_loader_options(self) -> list[Any]:
                return []

    message = str(raised.value)
    assert "BookView defines get_relationship_loader_options" in message
    assert "renamed to get_loader_options" in message


# ---------------------------------------------------------------------------
# where_id: one hook for every load by id
# ---------------------------------------------------------------------------


def test_where_id_serves_every_item_route(client):
    Shelf, Book, ShelfSchema, BookSchema = _models()
    asked: list[Any] = []

    @fr.include_view(client.app)
    class BookView(fr.AsyncRestView):
        prefix = "/books"
        model = Book
        schema = BookSchema
        id_type = str

        class Action(fr.Action):
            HIDE = "hide"

        def where_id(self, id: str) -> sa.ColumnElement[bool]:
            asked.append(id)
            if id.isdigit():
                return Book.id == int(id)
            return Book.slug == id

        @fr.post("/{id}/hide", status_code=200)
        async def hide(self, id: str) -> Any:
            book = await self.get_one(id)
            async with self.write_action("hide", obj=book):
                book.hidden = True
            return {"hidden": book.id}

        @fr.get("/by-title/{title}")
        async def by_title(self, title: str) -> Any:
            return self.to_response(await self.handle_get_one(Book.title == title))

    create_tables()
    _seed(client)

    assert client.get("/books/dune").json()["title"] == "Dune"
    assert client.get("/books/1").json()["slug"] == "dune"
    changed = client.patch("/books/emma", json={"title": "Emma!"}).json()
    assert changed == {
        "id": 2,
        "title": "Emma!",
        "slug": "emma",
        "shelf_id": None,
        "shelf": None,
    }
    assert client.post("/books/dune/hide").json() == {"hidden": 1}
    client.delete("/books/emma", assert_status_code=204)
    client.get("/books/emma", assert_status_code=404)
    assert asked == ["dune", "1", "emma", "dune", "emma", "emma"]

    # an expression in place of the id does not come to where_id
    asked.clear()
    client.post("/books/", json={"title": "Odyssey", "slug": "odyssey"})
    assert client.get("/books/by-title/Odyssey").json()["slug"] == "odyssey"
    assert asked == []


def test_sync_where_id_serves_every_item_route(sync_client):
    Shelf, Book, ShelfSchema, BookSchema = _models()

    @fr.include_view(sync_client.app)
    class BookView(fr.RestView):
        prefix = "/books"
        model = Book
        schema = BookSchema
        id_type = str

        def where_id(self, id: str) -> sa.ColumnElement[bool]:
            return Book.id == int(id) if id.isdigit() else Book.slug == id

    SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])
    _seed(sync_client)

    assert sync_client.get("/books/dune").json()["title"] == "Dune"
    sync_client.patch("/books/emma", json={"title": "Emma!"})
    assert sync_client.get("/books/2").json()["title"] == "Emma!"
    sync_client.delete("/books/dune", assert_status_code=204)
    sync_client.get("/books/1", assert_status_code=404)


def test_where_id_must_return_an_expression(client):
    Shelf, Book, ShelfSchema, BookSchema = _models()

    @fr.include_view(client.app)
    class BookView(fr.AsyncRestView):
        prefix = "/books"
        model = Book
        schema = BookSchema

        def where_id(self, id: int) -> Any:
            return id == 1  # a bool, not SQL

    create_tables()
    with pytest.raises(TypeError, match="where_id must return a SQL condition"):
        client.get("/books/1")


def test_a_composite_key_gets_the_item_routes_through_where_id(client):
    class Seat(SharedBase):
        row: Mapped[int] = mapped_column(primary_key=True)
        number: Mapped[int] = mapped_column(primary_key=True)
        guest: Mapped[str] = mapped_column(default="")

    class SeatSchema(fr.BaseSchema):
        row: int
        number: int
        guest: str = ""

    @fr.include_view(client.app)
    class SeatView(fr.AsyncRestView):
        prefix = "/seats"
        model = Seat
        schema = SeatSchema
        id_type = str  # the path is "row-number", such as "3-7"

        def where_id(self, id: str) -> sa.ColumnElement[bool]:
            row, number = id.split("-")
            return sa.and_(Seat.row == int(row), Seat.number == int(number))

    create_tables()
    client.post("/seats/", json={"row": 3, "number": 7})

    assert client.get("/seats/3-7").json() == {"row": 3, "number": 7, "guest": ""}
    assert client.patch("/seats/3-7", json={"guest": "Ada"}).json()["guest"] == "Ada"
    client.delete("/seats/3-7", assert_status_code=204)
    client.get("/seats/3-7", assert_status_code=404)


@pytest.mark.parametrize("base", [fr.RestView, fr.AsyncRestView])
def test_a_composite_key_without_where_id_fails_at_registration(base):
    """An id from the path cannot name one row of a composite key. The view
    fails when it registers, not at the first request to an item route."""

    class Seat(SharedBase):
        row: Mapped[int] = mapped_column(primary_key=True)
        number: Mapped[int] = mapped_column(primary_key=True)

    class SeatSchema(fr.BaseSchema):
        row: int
        number: int

    class SeatView(base):
        prefix = "/seats"
        model = Seat
        schema = SeatSchema

    with pytest.raises(fr.exc.RestlyConfigurationError) as raised:
        fr.include_view(FastAPI(), SeatView)

    message = str(raised.value)
    assert "Seat has a composite primary key" in message
    assert "get_one_endpoint, update_endpoint, delete_endpoint" in message
    assert "Set id_type = str, and override where_id(id)" in message
    assert "leave the item routes out with include_routes or exclude_routes" in message

    # without the item routes, the view registers
    class ListOnlySeatView(base):
        prefix = "/seats"
        model = Seat
        schema = SeatSchema
        exclude_routes = (
            fr.ViewRoute.GET_ONE,
            fr.ViewRoute.UPDATE,
            fr.ViewRoute.DELETE,
        )

    fr.include_view(FastAPI(), ListOnlySeatView)


# ---------------------------------------------------------------------------
# not_found: one hook for the 404 of every item route
# ---------------------------------------------------------------------------


def test_not_found_is_the_error_of_every_item_route(client):
    Shelf, Book, ShelfSchema, BookSchema = _models()
    asked: list[Any] = []

    @fr.include_view(client.app)
    class BookView(fr.AsyncRestView):
        prefix = "/books"
        model = Book
        schema = BookSchema
        scope = fr.where_clause(Book.hidden.is_(False))

        def not_found(self, id: Any) -> Exception:
            asked.append(id)
            return fr.exc.NotFound("book not found")

    create_tables()
    _seed(client)
    client.post("/books/", json={"title": "Secret", "slug": "secret"})
    _run_async(sa.update(Book).where(Book.id == 3).values(hidden=True))

    responses = [
        client.get("/books/99", assert_status_code=404),
        client.patch("/books/99", json={"title": "x"}, assert_status_code=404),
        client.delete("/books/99", assert_status_code=404),
        # outside the scope: the same 404
        client.get("/books/3", assert_status_code=404),
    ]
    assert {str(r.json()) for r in responses} == {"{'detail': 'book not found'}"}
    assert asked == [99, 99, 99, 3]


# ---------------------------------------------------------------------------
# select(): where every read starts
# ---------------------------------------------------------------------------


def test_select_has_the_scope_and_the_loads_of_the_reads(client):
    Shelf, Book, ShelfSchema, BookSchema = _models()

    @fr.include_view(client.app)
    class BookView(fr.AsyncRestView):
        prefix = "/books"
        model = Book
        schema = BookSchema
        scope = fr.where_clause(Book.hidden.is_(False))

        @fr.get("/on-shelves")
        async def on_shelves(self) -> list[BookSchema]:
            await self.authorize(fr.Action.GET_MANY)
            stmt = self.select().where(Book.shelf_id.is_not(None))
            # the shelf is loaded: serializing it needs no IO on async
            books = (await self.session.scalars(stmt)).all()
            return [self.to_single_response(book) for book in books]

        @fr.get("/all-titles")
        async def all_titles(self) -> list[str]:
            stmt = self.select(scope=fr.clauses.UNSCOPED).with_only_columns(Book.title)
            return list((await self.session.scalars(stmt.order_by(Book.id))).all())

        @fr.get("/total")
        async def total(self, list_params: Any) -> int:
            stmt = await self.apply_filters(self.select(), list_params)
            return await self.count(stmt)

    create_tables()
    shelf_id = int(
        _run_async(
            sa.text("INSERT INTO shelf (name) VALUES ('fiction') RETURNING id")
        ).scalar_one()
    )
    client.post("/books/", json={"title": "Dune", "slug": "dune", "shelf_id": shelf_id})
    client.post("/books/", json={"title": "Emma", "slug": "emma", "shelf_id": shelf_id})
    client.post("/books/", json={"title": "Loose", "slug": "loose"})
    _run_async(sa.update(Book).where(Book.id == 2).values(hidden=True))

    on_shelves = client.get("/books/on-shelves").json()
    assert [b["title"] for b in on_shelves] == ["Dune"]
    assert on_shelves[0]["shelf"] == {"id": shelf_id, "name": "fiction"}
    # the loader options do not get in the way of a select of some columns
    assert client.get("/books/all-titles").json() == ["Dune", "Emma", "Loose"]
    # nor of a count
    assert client.get("/books/total").json() == 2
    assert client.get("/books/total", params={"title": "Loose"}).json() == 1


@pytest.fixture
def sync_session_view(sync_db):
    engine, make_session = sync_db
    Shelf, Book, ShelfSchema, BookSchema = _models()

    class BookView(fr.RestView):
        prefix = "/books"
        model = Book
        schema = BookSchema
        scope = fr.where_clause(Book.hidden.is_(False))

    SharedBase.metadata.create_all(engine)
    with make_session() as session:
        session.add_all(
            [
                Book(title="Dune", slug="dune"),
                Book(title="Emma", slug="emma"),
                Book(title="Secret", slug="secret", hidden=True),
            ]
        )
        session.flush()
        view = BookView()
        view.session = session
        yield view, Book


def test_get_many_takes_where_itself(sync_session_view):
    view, Book = sync_session_view
    rows = view.get_many({}, where=Book.slug == "emma").objects
    assert [row.title for row in rows] == ["Emma"]
    # where= narrows inside the scope, and a scope replaces the view's
    rows = view.get_many({}, scope=fr.clauses.UNSCOPED, where=Book.slug != "x")
    assert sorted(row.title for row in rows.objects) == ["Dune", "Emma", "Secret"]
    assert rows.total_count == 3


# ---------------------------------------------------------------------------
# A value on each row: the response, and SQL that loads with the row
# ---------------------------------------------------------------------------


def _count_statements(engine: Any) -> list[str]:
    seen: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def record(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    return seen


def test_a_computed_value_loads_with_the_rows_and_after_a_write(client):
    """The SQL way to add a value to each row: a ``column_property``. It
    loads in the same SELECT on every read, and the save after a write
    refreshes it. A value without IO, such as one that depends on the caller,
    goes in ``to_single_response``, which every response passes."""
    Shelf, Book, ShelfSchema, BookSchema = _models()
    Shelf.book_count = column_property(
        sa.select(func.count(Book.id))
        .where(Book.shelf_id == Shelf.id)
        .correlate_except(Book)
        .scalar_subquery()
    )

    class CountedShelfSchema(fr.IDSchema):
        name: str
        book_count: fr.ReadOnly[int] = 0
        mine: fr.ReadOnly[bool] = False

    @fr.include_view(client.app)
    class ShelfView(fr.AsyncRestView):
        prefix = "/shelves"
        model = Shelf
        schema = CountedShelfSchema

        def to_single_response(self, obj: Any) -> Any:
            response = super().to_single_response(obj)
            return response.model_copy(update={"mine": obj.name.startswith("my")})

    @fr.include_view(client.app)
    class BookView(fr.AsyncRestView):
        prefix = "/books"
        model = Book
        schema = BookSchema

    create_tables()
    for name in ("my fiction", "poetry", "my history"):
        shelf = client.post("/shelves/", json={"name": name}).json()
        assert shelf["book_count"] == 0
    for n in range(3):
        client.post("/books/", json={"title": f"b{n}", "slug": f"b{n}", "shelf_id": 1})

    seen = _count_statements(_fr_config.async_make_session.kw["bind"].sync_engine)
    page = client.get("/shelves/").json()["data"]
    assert [(s["book_count"], s["mine"]) for s in page] == [
        (3, True),
        (0, False),
        (0, True),
    ]
    assert len(seen) == 2  # the count and the page, for any page size

    assert client.get("/shelves/1").json()["book_count"] == 3
    renamed = client.patch("/shelves/1", json={"name": "fiction"}).json()
    assert (renamed["book_count"], renamed["mine"]) == (3, False)


# ---------------------------------------------------------------------------
# A custom route that declares the view's response answers through it
# ---------------------------------------------------------------------------


def _marked_book_view(client: RestlyTestClient, Book: Any, BookSchema: Any) -> Any:
    """A view whose ``to_single_response`` marks each row, so a response
    shows whether it passed the view's response step."""

    class BookView(fr.AsyncRestView):
        prefix = "/books"
        model = Book
        schema = BookSchema

        def to_single_response(self, obj: Any) -> Any:
            response = super().to_single_response(obj)
            return response.model_copy(update={"title": obj.title.upper()})

    return BookView


def test_a_route_that_declares_view_response_returns_the_row(client):
    """``-> ViewResponse``: the row goes through ``to_response``. Anything
    else, such as a response object, goes out as it is."""
    Shelf, Book, ShelfSchema, BookSchema = _models()

    class BookView(_marked_book_view(client, Book, BookSchema)):
        class Action(fr.Action):
            TOUCH = "touch"

        @fr.get("/{id}/plain")
        async def plain(self, id: int) -> ViewResponse:
            return await self.get_one(id)

        @fr.post("/{id}/touch", status_code=200)
        async def touch(self, id: int) -> ViewResponse:
            book = await self.get_one(id)
            async with self.write_action("touch", obj=book):
                book.slug = book.slug + "!"
            return book

        @fr.get("/{id}/teapot")
        async def teapot(self, id: int) -> ViewResponse:
            return JSONResponse({"teapot": id}, status_code=418)

    fr.include_view(client.app, BookView)
    create_tables()
    _seed(client)

    assert client.get("/books/1/plain").json()["title"] == "DUNE"
    touched = client.post("/books/2/touch").json()
    assert (touched["title"], touched["slug"]) == ("EMMA", "emma!")
    teapot = client.get("/books/1/teapot", assert_status_code=418)
    assert teapot.json() == {"teapot": 1}


def test_a_route_that_declares_a_list_of_view_response_returns_rows(sync_client):
    """``-> list[ViewResponse]``: each row goes through
    ``to_single_response``. ``response_model=`` declares the same."""
    Shelf, Book, ShelfSchema, BookSchema = _models()

    @fr.include_view(sync_client.app)
    class BookView(fr.RestView):
        prefix = "/books"
        model = Book
        schema = BookSchema

        def to_single_response(self, obj: Any) -> Any:
            response = super().to_single_response(obj)
            return response.model_copy(update={"title": obj.title.upper()})

        @fr.get("/all")
        def all_books(self) -> list[ViewResponse]:
            return list(self.session.scalars(self.select().order_by(Book.id)))

        @fr.get("/first-two", response_model=list[ViewResponse])
        def first_two(self) -> Any:
            stmt = self.select().order_by(Book.id).limit(2)
            return tuple(self.session.scalars(stmt))

    SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])
    _seed(sync_client)
    sync_client.post("/books/", json={"title": "Odyssey", "slug": "odyssey"})

    titles = [b["title"] for b in sync_client.get("/books/all").json()]
    assert titles == ["DUNE", "EMMA", "ODYSSEY"]
    titles = [b["title"] for b in sync_client.get("/books/first-two").json()]
    assert titles == ["DUNE", "EMMA"]


def test_a_route_that_declares_view_list_response_returns_the_list_result(client):
    """``-> ViewListResponse``: a ``ListResult`` goes through
    ``to_response(result, ResponseShape.LIST)``."""
    Shelf, Book, ShelfSchema, BookSchema = _models()

    class BookView(_marked_book_view(client, Book, BookSchema)):
        @fr.get("/starting/{letter}")
        async def starting(self, letter: str, list_params) -> ViewListResponse:
            return await self.handle_get_many(
                list_params, where=Book.title.startswith(letter)
            )

    fr.include_view(client.app, BookView)
    create_tables()
    _seed(client)

    page = client.get("/books/starting/E").json()
    assert [b["title"] for b in page["data"]] == ["EMMA"]
    assert page["total_count"] == 1


def test_a_route_that_returns_the_model_gets_the_view_response(client):
    """``-> Book`` and ``-> list[Book]`` for the view's own model: the view
    sets its response class as the response model, and each row goes
    through ``to_single_response``. The view's response is set on the class,
    so the model alone means that response."""
    Shelf, Book, ShelfSchema, BookSchema = _models()

    class BookView(_marked_book_view(client, Book, BookSchema)):
        @fr.get("/{id}/row")
        async def row(self, id: int) -> Book:
            return await self.get_one(id)

        @fr.get("/rows")
        async def rows(self) -> list[Book]:
            return list(await self.session.scalars(self.select().order_by(Book.id)))

    fr.include_view(client.app, BookView)
    create_tables()
    _seed(client)

    assert client.get("/books/1/row").json()["title"] == "DUNE"
    assert [b["title"] for b in client.get("/books/rows").json()] == ["DUNE", "EMMA"]

    paths = client.app.openapi()["paths"]
    response_name = BookView.schema_response.__name__

    def body(path: str) -> Any:
        content = paths[path]["get"]["responses"]["200"]["content"]
        return content["application/json"]["schema"]

    assert body("/books/{id}/row")["$ref"].endswith(f"/{response_name}")
    assert body("/books/rows")["items"]["$ref"].endswith(f"/{response_name}")


def test_a_route_may_return_the_result_of_scalars_or_none(client):
    """``list[ViewResponse]`` takes any iterable of rows, such as the result
    of ``session.scalars()``. ``| None`` keeps the wrapping, and ``None``
    goes out as ``null``."""
    Shelf, Book, ShelfSchema, BookSchema = _models()

    class BookView(_marked_book_view(client, Book, BookSchema)):
        @fr.get("/scalars")
        async def scalars(self) -> list[ViewResponse]:
            return await self.session.scalars(self.select().order_by(Book.id))

        @fr.get("/by-slug/{slug}")
        async def by_slug(self, slug: str) -> ViewResponse | None:
            stmt = self.select().where(Book.slug == slug)
            return (await self.session.scalars(stmt)).one_or_none()

        @fr.get("/{id}/maybe")
        async def maybe(self, id: int) -> Book | None:
            return await self.get_one(id)

    fr.include_view(client.app, BookView)
    create_tables()
    _seed(client)

    titles = [b["title"] for b in client.get("/books/scalars").json()]
    assert titles == ["DUNE", "EMMA"]
    assert client.get("/books/by-slug/emma").json()["title"] == "EMMA"
    assert client.get("/books/by-slug/nope").json() is None
    assert client.get("/books/1/maybe").json()["title"] == "DUNE"


def test_a_route_that_names_the_views_own_response_class(client):
    """The 0.11 form ``response_model=fr.schemas.derive_schema_response(...)``
    names the view's own response class: each call returns that same class.
    So the route declares the view's response, as with ``ViewResponse``, and
    ``to_single_response`` runs. The list response class works the same."""
    Shelf, Book, ShelfSchema, BookSchema = _models()
    BookResponse = fr.schemas.derive_schema_response(BookSchema)
    # the view keeps the default pagination, so the default envelope
    BookListResponse = fr.schemas.derive_schema_list_response(BookResponse)

    class BookView(_marked_book_view(client, Book, BookSchema)):
        @fr.get("/{id}/named", response_model=BookResponse)
        async def named(self, id: int) -> Any:
            return await self.get_one(id)

        @fr.get("/named-list", response_model=list[BookResponse])
        async def named_list(self) -> Any:
            return await self.session.scalars(self.select().order_by(Book.id))

        @fr.get("/named-page", response_model=BookListResponse)
        async def named_page(self, list_params) -> Any:
            return await self.handle_get_many(list_params)

    fr.include_view(client.app, BookView)
    create_tables()
    _seed(client)

    assert client.get("/books/1/named").json()["title"] == "DUNE"
    titles = [b["title"] for b in client.get("/books/named-list").json()]
    assert titles == ["DUNE", "EMMA"]
    page = client.get("/books/named-page").json()
    assert [b["title"] for b in page["data"]] == ["DUNE", "EMMA"]


def test_a_route_that_names_its_own_class_keeps_fastapi_behaviour(client):
    """A Pydantic class that the route names itself is the route's choice:
    FastAPI validates the row against it, and ``to_single_response`` does
    not run."""
    Shelf, Book, ShelfSchema, BookSchema = _models()

    class Title(fr.BaseSchema):
        title: str

    class BookView(_marked_book_view(client, Book, BookSchema)):
        @fr.get("/{id}/title")
        async def title(self, id: int) -> Title:
            return await self.get_one(id)  # type: ignore[return-value]

        @fr.get("/{id}/schema", response_model=BookSchema)
        async def as_schema(self, id: int) -> Any:
            return await self.get_one(id)

    fr.include_view(client.app, BookView)
    create_tables()
    _seed(client)

    assert client.get("/books/1/title").json() == {"title": "Dune"}
    assert client.get("/books/1/schema").json()["title"] == "Dune"


def test_a_string_annotation_declares_the_view_response(client, monkeypatch):
    """Under ``from __future__ import annotations`` every annotation is a
    string. The strings here are those strings: Restly evaluates them in the
    module of the route, so a marker and the view's model work as they do
    without the import."""
    Shelf, Book, ShelfSchema, BookSchema = _models()
    # a model of an app is a name of its module, as the strings need
    monkeypatch.setitem(globals(), "Book", Book)

    class BookView(_marked_book_view(client, Book, BookSchema)):
        @fr.get("/{id}/plain")
        async def plain(self, id: "int") -> "ViewResponse":
            return await self.get_one(id)

        @fr.get("/all")
        async def all_books(self) -> "list[ViewResponse]":
            return list(await self.session.scalars(self.select().order_by(Book.id)))

        @fr.get("/page")
        async def page(self, list_params) -> "ViewListResponse":
            return await self.handle_get_many(list_params)

        @fr.get("/{id}/row")
        async def row(self, id: "int") -> "Book":
            return await self.get_one(id)

    fr.include_view(client.app, BookView)
    create_tables()
    _seed(client)

    assert client.get("/books/1/plain").json()["title"] == "DUNE"
    assert [b["title"] for b in client.get("/books/all").json()] == ["DUNE", "EMMA"]
    assert [b["title"] for b in client.get("/books/page").json()["data"]] == [
        "DUNE",
        "EMMA",
    ]
    assert client.get("/books/2/row").json()["title"] == "EMMA"
