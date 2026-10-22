"""Edge cases of the final reads.

Each test checks one claim of the design against a case that could break
it: react-admin lists, nested views, hooks that return the wrong thing, the
count over primary keys, and values that load with the rows after a write.
Most tests run on ``RestView`` and on ``AsyncRestView``.
"""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, HTTPException
from sqlalchemy import ForeignKey, event, func
from sqlalchemy.orm import (
    Mapped,
    column_property,
    joinedload,
    mapped_column,
    query_expression,
    relationship,
    selectinload,
    with_expression,
)

import fastapi_restly as fr
from fastapi_restly.db._globals import _fr_config
from fastapi_restly.testing import RestlyTestClient

from ._models import SharedBase
from .conftest import create_tables

# ---------------------------------------------------------------------------
# Fixtures: one test body for sync and async
# ---------------------------------------------------------------------------


class Flavor:
    def __init__(self, name: str, client: RestlyTestClient, engine: Any) -> None:
        self.name = name
        self.client = client
        self.engine = engine
        self.base: Any = fr.RestView if name == "sync" else fr.AsyncRestView
        self.react_admin: Any = (
            fr.views.ReactAdminView if name == "sync" else fr.views.AsyncReactAdminView
        )

    def create_tables(self) -> None:
        if self.name == "sync":
            SharedBase.metadata.create_all(self.engine)
        else:
            create_tables()

    def run(self, statement: Any) -> None:
        """Run a write outside the app and commit it."""
        if self.name == "sync":
            with _fr_config.make_session() as session:
                session.execute(statement)
                session.commit()
            return

        async def go() -> None:
            async with _fr_config.async_make_session() as session:
                await session.execute(statement)
                await session.commit()

        asyncio.run(go())

    def selects(self) -> list[str]:
        seen: list[str] = []

        @event.listens_for(self.engine, "before_cursor_execute")
        def record(conn, cursor, statement, params, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                seen.append(" ".join(statement.split()))

        return seen


@pytest.fixture(params=["sync", "async"])
def flavor(request) -> Iterator[Flavor]:
    if request.param == "sync":
        engine, _ = request.getfixturevalue("sync_db")
        yield Flavor("sync", RestlyTestClient(FastAPI()), engine)
    else:
        client = request.getfixturevalue("client")
        engine = _fr_config.async_make_session.kw["bind"].sync_engine
        yield Flavor("async", client, engine)


def _shelves_and_books():
    class Shelf(fr.IDMixin, SharedBase):
        name: Mapped[str]
        books: Mapped[list["Book"]] = relationship(
            back_populates="shelf", default_factory=list, order_by="Book.id"
        )

    class Book(fr.IDMixin, SharedBase):
        title: Mapped[str]
        shelf_id: Mapped[int | None] = mapped_column(
            ForeignKey("shelf.id"), default=None
        )
        shelf: Mapped[Shelf | None] = relationship(
            back_populates="books", default=None, init=False
        )

    # configure now: a failed test keeps its frame, and with it these
    # classes, alive; an unconfigured mapper would then break the next test
    sa.orm.configure_mappers()
    return Shelf, Book


def _seed_shelves(flavor: Flavor, Shelf: Any, Book: Any, books_per_shelf: int = 3):
    for n in range(5):
        flavor.run(sa.insert(Shelf).values(id=n + 1, name=f"s{n + 1}"))
        for b in range(books_per_shelf):
            flavor.run(sa.insert(Book).values(title=f"s{n + 1}b{b}", shelf_id=n + 1))


# ---------------------------------------------------------------------------
# React-admin views on the final loads
# ---------------------------------------------------------------------------


def test_react_admin_list_with_a_joined_collection_and_a_range(flavor):
    """The final get_many with the react-admin apply_list_params, a
    joinedload of a collection, a sort and a range: the page has whole rows,
    each with all its children, and Content-Range has the total."""
    Shelf, Book = _shelves_and_books()

    class BookRef(fr.IDSchema):
        title: str

    class ShelfSchema(fr.IDSchema):
        name: str
        books: fr.ReadOnly[list[BookRef]] = []

    @fr.include_view(flavor.client.app)
    class ShelfView(flavor.react_admin):
        prefix = "/shelves"
        model = Shelf
        schema = ShelfSchema

        def get_loader_options(self) -> list[Any]:
            return [joinedload(Shelf.books)]

    flavor.create_tables()
    _seed_shelves(flavor, Shelf, Book)

    response = flavor.client.get(
        "/shelves/", params={"sort": '["name","DESC"]', "range": "[1,2]"}
    )
    rows = response.json()
    assert [row["name"] for row in rows] == ["s4", "s3"]
    assert [len(row["books"]) for row in rows] == [3, 3]
    assert response.headers["content-range"] == "items 1-2/5"

    # getMany: the id array
    rows = flavor.client.get("/shelves/", params={"filter": '{"id":[2,4]}'}).json()
    assert [row["name"] for row in rows] == ["s2", "s4"]


def test_react_admin_search_override_from_the_docs(client):
    """docs/react_admin.md: apply_filters takes "q" out and passes the rest
    on. Under F it is a seam of the final get_many, and the count sees it."""
    Shelf, Book = _shelves_and_books()

    class BookSchema(fr.IDSchema):
        title: str

    @fr.include_view(client.app)
    class BookView(fr.views.AsyncReactAdminView):
        prefix = "/books"
        model = Book
        schema = BookSchema

        async def apply_filters(self, stmt, list_params):
            filters = dict(list_params.filter or {})
            q = filters.pop("q", None)
            rest = list_params.model_copy(update={"filter": filters})
            stmt = await super().apply_filters(stmt, rest)
            if q:
                stmt = stmt.where(Book.title.icontains(q))
            return stmt

    create_tables()
    for title in ("Dune", "Dune Messiah", "Emma"):
        client.post("/books/", json={"title": title})
    response = client.get("/books/", params={"filter": '{"q":"dune"}'})
    assert [b["title"] for b in response.json()] == ["Dune", "Dune Messiah"]
    assert response.headers["content-range"] == "items 0-1/2"


# ---------------------------------------------------------------------------
# Nested views: a Parent context and fr.resolve_scope(ParentView)
# ---------------------------------------------------------------------------


def test_nested_view_with_parent_context_and_hooks(flavor):
    """The nested view of docs/crud_routes.md, on the final loads, with a
    where_id that reads the task number inside the project, and a not_found
    of its own."""

    class Project(fr.IDMixin, SharedBase):
        name: Mapped[str]
        archived: Mapped[bool] = mapped_column(default=False)

    class Task(fr.IDMixin, SharedBase):
        title: Mapped[str]
        number: Mapped[int] = mapped_column(default=0)
        project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), init=False)

    class ProjectSchema(fr.IDSchema):
        name: str

    class TaskSchema(fr.IDSchema):
        title: str
        number: int = 0
        project_id: fr.ReadOnly[int]

    class Parent(fr.ContextNamespace):
        project_id: fr.ContextParam[int]

    app = flavor.client.app

    @fr.include_view(app)
    class ProjectView(flavor.base):
        prefix = "/projects"
        model = Project
        schema = ProjectSchema
        scope = fr.where_clause(Project.archived.is_(False))

    if flavor.name == "sync":

        def project_from_path(project_id: int, session: fr.SessionDep) -> int:
            stmt = fr.apply_clauses(
                sa.select(Project.id).where(Project.id == project_id),
                fr.resolve_scope(ProjectView),
            )
            found = session.scalar(stmt)
            if found is None:
                raise fr.exc.NotFound(f"Project with id {project_id} was not found")
            return found

    else:

        async def project_from_path(  # type: ignore[misc]
            project_id: int, session: fr.AsyncSessionDep
        ) -> int:
            stmt = fr.apply_clauses(
                sa.select(Project.id).where(Project.id == project_id),
                fr.resolve_scope(ProjectView),
            )
            found = await session.scalar(stmt)
            if found is None:
                raise fr.exc.NotFound(f"Project with id {project_id} was not found")
            return found

    class ProjectTaskBase(flavor.base):
        prefix = "/projects/{project_id}/tasks"
        model = Task
        schema = TaskSchema
        dependencies = [Parent.depends(project_id=project_from_path)]
        scope = fr.all_of(
            fr.resolve_scope(Task),
            fr.where_clause(Task.project_id == Parent.project_id),
        )

        def where_id(self, id: int) -> sa.ColumnElement[bool]:
            # the task number inside the project, not the primary key
            return Task.number == id

        def not_found(self, id: Any) -> Exception:
            return fr.exc.NotFound(f"Project {Parent.project_id()} has no task {id}")

    if flavor.name == "sync":

        class ProjectTaskView(ProjectTaskBase):
            def create(self, data):
                task = super().create(data)
                task.project_id = Parent.project_id()
                return task

    else:

        class ProjectTaskView(ProjectTaskBase):  # type: ignore[no-redef]
            async def create(self, data):
                task = await super().create(data)
                task.project_id = Parent.project_id()
                return task

    fr.include_view(app, ProjectTaskView)
    flavor.create_tables()
    c = flavor.client
    c.post("/projects/", json={"name": "one"})
    c.post("/projects/", json={"name": "two"})
    c.post("/projects/1/tasks/", json={"title": "a", "number": 1})
    c.post("/projects/2/tasks/", json={"title": "b", "number": 1})
    c.post("/projects/2/tasks/", json={"title": "c", "number": 2})

    assert c.get("/projects/2/tasks/1").json()["title"] == "b"
    assert c.patch("/projects/2/tasks/2", json={"title": "c!"}).json()["title"] == "c!"
    missing = c.get("/projects/1/tasks/2", assert_status_code=404).json()
    assert missing == {"detail": "Project 1 has no task 2"}
    listed = c.get("/projects/2/tasks/").json()
    assert [t["title"] for t in listed["data"]] == ["b", "c!"]
    assert listed["total_count"] == 2
    c.delete("/projects/2/tasks/1", assert_status_code=204)
    flavor.run(sa.update(Project).where(Project.id == 2).values(archived=True))
    c.get("/projects/2/tasks/2", assert_status_code=404)


