"""An IDRef reference is checked against the referenced model's
``default_scope``, not against the ``scope`` of a view.

So a visibility rule that only a view declares does not cover references.
This pins the documented way to add one: ``authorize`` runs BEFORE the
reference check and sees the *unresolved* reference (``data.<field>.id``), so it
can reject a reference by id before the row is ever fetched.
"""

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr

from ._models import SharedBase
from .conftest import create_tables


def test_idref_reference_is_unresolved_and_gatable_in_authorize(client):
    class Author(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Article(fr.IDMixin, SharedBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey(Author.id))

    class AuthorSchema(fr.IDSchema):
        name: str

    class ArticleSchema(fr.IDSchema):
        title: str
        author_id: fr.IDRef[Author]

    gate: dict = {"forbidden_id": None}
    seen: dict = {}

    @fr.include_view(client.app)
    class AuthorView(fr.AsyncRestView):
        prefix = "/authors"
        model = Author
        schema = AuthorSchema

    @fr.include_view(client.app)
    class ArticleView(fr.AsyncRestView):
        prefix = "/articles"
        model = Article
        schema = ArticleSchema

        async def authorize(self, action, obj=None, data=None):
            ref = getattr(data, "author_id", None) if data is not None else None
            if ref is not None:
                # The reference is UNRESOLVED here: it is the IDRef carrying .id,
                # not the resolved Author ORM row (which only exists after the
                # business method runs).
                seen["ref_type"] = type(ref).__name__
                seen["ref_id"] = ref.id
                if ref.id == gate["forbidden_id"]:
                    raise fr.exc.NotFound("author not found")

    create_tables()

    a1 = client.post("/authors/", json={"name": "a1"}).json()
    a2 = client.post("/authors/", json={"name": "a2"}).json()
    gate["forbidden_id"] = a1["id"]

    # A reference to the "invisible" author is rejected in authorize, by id,
    # before the row is fetched. Author has no default_scope, so the
    # reference check alone would accept it.
    client.post(
        "/articles/", json={"title": "x", "author_id": a1["id"]}, assert_status_code=404
    )
    assert seen["ref_id"] == a1["id"]
    assert seen["ref_type"] != "Author"  # unresolved reference, not the ORM row

    # A permitted reference passes the gate and resolves normally.
    resp = client.post("/articles/", json={"title": "y", "author_id": a2["id"]})
    assert resp.json()["author_id"] == a2["id"]
