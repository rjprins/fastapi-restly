"""Read-path correctness regressions.

Covers three list-endpoint bugs:

* **PK tiebreaker** — a paginated list sorted on a *non-unique* column had no
  PK tiebreaker, so rows could be skipped or repeated across pages. Both the
  standard dialect (``_apply_sorting``) and the react-admin dialect
  (``_apply_react_admin_sort_and_range``) must append the primary key as the final
  ``ORDER BY`` term (and must not duplicate it when the user already sorts by
  the PK). These are asserted on the compiled SQL, so they hold regardless of a
  given backend's incidental tie ordering.

* **a condition on a to-many relationship**: it goes in ``EXISTS``
  (``.any()``), and each page then has whole rows. A join would repeat the
  row for each child, as in SQLAlchemy, and Restly does not page around
  that. (The list params never join a to-many relationship: dotted filters
  and sorts only follow to-one relations.)

* **the count's columns**: the count ran ``DISTINCT`` over every column of the
  model, which computed each ``column_property`` for every row. It now counts
  the columns that name a row of the entity: the primary key, and the
  discriminator of a concrete union.
"""

import asyncio

import pydantic
import pytest
import sqlalchemy
import sqlalchemy.orm
from fastapi import FastAPI
from sqlalchemy import ForeignKey, event
from sqlalchemy.ext.declarative import AbstractConcreteBase
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    column_property,
    mapped_column,
    relationship,
)
from starlette.datastructures import QueryParams

import fastapi_restly as fr
from fastapi_restly.db._globals import _fr_config
from fastapi_restly.query._impl import _apply_sorting
from fastapi_restly.testing._client import RestlyTestClient
from fastapi_restly.views._react_admin import _apply_react_admin_sort_and_range

from ._models import SharedBase
from .conftest import create_tables

# ---------------------------------------------------------------------------
# PK tiebreaker on a non-unique sort (asserted on the compiled SQL)
# ---------------------------------------------------------------------------


class _Book(fr.IDMixin, SharedBase):
    title: Mapped[str]
    status: Mapped[str]


class _BookSchema(fr.IDSchema):
    title: str
    status: str


def _order_by_sql(stmt: sqlalchemy.Select) -> str:
    return str(stmt).rsplit("ORDER BY", 1)[1]


def test_standard_sort_appends_pk_tiebreaker():
    out = _apply_sorting(
        QueryParams("sort=status"), sqlalchemy.select(_Book), _Book, _BookSchema
    )
    order_by = _order_by_sql(out)
    # status first, the PK last -- the deterministic tiebreaker.
    assert ".status" in order_by and ".id" in order_by
    assert order_by.rindex(".id") > order_by.rindex(".status")


def test_standard_sort_by_pk_is_not_duplicated():
    out = _apply_sorting(
        QueryParams("sort=id"), sqlalchemy.select(_Book), _Book, _BookSchema
    )
    # Already sorted by the PK -- it must not be appended a second time.
    assert _order_by_sql(out).count(".id") == 1


def test_react_admin_sort_appends_pk_tiebreaker():
    out = _apply_react_admin_sort_and_range(
        sqlalchemy.select(_Book), _Book, _BookSchema, ("status", "ASC"), 0, 9
    )
    order_by = _order_by_sql(out)
    assert ".status" in order_by and ".id" in order_by
    assert order_by.rindex(".id") > order_by.rindex(".status")


def test_react_admin_sort_by_pk_is_not_duplicated():
    out = _apply_react_admin_sort_and_range(
        sqlalchemy.select(_Book), _Book, _BookSchema, ("id", "ASC"), 0, 9
    )
    assert _order_by_sql(out).count(".id") == 1


class _Code(SharedBase):
    """A primary key that is not called ``id``."""

    __tablename__ = "lc_codes"
    code: Mapped[str] = mapped_column(primary_key=True)
    status: Mapped[str]


class _CodeSchema(pydantic.BaseModel):
    code: str
    status: str