# ---------------------------------------------------------------------------
# where_id that returns something wrong
# ---------------------------------------------------------------------------


def test_where_id_on_another_table_is_refused(flavor):
    """A typo, ``Shelf.id`` in place of ``Book.id``, would make a cross join
    and load the wrong book. The load refuses the expression and names
    where_id."""
    Shelf, Book = _shelves_and_books()

    class BookSchema(fr.IDSchema):
        title: str

    @fr.include_view(flavor.client.app)
    class BookView(flavor.base):
        prefix = "/books"
        model = Book
        schema = BookSchema

        def where_id(self, id: int) -> sa.ColumnElement[bool]:
            return Shelf.id == id  # a typo: Shelf in place of Book

    flavor.create_tables()
    flavor.run(sa.insert(Shelf).values(id=2, name="s2"))
    flavor.run(sa.insert(Book).values(id=1, title="only book"))
    # without the check, SQL joins every shelf, and PATCH /books/2 changes
    # book 1
    with pytest.raises(TypeError, match="BookView.where_id: .* names a table"):
        flavor.client.patch("/books/2", json={"title": "changed"})


def test_where_id_that_returns_a_column_is_refused(flavor):
    """A column, not a comparison, is an InstrumentedAttribute, not a
    ColumnElement, so the check of the result refuses it with a TypeError
    that names where_id."""
    Shelf, Book = _shelves_and_books()

    class BookSchema(fr.IDSchema):
        title: str

    @fr.include_view(flavor.client.app)
    class BookView(flavor.base):
        prefix = "/books"
        model = Book
        schema = BookSchema

        def where_id(self, id: int) -> Any:
            return Book.title  # forgot "== id"

    flavor.create_tables()
    flavor.run(sa.insert(Book).values(id=1, title="Dune"))
    with pytest.raises(TypeError, match="where_id must return"):
        flavor.client.get("/books/1")


