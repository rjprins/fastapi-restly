"""Read-path correctness regressions.

Covers two list-endpoint bugs:

* **lm7** — a paginated list sorted on a *non-unique* column had no PK
  tiebreaker, so rows could be skipped or repeated across pages. Both the
  standard dialect (``_apply_sorting``) and the react-admin dialect
  (``apply_react_admin_query``) must append the primary key as the final
  ``ORDER BY`` term (and must not duplicate it when the user already sorts by
  the PK). These are asserted on the compiled SQL, so they hold regardless of a
  given backend's incidental tie ordering.

* **to-many JOIN fan-out** — a view scope that JOINs a to-many relationship
  fans out (one row per child), which duplicated entities in the page and
  inflated the total. ``get_many`` now de-duplicates via ``.unique()`` and
  ``count`` counts a ``DISTINCT`` subquery. (Not reachable through the public
  URL grammar -- dotted filters/sorts only traverse to-one relations -- so the
  trigger is a collection JOIN in a scope transform, as exercised here.)
"""

import pydantic
import sqlalchemy
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from starlette.datastructures import QueryParams

import fastapi_restly as fr
from fastapi_restly.query._impl import _apply_sorting
from fastapi_restly.views._react_admin import apply_react_admin_query

from .conftest import create_tables

# ---------------------------------------------------------------------------
# lm7 -- PK tiebreaker on a non-unique sort (asserted on the compiled SQL)
# ---------------------------------------------------------------------------


class _Book(fr.IDBase):
    title: Mapped[str]
    status: Mapped[str]


class _BookSchema(fr.IDSchema):
    title: str
    status: str


def _order_by_sql(query: sqlalchemy.Select) -> str:
    return str(query).rsplit("ORDER BY", 1)[1]


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
    out = apply_react_admin_query(
        sqlalchemy.select(_Book), _Book, _BookSchema, ("status", "ASC"), 0, 9, {}
    )
    order_by = _order_by_sql(out)
    assert ".status" in order_by and ".id" in order_by
    assert order_by.rindex(".id") > order_by.rindex(".status")


def test_react_admin_sort_by_pk_is_not_duplicated():
    out = apply_react_admin_query(
        sqlalchemy.select(_Book), _Book, _BookSchema, ("id", "ASC"), 0, 9, {}
    )
    assert _order_by_sql(out).count(".id") == 1


class _Code(fr.DataclassBase):
    """A primary key that is not called ``id``."""

    __tablename__ = "lc_codes"
    code: Mapped[str] = mapped_column(primary_key=True)
    status: Mapped[str]


class _CodeSchema(pydantic.BaseModel):
    code: str
    status: str


class _Pair(fr.DataclassBase):
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
    out = apply_react_admin_query(
        sqlalchemy.select(_Code), _Code, _CodeSchema, ("status", "ASC"), 0, 9, {}
    )
    order_by = _order_by_sql(out)
    assert order_by.rindex("lc_codes.code") > order_by.rindex("lc_codes.status")
    out = apply_react_admin_query(
        sqlalchemy.select(_Code), _Code, _CodeSchema, None, 0, 9, {}
    )
    assert _order_by_sql(out).split("LIMIT")[0].strip() == "lc_codes.code"


def test_react_admin_sort_appends_every_column_of_a_composite_key():
    out = apply_react_admin_query(
        sqlalchemy.select(_Pair), _Pair, _PairSchema, ("b", "DESC"), 0, 9, {}
    )
    assert _order_by_sql(out).split("LIMIT")[0].strip() == "lc_pairs.b DESC, lc_pairs.a"


# ---------------------------------------------------------------------------
# A to-many JOIN in the scope must not duplicate rows / inflate count
# ---------------------------------------------------------------------------


def test_to_many_join_in_scope_does_not_duplicate_or_inflate(client):
    class Author(fr.IDBase):
        name: Mapped[str]

    class Book(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey(Author.id))

    class AuthorSchema(fr.IDSchema):
        name: str

    class BookSchema(fr.IDSchema):
        title: str
        author_id: int

    @fr.transform_clause
    def join_books(stmt: sqlalchemy.Select) -> sqlalchemy.Select:
        # A collection JOIN: one row per book -> fan-out without dedup.
        return stmt.join(Book, Book.author_id == Author.id)

    @fr.include_view(client.app)
    class AuthorView(fr.AsyncRestView):
        prefix = "/authors"
        model = Author
        schema = AuthorSchema
        scope = join_books

    @fr.include_view(client.app)
    class BookView(fr.AsyncRestView):
        prefix = "/books"
        model = Book
        schema = BookSchema

    create_tables()

    author = client.post("/authors/", json={"name": "A"}).json()
    for i in range(3):
        client.post("/books/", json={"title": f"b{i}", "author_id": author["id"]})

    payload = client.get("/authors/").json()

    # Without .unique()/.distinct() this would be 3 duplicate authors, total 3.
    assert payload["total_count"] == 1
    assert len(payload["data"]) == 1
    assert payload["data"][0]["name"] == "A"
