"""Regression for ticket oiul: an ``fr.IDRef[T]``-typed FK field on the response
schema must derive list filter params.

A scalar ``post_id: fr.IDRef[Post]`` FK field -- now discouraged in favor of
``fr.MustExist[int, Post]`` but still supported -- must still derive filter
params. Because ``IDRef`` is a ``BaseModel`` subclass, the list-params generator
used to recurse
into it (yielding only the dead path ``post_id.id``), so the FK got NO filter
param at all and ``GET /comments/?post_id=1`` returned 422 -- the single most
common filter in any parent-child API. Even forced through, the apply path bound
the ``IDRef`` object itself as the SQL value and crashed.

An ``IDRef`` FK now filters on its own public name with the opaque-id operator
set (eq, ``__in``, ``__ne``, ``__isnull``) -- the same operators that make sense
for an opaque identifier, uniform across int/uuid/str PK types. The range and
substring families are deliberately NOT exposed (range on an opaque id is noise;
choosing ``IDRef`` over ``int`` signals "reference", not "orderable quantity").
Sync and async share the query layer, so both are exercised here for parity.
"""


from uuid import UUID, uuid4

from fastapi import FastAPI
from sqlalchemy import ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.query import create_list_params_schema
from fastapi_restly.testing import RestlyTestClient

from .conftest import create_tables

OPAQUE_SET = {"post_id", "post_id__in", "post_id__ne", "post_id__isnull"}
NEVER = {
    "post_id__gte",
    "post_id__lte",
    "post_id__gt",
    "post_id__lt",
    "post_id__contains",
    "post_id__icontains",
    "post_id.id",  # the dead nested path the generator used to emit
}


# --------------------------------------------------------------------------- #
# Param generation                                                            #
# --------------------------------------------------------------------------- #


def test_idref_fk_generates_opaque_filter_params():
    class Post(fr.IDBase):
        title: Mapped[str] = mapped_column()

    class Comment(fr.IDBase):
        content: Mapped[str] = mapped_column()
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

    class CommentRead(fr.IDSchema):
        content: str
        post_id: fr.IDRef[Post]

    fields = set(create_list_params_schema(CommentRead, Comment).model_fields)

    assert OPAQUE_SET <= fields
    assert NEVER.isdisjoint(fields)


def test_idref_fk_uuid_pk_yields_the_same_opaque_set():
    # Uniformity: the operator set does not depend on the PK type. A uuid PK
    # gets exactly the same four operators as an int PK -- no range either way.
    class UPost(fr.DataclassBase):
        __tablename__ = "oiul_upost"
        id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid4)
        title: Mapped[str] = mapped_column(default="t")

    class UComment(fr.IDBase):
        content: Mapped[str] = mapped_column()
        post_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("oiul_upost.id"))

    class UCommentRead(fr.IDSchema):
        content: str
        post_id: fr.IDRef[UPost]

    fields = set(create_list_params_schema(UCommentRead, UComment).model_fields)

    assert OPAQUE_SET <= fields
    assert NEVER.isdisjoint(fields)


def test_idref_targeting_leaves_nested_resource_dotted_traversal_intact():
    # The fix targets IDRef specifically, not IDSchema. A nested *resource*
    # schema that embeds its own id also subclasses IDSchema; it must keep its
    # dotted relation traversal rather than collapse to a scalar leaf.
    class Pub(fr.IDBase):
        name: Mapped[str] = mapped_column()

    class Author(fr.IDBase):
        name: Mapped[str] = mapped_column()
        pub_id: Mapped[int | None] = mapped_column(ForeignKey("pub.id"))
        pub: Mapped[Pub | None] = relationship()

    class PubResource(fr.IDSchema):  # nested resource, NOT a flat IDRef
        name: str

    class AuthorSchema(fr.IDSchema):
        name: str
        pub: PubResource | None = None

    fields = set(create_list_params_schema(AuthorSchema, Author).model_fields)

    # Dotted traversal through the embedded relationship still works.
    assert "pub.name" in fields
    assert "pub.id" in fields


# --------------------------------------------------------------------------- #
# End-to-end: async + sync parity                                             #
# --------------------------------------------------------------------------- #
#
# Models are defined locally (inside ``_register_blog``) so the autouse
# ``reset_metadata`` fixture, which keys cleanup off a ``<locals>`` qualname,
# tears them down between tests.


def _register_blog(app, view_base):
    """Register a /posts + /comments blog where Comment.post_id is an IDRef."""

    class Post(fr.IDBase):
        title: Mapped[str] = mapped_column()

    class Comment(fr.IDBase):
        content: Mapped[str] = mapped_column()
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

    class PostRead(fr.IDSchema):
        title: str

    class CommentRead(fr.IDSchema):
        content: str
        post_id: fr.IDRef[Post]

    @fr.include_view(app)
    class PostView(view_base):
        prefix = "/posts"
        model = Post
        schema = PostRead

    @fr.include_view(app)
    class CommentView(view_base):
        prefix = "/comments"
        model = Comment
        schema = CommentRead


def _seed(client):
    """Create two posts and three comments; return (p1, p2) ids."""
    p1 = client.post("/posts/", json={"title": "first"}).json()["id"]
    p2 = client.post("/posts/", json={"title": "second"}).json()["id"]
    for post_id in (p1, p1, p2):
        assert (
            client.post(
                "/comments/", json={"content": "c", "post_id": post_id}
            ).status_code
            == 201
        )
    return p1, p2


def _assert_fk_filtering(client, p1, p2):
    def post_ids(query):
        resp = client.get(f"/comments/{query}")
        assert resp.status_code == 200, (query, resp.status_code, resp.text)
        return sorted(c["post_id"] for c in resp.json())

    # The headline fix: filtering by the FK no longer 422s and returns the
    # matching children only.
    assert post_ids(f"?post_id={p1}") == [p1, p1]
    assert post_ids(f"?post_id__in={p1},{p2}") == [p1, p1, p2]
    assert post_ids(f"?post_id__ne={p1}") == [p2]
    assert post_ids("?post_id__isnull=false") == [p1, p1, p2]

    # A non-coercible id is a clean 400 (BadQueryParam), not a 500 -- the same
    # convention as any other filter value that fails coercion.
    client.get("/comments/?post_id=not-an-int", assert_status_code=400)


def test_async_list_filters_by_idref_fk(client):
    _register_blog(client.app, fr.AsyncRestView)
    create_tables()
    _assert_fk_filtering(client, *_seed(client))


def test_sync_list_filters_by_idref_fk(sync_db):
    engine, _ = sync_db
    app = FastAPI()
    _register_blog(app, fr.RestView)
    fr.DataclassBase.metadata.create_all(engine)
    client = RestlyTestClient(app)
    _assert_fk_filtering(client, *_seed(client))