def test_where_id_that_is_true_for_every_row(flavor):
    """sqlalchemy.true() is a boolean expression. With one row in the scope,
    every id names that row; with two rows, the load raises
    MultipleResultsFound (a 500)."""
    Shelf, Book = _shelves_and_books()

    class BookSchema(fr.IDSchema):
        title: str

    @fr.include_view(flavor.client.app)
    class BookView(flavor.base):
        prefix = "/books"
        model = Book
        schema = BookSchema

        def where_id(self, id: int) -> sa.ColumnElement[bool]:
            return sa.true()

    flavor.create_tables()
    flavor.run(sa.insert(Book).values(id=1, title="Dune"))
    assert flavor.client.get("/books/999").json()["id"] == 1
    flavor.run(sa.insert(Book).values(id=2, title="Emma"))
    with pytest.raises(sa.exc.MultipleResultsFound):
        flavor.client.get("/books/999")


def test_an_async_where_id_is_refused_at_class_creation():
    """Every other hook of an async view is async, so ``async def where_id``
    is an easy mistake. where_id builds SQL and runs no query, so the class
    fails when Python creates it."""
    Shelf, Book = _shelves_and_books()

    with pytest.raises(
        fr.exc.RestlyConfigurationError, match="BookView.where_id is async"
    ):

        class BookView(fr.AsyncRestView):
            prefix = "/books"
            model = Book

            async def where_id(self, id: int) -> Any:  # type: ignore[override]
                return Book.id == id