class _Pair(SharedBase):
    """A composite primary key."""

    __tablename__ = "lc_pairs"
    a: Mapped[int] = mapped_column(primary_key=True)
    b: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str]


class _PairSchema(pydantic.BaseModel):
    a: int
    b: int
    status: str


def test_standard_default_order_is_the_primary_key_whatever_its_name():
    out = _apply_sorting(QueryParams(""), sqlalchemy.select(_Code), _Code, _CodeSchema)
    assert _order_by_sql(out).strip() == "lc_codes.code"


def test_standard_sort_appends_a_primary_key_not_called_id():
    out = _apply_sorting(
        QueryParams("sort=status"), sqlalchemy.select(_Code), _Code, _CodeSchema
    )
    order_by = _order_by_sql(out)
    assert order_by.rindex("lc_codes.code") > order_by.rindex("lc_codes.status")
    out = _apply_sorting(
        QueryParams("sort=code"), sqlalchemy.select(_Code), _Code, _CodeSchema
    )
    assert _order_by_sql(out).count("lc_codes.code") == 1


def test_standard_sort_appends_every_column_of_a_composite_key():
    out = _apply_sorting(
        QueryParams("sort=status"), sqlalchemy.select(_Pair), _Pair, _PairSchema
    )
    assert _order_by_sql(out).strip() == "lc_pairs.status ASC, lc_pairs.a, lc_pairs.b"
    out = _apply_sorting(
        QueryParams("sort=-b"), sqlalchemy.select(_Pair), _Pair, _PairSchema
    )
    assert _order_by_sql(out).strip() == "lc_pairs.b DESC, lc_pairs.a"


def test_react_admin_sort_appends_a_primary_key_not_called_id():
    out = _apply_react_admin_sort_and_range(
        sqlalchemy.select(_Code), _Code, _CodeSchema, ("status", "ASC"), 0, 9
    )
    order_by = _order_by_sql(out)
    assert order_by.rindex("lc_codes.code") > order_by.rindex("lc_codes.status")
    out = _apply_react_admin_sort_and_range(
        sqlalchemy.select(_Code), _Code, _CodeSchema, None, 0, 9
    )
    assert _order_by_sql(out).split("LIMIT")[0].strip() == "lc_codes.code"


def test_react_admin_sort_appends_every_column_of_a_composite_key():
    out = _apply_react_admin_sort_and_range(
        sqlalchemy.select(_Pair), _Pair, _PairSchema, ("b", "DESC"), 0, 9
    )
    assert _order_by_sql(out).split("LIMIT")[0].strip() == "lc_pairs.b DESC, lc_pairs.a"


# ---------------------------------------------------------------------------
# A condition on a to-many relationship goes in EXISTS
# ---------------------------------------------------------------------------


def test_to_many_condition_in_exists_gives_whole_pages(client):
    class Author(fr.IDMixin, SharedBase):
        name: Mapped[str]
        books: Mapped[list["Book"]] = relationship(init=False)

    class Book(fr.IDMixin, SharedBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey(Author.id))

    class AuthorSchema(fr.IDSchema):
        name: str

    class BookSchema(fr.IDSchema):
        title: str
        author_id: int

    @fr.include_view(client.app)
    class AuthorView(fr.AsyncRestView):
        prefix = "/authors"
        model = Author
        schema = AuthorSchema

        async def apply_list_params(self, stmt, list_params):
            stmt = stmt.where(Author.books.any(Book.title.contains("b")))
            return await super().apply_list_params(stmt, list_params)

    @fr.include_view(client.app)
    class BookView(fr.AsyncRestView):
        prefix = "/books"
        model = Book
        schema = BookSchema

    create_tables()
    for name in ("a1", "a2", "a3"):
        author = client.post("/authors/", json={"name": name}).json()
        for i in range(3):
            client.post("/books/", json={"title": f"b{i}", "author_id": author["id"]})
    client.post("/authors/", json={"name": "no books"})

    pages = [
        client.get("/authors/", params={"page": page, "page_size": 2}).json()
        for page in (1, 2)
    ]

    assert [page["total_count"] for page in pages] == [3, 3]
    assert [[a["name"] for a in page["data"]] for page in pages] == [
        ["a1", "a2"],
        ["a3"],
    ]


