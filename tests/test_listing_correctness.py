"""Read-path correctness regressions.

Covers two list-endpoint bugs:

* **lm7** — a paginated list sorted on a *non-unique* column had no PK
  tiebreaker, so rows could be skipped or repeated across pages. Both the
  standard dialect (``_apply_sorting``) and the react-admin dialect
  (``apply_react_admin_query``) must append the primary key as the final
  ``ORDER BY`` term (and must not duplicate it when the user already sorts by
  the PK). These are asserted on the compiled SQL, so they hold regardless of a
  given backend's incidental tie ordering.

* **0lq.3** — a ``build_query`` that JOINs a to-many relationship fans out
  (one row per child), which duplicated entities in the page and inflated the
  total. ``get_many`` now de-duplicates via ``.unique()`` and ``count`` counts a
  ``DISTINCT`` subquery. (Not reachable through the public URL grammar -- dotted
  filters/sorts only traverse to-one relations -- so the trigger is a
  collection JOIN added by an override, as exercised here.)
"""

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


# ---------------------------------------------------------------------------
# 0lq.3 -- a to-many JOIN in build_query must not duplicate rows / inflate count
# ---------------------------------------------------------------------------


def test_to_many_join_in_build_query_does_not_duplicate_or_inflate(client):
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

    @fr.include_view(client.app)
    class AuthorView(fr.AsyncRestView):
        prefix = "/authors"
        model = Author
        schema = AuthorSchema
        include_pagination_metadata = True

        def build_query(self):
            # A collection JOIN: one row per book -> fan-out without dedup.
            return super().build_query().join(Book, Book.author_id == Author.id)

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
    assert payload["total"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["name"] == "A"