# ---------------------------------------------------------------------------
# not_found that returns something wrong
# ---------------------------------------------------------------------------


def test_a_not_found_that_returns_a_text_names_the_hook(flavor):
    """A port can keep its old texts, such as "book not found". Raising a
    text fails with "exceptions must derive from BaseException", so the load
    checks the result and names not_found."""
    Shelf, Book = _shelves_and_books()

    class BookSchema(fr.IDSchema):
        title: str

    @fr.include_view(flavor.client.app)
    class BookView(flavor.base):
        prefix = "/books"
        model = Book
        schema = BookSchema

        def not_found(self, id: Any) -> Any:
            return "book not found"

    flavor.create_tables()
    with pytest.raises(TypeError, match="BookView.not_found must return an exception"):
        flavor.client.get("/books/1")


def test_a_not_found_that_returns_a_plain_exception_is_a_500(flavor):
    """Any exception is allowed. A ValueError is not an HTTP error, so the
    client gets a 500. That is what the user wrote, so it holds."""
    Shelf, Book = _shelves_and_books()

    class BookSchema(fr.IDSchema):
        title: str

    @fr.include_view(flavor.client.app)
    class BookView(flavor.base):
        prefix = "/books"
        model = Book
        schema = BookSchema

        def not_found(self, id: Any) -> Exception:
            return ValueError(f"no book {id}")

    flavor.create_tables()
    with pytest.raises(ValueError, match="no book 1"):
        flavor.client.get("/books/1")


def test_not_found_gets_none_for_an_expression(flavor):
    """A not_found that formats its argument, as Advanced Alchemy's users ask
    for ("Recipe {id} does not exist"), would show SQL for a natural-key
    route. So not_found gets ``None`` when a route loads by an expression,
    and the default 404 names only the model."""
    Shelf, Book = _shelves_and_books()

    class BookSchema(fr.IDSchema):
        title: str

    if flavor.name == "sync":

        class BookViewBase(flavor.base):
            @fr.get("/by-title/{title}")
            def by_title(self, title: str) -> Any:
                return self.to_response(self.handle_get_one(Book.title == title))

    else:

        class BookViewBase(flavor.base):  # type: ignore[no-redef]
            @fr.get("/by-title/{title}")
            async def by_title(self, title: str) -> Any:
                return self.to_response(await self.handle_get_one(Book.title == title))

    asked: list[Any] = []

    @fr.include_view(flavor.client.app)
    class BookView(BookViewBase):
        prefix = "/books"
        model = Book
        schema = BookSchema

        def not_found(self, id: Any) -> Exception:
            asked.append(id)
            if id is None:
                return super().not_found(id)
            return fr.exc.NotFound(f"Book {id} does not exist")

    flavor.create_tables()
    detail = flavor.client.get("/books/by-title/x", assert_status_code=404).json()
    assert detail == {"detail": "Book was not found"}
    detail = flavor.client.get("/books/7", assert_status_code=404).json()
    assert detail == {"detail": "Book 7 does not exist"}
    assert asked == [None, 7]


# ---------------------------------------------------------------------------
# joinedload on a collection, pagination, and the count over primary keys
# ---------------------------------------------------------------------------