# ---------------------------------------------------------------------------
# The count counts the rows of the entity the list selects
# ---------------------------------------------------------------------------


@pytest.fixture(params=["async", "sync"])
def views(request):
    """A client, the view base class and the engine, for each kind of view."""
    if request.param == "async":
        client = request.getfixturevalue("client")
        engine = _fr_config.async_make_session.kw["bind"].sync_engine
        return client, fr.AsyncRestView, engine, create_tables
    engine, _ = request.getfixturevalue("sync_db")
    client = RestlyTestClient(FastAPI())
    return client, fr.RestView, engine, lambda: SharedBase.metadata.create_all(engine)


def test_count_does_not_run_a_column_property(views):
    """The count leaves out every column except the primary key. So the
    subquery of a ``column_property`` does not run for every row."""
    client, view_base, engine, make_tables = views

    class Shelf(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Novel(fr.IDMixin, SharedBase):
        shelf_id: Mapped[int] = mapped_column(ForeignKey(Shelf.id))

    Shelf.novel_count = column_property(
        sqlalchemy.select(sqlalchemy.func.count(Novel.id))
        .where(Novel.shelf_id == Shelf.id)
        .scalar_subquery()
    )

    class ShelfSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class ShelfView(view_base):
        prefix = "/shelves"
        model = Shelf
        schema = ShelfSchema

    make_tables()
    for name in ("a", "b", "c"):
        client.post("/shelves/", json={"name": name})

    statements: list[str] = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        payload = client.get("/shelves/", params={"page_size": 2}).json()
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert payload["total_count"] == 3
    (count_sql,) = [s for s in statements if "count(*)" in s]
    assert "FROM novel" not in count_sql


def test_count_of_a_single_table_subclass(client):
    """The count of a view on a single-table subclass keeps the WHERE clause
    of the subclass. So it counts only the rows of that subclass."""

    class Animal(fr.IDMixin, SharedBase):
        name: Mapped[str]
        kind: Mapped[str] = mapped_column(init=False)
        __mapper_args__ = {"polymorphic_on": "kind", "polymorphic_identity": "animal"}

    class Cat(Animal):
        __tablename__ = None  # type: ignore[assignment]
        __mapper_args__ = {"polymorphic_identity": "cat"}

    class Dog(Animal):
        __tablename__ = None  # type: ignore[assignment]
        __mapper_args__ = {"polymorphic_identity": "dog"}

    class AnimalSchema(fr.IDSchema):
        name: str

    for prefix, model in (("/animals", Animal), ("/cats", Cat), ("/dogs", Dog)):
        fr.include_view(
            client.app,
            type(
                f"{model.__name__}View",
                (fr.AsyncRestView,),
                {"prefix": prefix, "model": model, "schema": AnimalSchema},
            ),
        )

    create_tables()
    client.post("/cats/", json={"name": "tom"})
    client.post("/cats/", json={"name": "kit"})
    client.post("/dogs/", json={"name": "rex"})

    assert client.get("/cats/").json()["total_count"] == 2
    assert client.get("/dogs/").json()["total_count"] == 1
    assert client.get("/animals/").json()["total_count"] == 3


def test_count_of_a_concrete_union(client):
    """Each table of a concrete inheritance numbers its own rows, so the same
    id is in more than one table. The count of the union counts each pair of
    discriminator and id."""

    # AbstractConcreteBase does not work with MappedAsDataclass
    class PlainBase(DeclarativeBase):
        pass

    class Payment(AbstractConcreteBase, PlainBase):
        strict_attrs = True
        id: Mapped[int] = mapped_column(primary_key=True)
        amount: Mapped[int]

    class CardPayment(Payment):
        __tablename__ = "card_payment"
        __mapper_args__ = {"polymorphic_identity": "card", "concrete": True}

    class BankPayment(Payment):
        __tablename__ = "bank_payment"
        __mapper_args__ = {"polymorphic_identity": "bank", "concrete": True}

    PlainBase.registry.configure()

    class PaymentSchema(fr.IDSchema):
        amount: int

    for prefix, model in (
        ("/payments", Payment),
        ("/card-payments", CardPayment),
        ("/bank-payments", BankPayment),
    ):
        fr.include_view(
            client.app,
            type(
                f"{model.__name__}View",
                (fr.AsyncRestView,),
                {"prefix": prefix, "model": model, "schema": PaymentSchema},
            ),
        )

    asyncio.run(fr.db.async_create_all(PlainBase))
    client.post("/card-payments/", json={"amount": 10})
    client.post("/card-payments/", json={"amount": 20})
    client.post("/bank-payments/", json={"amount": 30})

    # card 1 and bank 1 are two rows of the list
    payload = client.get("/payments/").json()
    assert len(payload["data"]) == 3
    assert payload["total_count"] == 3
    filtered = client.get("/payments/", params={"amount__gte": 20}).json()
    assert filtered["total_count"] == 2


def test_count_of_a_statement_of_another_entity(client):
    """``count`` counts the entity that the statement selects, which need
    not be the model of the view. A statement of columns counts its distinct
    rows."""

    class Shelf(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Novel(fr.IDMixin, SharedBase):
        genre: Mapped[str]
        shelf_id: Mapped[int] = mapped_column(ForeignKey(Shelf.id))

    class ShelfSchema(fr.IDSchema):
        name: str

    class NovelSchema(fr.IDSchema):
        genre: str
        shelf_id: int

    @fr.include_view(client.app)
    class ShelfView(fr.AsyncRestView):
        prefix = "/shelves"
        model = Shelf
        schema = ShelfSchema

        @fr.get("/{id}/totals")
        async def totals(self, id: int) -> dict[str, int]:
            novels = sqlalchemy.select(Novel).where(Novel.shelf_id == id)
            other = sqlalchemy.orm.aliased(Shelf)
            return {
                "novels": await self.count(novels),
                "genres": await self.count(novels.with_only_columns(Novel.genre)),
                "other_shelves": await self.count(
                    sqlalchemy.select(other).where(other.id != id)
                ),
            }

    @fr.include_view(client.app)
    class NovelView(fr.AsyncRestView):
        prefix = "/novels"
        model = Novel
        schema = NovelSchema

    create_tables()
    shelf = client.post("/shelves/", json={"name": "a"}).json()
    client.post("/shelves/", json={"name": "b"})
    for genre in ("crime", "crime", "drama"):
        client.post("/novels/", json={"genre": genre, "shelf_id": shelf["id"]})

    totals = client.get(f"/shelves/{shelf['id']}/totals").json()
    assert totals == {"novels": 3, "genres": 2, "other_shelves": 1}


def test_default_ordering_belongs_in_apply_list_params(client):
    """A list's default ordering precedes the client's sort order."""

    class RankedNote(fr.IDMixin, SharedBase):
        rank: Mapped[int]

    class RankedNoteSchema(fr.IDSchema):
        rank: int

    @fr.include_view(client.app)
    class RankedNoteView(fr.AsyncRestView):
        prefix = "/ranked-notes"
        model = RankedNote
        schema = RankedNoteSchema

        async def apply_list_params(self, stmt, list_params):
            # before super(): the client's ?sort= then orders within it
            stmt = stmt.order_by(RankedNote.rank.desc())
            return await super().apply_list_params(stmt, list_params)

    create_tables()
    for rank in (1, 3, 2, 3):
        client.post("/ranked-notes/", json={"rank": rank})

    rows = client.get("/ranked-notes/").json()["data"]
    assert [row["rank"] for row in rows] == [3, 3, 2, 1]
    # the primary-key tiebreak still makes the page order stable
    assert rows[0]["id"] < rows[1]["id"]

    within = client.get("/ranked-notes/?sort=-id").json()["data"]
    assert [row["rank"] for row in within] == [3, 3, 2, 1]
    assert within[0]["id"] > within[1]["id"]
