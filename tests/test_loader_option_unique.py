"""``get_one`` and ``get_many`` must both tolerate a ``joinedload`` of a
to-many relationship on the shared loader seam.

``get_many`` and ``get_one`` share their eager loading through one seam,
``get_relationship_loader_options()``. SQLAlchemy documents ``.unique()`` as
required for results with joined eager loads against collections; ``.all()``
(the ``get_many`` fetch) enforces it with ``InvalidRequestError``, while
``.first()`` (the ``get_one`` fetch) currently happens not to — an
enforcement detail, not a contract. Both read paths now apply ``.unique()``
(a no-op for the ``selectinload`` default), so a ``joinedload``-to-many on
the seam is uniformly safe; these tests pin that the detail and list reads
keep answering, sync and async alike.
"""

from fastapi import FastAPI
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, joinedload, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient

from .conftest import create_tables


def _register_library(app, view_base):
    """Register /loader-authors (joinedload-to-many on the loader seam) and
    /loader-books (used to seed the fan-out)."""

    class LoaderAuthor(fr.IDBase):
        name: Mapped[str]
        books: Mapped[list["LoaderBook"]] = relationship(default_factory=list)

    class LoaderBook(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey("loader_author.id"))

    class AuthorSchema(fr.IDSchema):
        name: str

    class BookSchema(fr.IDSchema):
        title: str
        author_id: int

    @fr.include_view(app)
    class LoaderAuthorView(view_base):
        prefix = "/loader-authors"
        model = LoaderAuthor
        schema = AuthorSchema

        def get_relationship_loader_options(self):
            return [joinedload(LoaderAuthor.books)]

    @fr.include_view(app)
    class LoaderBookView(view_base):
        prefix = "/loader-books"
        model = LoaderBook
        schema = BookSchema


def _assert_detail_and_list_survive(client):
    author_id = client.post(
        "/loader-authors/", json={"name": "ann"}, assert_status_code=201
    ).json()["id"]
    # Two books make the to-many JOIN fan out into multiple rows per author.
    for title in ("one", "two"):
        client.post(
            "/loader-books/",
            json={"title": title, "author_id": author_id},
            assert_status_code=201,
        )

    # The detail read follows the same documented unique() contract as the
    # list; it must answer regardless of how SQLAlchemy enforces it.
    one = client.get(f"/loader-authors/{author_id}", assert_status_code=200).json()
    assert one["name"] == "ann"

    # The list keeps deduplicating: one author, not one per joined book row.
    many = client.get("/loader-authors/", assert_status_code=200).json()
    assert [a["name"] for a in many] == ["ann"]


def test_get_one_survives_joinedload_to_many_loader_async(client):
    _register_library(client.app, fr.AsyncRestView)
    create_tables()
    _assert_detail_and_list_survive(client)


def test_get_one_survives_joinedload_to_many_loader_sync(sync_db):
    engine, _ = sync_db
    app = FastAPI()
    _register_library(app, fr.RestView)
    fr.DataclassBase.metadata.create_all(engine)
    _assert_detail_and_list_survive(RestlyTestClient(app))