def test_joinedload_collection_with_pages(flavor):
    """A joinedload of a collection in select() fans out the rows. SQLAlchemy
    puts the LIMIT in a subquery, so each page has whole parents, and the
    count over the primary keys is the number of parents."""
    Shelf, Book = _shelves_and_books()

    class BookRef(fr.IDSchema):
        title: str

    class ShelfSchema(fr.IDSchema):
        name: str
        books: fr.ReadOnly[list[BookRef]] = []

    @fr.include_view(flavor.client.app)
    class ShelfView(flavor.base):
        prefix = "/shelves"
        model = Shelf
        schema = ShelfSchema

        def get_loader_options(self) -> list[Any]:
            return [joinedload(Shelf.books)]

    flavor.create_tables()
    _seed_shelves(flavor, Shelf, Book)
    seen = flavor.selects()
    pages = [
        flavor.client.get("/shelves/", params={"page": p, "page_size": 2}).json()
        for p in (1, 2, 3)
    ]
    assert [[s["name"] for s in page["data"]] for page in pages] == [
        ["s1", "s2"],
        ["s3", "s4"],
        ["s5"],
    ]
    assert all(len(s["books"]) == 3 for page in pages for s in page["data"])
    assert {page["total_count"] for page in pages} == {5}
    count = next(s for s in seen if "count(" in s.lower())
    # the count selects the primary key only, and no join for the eager load
    assert "DISTINCT shelf.id" in count and "JOIN" not in count


def test_the_count_over_primary_keys_with_group_by_and_having(flavor):
    """An apply_list_params that groups and filters on an aggregate: the
    count keeps GROUP BY and HAVING, and selects the primary key."""
    Shelf, Book = _shelves_and_books()

    class ShelfSchema(fr.IDSchema):
        name: str

    if flavor.name == "sync":

        class Base(flavor.base):
            def apply_list_params(self, stmt, list_params):
                stmt = super().apply_list_params(stmt, list_params)
                return (
                    stmt.join(Shelf.books)
                    .group_by(Shelf.id)
                    .having(func.count(Book.id) >= 2)
                )

    else:

        class Base(flavor.base):  # type: ignore[no-redef]
            async def apply_list_params(self, stmt, list_params):
                stmt = await super().apply_list_params(stmt, list_params)
                return (
                    stmt.join(Shelf.books)
                    .group_by(Shelf.id)
                    .having(func.count(Book.id) >= 2)
                )

    @fr.include_view(flavor.client.app)
    class ShelfView(Base):
        prefix = "/shelves"
        model = Shelf
        schema = ShelfSchema

    flavor.create_tables()
    _seed_shelves(flavor, Shelf, Book, books_per_shelf=2)
    flavor.run(sa.delete(Book).where(Book.shelf_id == 3))
    page = flavor.client.get("/shelves/").json()
    assert page["total_count"] == 4
    assert [s["name"] for s in page["data"]] == ["s1", "s2", "s4", "s5"]


# ---------------------------------------------------------------------------
# A composite primary key
# ---------------------------------------------------------------------------


def test_composite_key_count_and_list(flavor):
    class Seat(SharedBase):
        row: Mapped[int] = mapped_column(primary_key=True)
        number: Mapped[int] = mapped_column(primary_key=True)

    class SeatSchema(fr.BaseSchema):
        row: int
        number: int

    @fr.include_view(flavor.client.app)
    class SeatView(flavor.base):
        prefix = "/seats"
        model = Seat
        schema = SeatSchema
        # an id from the path cannot name one seat
        exclude_routes = (
            fr.ViewRoute.GET_ONE,
            fr.ViewRoute.UPDATE,
            fr.ViewRoute.DELETE,
        )

    flavor.create_tables()
    for row in (1, 2):
        for number in (1, 2, 3):
            flavor.client.post("/seats/", json={"row": row, "number": number})
    page = flavor.client.get("/seats/", params={"page_size": 4}).json()
    assert page["total_count"] == 6
    assert len(page["data"]) == 4


# ---------------------------------------------------------------------------
# Values that load with the row: column_property and with_expression
# ---------------------------------------------------------------------------


def _counted_shelves():
    Shelf, Book = _shelves_and_books()
    Shelf.book_count = column_property(
        sa.select(func.count(Book.id))
        .where(Book.shelf_id == Shelf.id)
        .correlate_except(Book)
        .scalar_subquery()
    )
    return Shelf, Book


def test_a_related_column_property_is_fresh_after_create(flavor):
    """The worry: a column_property of a related row in the response is
    stale after a write, when the row was loaded earlier in the request (here
    by a check in create). It holds: the response has the new count."""
    Shelf, Book = _counted_shelves()

    class ShelfRef(fr.IDSchema):
        name: str
        book_count: fr.ReadOnly[int] = 0

    class BookSchema(fr.IDSchema):
        title: str
        shelf_id: int | None = None
        shelf: fr.ReadOnly[ShelfRef | None] = None

    if flavor.name == "sync":

        class Base(flavor.base):
            def create(self, data):
                shelf = self.session.get(Shelf, data.shelf_id)
                if shelf is not None and shelf.name == "closed":
                    raise fr.exc.Conflict("the shelf is closed")
                return super().create(data)

    else:

        class Base(flavor.base):  # type: ignore[no-redef]
            async def create(self, data):
                shelf = await self.session.get(Shelf, data.shelf_id)
                if shelf is not None and shelf.name == "closed":
                    raise fr.exc.Conflict("the shelf is closed")
                return await super().create(data)

    @fr.include_view(flavor.client.app)
    class BookView(Base):
        prefix = "/books"
        model = Book
        schema = BookSchema

    flavor.create_tables()
    flavor.run(sa.insert(Shelf).values(id=1, name="open"))
    created = flavor.client.post("/books/", json={"title": "a", "shelf_id": 1}).json()
    assert created["shelf"]["book_count"] == 1


def test_a_related_column_property_is_fresh_without_an_earlier_load(flavor):
    """Without the earlier load, the save loads the shelf after the flush, so
    the count is right."""
    Shelf, Book = _counted_shelves()

    class ShelfRef(fr.IDSchema):
        name: str
        book_count: fr.ReadOnly[int] = 0

    class BookSchema(fr.IDSchema):
        title: str
        shelf_id: int | None = None
        shelf: fr.ReadOnly[ShelfRef | None] = None

    @fr.include_view(flavor.client.app)
    class BookView(flavor.base):
        prefix = "/books"
        model = Book
        schema = BookSchema

    flavor.create_tables()
    flavor.run(sa.insert(Shelf).values(id=1, name="open"))
    flavor.run(sa.insert(Shelf).values(id=2, name="other"))
    created = flavor.client.post("/books/", json={"title": "a", "shelf_id": 1}).json()
    assert created["shelf"]["book_count"] == 1
    moved = flavor.client.patch("/books/1", json={"shelf_id": 2}).json()
    assert moved["shelf"] == {"id": 2, "name": "other", "book_count": 1}


def test_a_with_expression_value_on_the_write_responses(flavor):
    """The worry: the refresh after a write reloads a query_expression
    without its option, so the response loses the value. It holds: the
    reload after the save applies the loader options, so the POST and the
    PATCH responses have the value."""
    Shelf, Book = _shelves_and_books()
    Shelf.book_count = query_expression()

    class ShelfSchema(fr.IDSchema):
        name: str
        book_count: fr.ReadOnly[int | None] = None

    @fr.include_view(flavor.client.app)
    class ShelfView(flavor.base):
        prefix = "/shelves"
        model = Shelf
        schema = ShelfSchema

        def get_loader_options(self) -> list[Any]:
            count = (
                sa.select(func.count(Book.id))
                .where(Book.shelf_id == Shelf.id)
                .correlate_except(Book)
                .scalar_subquery()
            )
            return [with_expression(Shelf.book_count, count)]

    flavor.create_tables()
    _seed_shelves(flavor, Shelf, Book)
    assert flavor.client.get("/shelves/1").json()["book_count"] == 3
    patched = flavor.client.patch("/shelves/1", json={"name": "renamed"}).json()
    assert patched["book_count"] == 3
    created = flavor.client.post("/shelves/", json={"name": "new"}).json()
    assert created["book_count"] == 0


def test_to_single_response_reads_a_relationship_from_the_loader_options(flavor):
    """The place for a value per caller is to_single_response. Here the
    value reads a relationship that only get_loader_options loads: the
    response does not name it. The reload after the save applies the loader
    options, so the relationship is there after POST and PATCH too. On an
    async view, a lazy load there would raise MissingGreenlet (a 500)."""
    Shelf, Book = _shelves_and_books()

    class BookSchema(fr.IDSchema):
        title: str
        shelf_id: int | None = None
        on_open_shelf: fr.ReadOnly[bool] = False

    @fr.include_view(flavor.client.app)
    class BookView(flavor.base):
        prefix = "/books"
        model = Book
        schema = BookSchema

        def get_loader_options(self) -> list[Any]:
            return [*super().get_loader_options(), selectinload(Book.shelf)]

        def to_single_response(self, obj: Any) -> Any:
            response = super().to_single_response(obj)
            is_open = obj.shelf is not None and obj.shelf.name == "open"
            return response.model_copy(update={"on_open_shelf": is_open})

    flavor.create_tables()
    flavor.run(sa.insert(Shelf).values(id=1, name="open"))
    flavor.run(sa.insert(Shelf).values(id=2, name="closed"))
    flavor.run(sa.insert(Book).values(id=1, title="a", shelf_id=1))
    c = flavor.client
    assert c.get("/books/1").json()["on_open_shelf"] is True
    assert c.patch("/books/1", json={"title": "b"}).json()["on_open_shelf"] is True
    # the write changes the foreign key: the response has the new shelf
    assert c.patch("/books/1", json={"shelf_id": 2}).json()["on_open_shelf"] is False
    created = c.post("/books/", json={"title": "c", "shelf_id": 1}).json()
    assert created["on_open_shelf"] is True


# ---------------------------------------------------------------------------
# 410 Gone for a soft-deleted row, on every item route
# ---------------------------------------------------------------------------


def _soft_deletable():
    class Note(fr.IDMixin, SharedBase):
        text: Mapped[str]
        deleted: Mapped[bool] = mapped_column(default=False)

    class NoteSchema(fr.IDSchema):
        text: str

    sa.orm.configure_mappers()
    return Note, NoteSchema


def test_sync_not_found_can_answer_410_for_a_deleted_row(sync_db):
    """On a sync view, not_found can run a query: a row that the scope hides
    because it is deleted answers 410 on GET, PATCH and DELETE."""
    Note, NoteSchema = _soft_deletable()
    sync_client = RestlyTestClient(FastAPI())

    @fr.include_view(sync_client.app)
    class NoteView(fr.RestView):
        prefix = "/notes"
        model = Note
        schema = NoteSchema
        scope = fr.where_clause(Note.deleted.is_(False))

        def not_found(self, id: Any) -> Exception:
            gone = self.session.scalar(
                sa.select(Note.id).where(Note.id == id, Note.deleted.is_(True))
            )
            if gone is not None:
                return HTTPException(410, f"Note {id} was deleted")
            return super().not_found(id)

    SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])
    sync_client.post("/notes/", json={"text": "a"})
    with _fr_config.make_session() as session:
        session.execute(sa.update(Note).values(deleted=True))
        session.commit()
    sync_client.get("/notes/1", assert_status_code=410)
    sync_client.patch("/notes/1", json={"text": "b"}, assert_status_code=410)
    sync_client.delete("/notes/1", assert_status_code=410)
    sync_client.get("/notes/2", assert_status_code=404)


def test_async_not_found_can_answer_410_for_a_deleted_row(client):
    """On an async view, not_found may be ``async def``, and the load awaits
    it. So it can run a query too, and answer 410 on every item route."""
    Note, NoteSchema = _soft_deletable()

    @fr.include_view(client.app)
    class NoteView(fr.AsyncRestView):
        prefix = "/notes"
        model = Note
        schema = NoteSchema
        scope = fr.where_clause(Note.deleted.is_(False))

        async def not_found(self, id: Any) -> Exception:
            gone = await self.session.scalar(
                sa.select(Note.id).where(Note.id == id, Note.deleted.is_(True))
            )
            if gone is not None:
                return HTTPException(410, f"Note {id} was deleted")
            return super().not_found(id)

    create_tables()
    client.post("/notes/", json={"text": "a"})
    Flavor("async", client, None).run(sa.update(Note).values(deleted=True))
    client.get("/notes/1", assert_status_code=410)
    client.patch("/notes/1", json={"text": "b"}, assert_status_code=410)
    client.delete("/notes/1", assert_status_code=410)
    client.get("/notes/2", assert_status_code=404)
