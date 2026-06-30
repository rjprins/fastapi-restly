"""IDRef / IDSchema serialize through the type itself (ticket 0lq.10).

The view layer used to pre-extract a reference's scalar id before re-validating
the response (`_serialize_idschema_value`). That workaround is gone: the
reference types now self-serialize under plain Pydantic ``from_attributes``,
whether the attribute read off the ORM row is the related row (a relationship)
or the raw scalar FK (a scalar-named reference). These tests pin:

1. ``Schema.model_validate(orm_row, from_attributes=True)`` no longer crashes for
   ``IDRef`` / ``list[IDRef]`` -- the leak that the workaround masked everywhere
   outside ``to_response_schema`` (nested models, custom endpoints, a raw
   ``response_model``).
2. ``to_response_schema`` output is unchanged for ``IDRef`` (flat id), ``IDSchema``
   (``{"id": ...}``), ``list[IDRef]``, and the react-admin path -- for both the
   relationship-named form (read yields the row) and the scalar-named form (read
   yields the raw id).
3. A nested IDSchema *subclass* with extra fields is NOT collapsed to its id.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr

from .conftest import create_tables

# ---------------------------------------------------------------------------
# 1. Self-sufficiency under plain ``from_attributes`` (the headline fix).
#    No view, no resolver -- straight Pydantic validation of an ORM row.
# ---------------------------------------------------------------------------


def test_idref_self_sufficient_under_plain_from_attributes(sync_db):
    """``model_validate(row, from_attributes=True)`` returns the flat id for an
    ``IDRef`` field instead of crashing with an ``int_type`` error."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"), init=False)
        post: Mapped[Post] = relationship(default=None)

    class CommentSchema(fr.BaseSchema):
        content: str
        post: fr.IDRef[Post]

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        post = Post(title="p1")
        comment = Comment(content="hi", post=post)
        session.add_all([post, comment])
        session.flush()

        validated = CommentSchema.model_validate(comment, from_attributes=True)
        assert validated.model_dump() == {"content": "hi", "post": post.id}


def test_idref_list_self_sufficient_under_plain_from_attributes(sync_db):
    """Same for ``list[IDRef[T]]`` -- each related row resolves to its id."""
    engine, make_session = sync_db

    class Tag(fr.IDBase):
        name: Mapped[str]
        article_id: Mapped[int] = mapped_column(ForeignKey("article.id"), init=False)

    class Article(fr.IDBase):
        title: Mapped[str]
        tags: Mapped[list[Tag]] = relationship(default_factory=list)

    class ArticleSchema(fr.BaseSchema):
        title: str
        tags: list[fr.IDRef[Tag]]

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        t1, t2 = Tag(name="a"), Tag(name="b")
        article = Article(title="t")
        article.tags.extend([t1, t2])
        session.add_all([t1, t2, article])
        session.flush()

        validated = ArticleSchema.model_validate(article, from_attributes=True)
        assert validated.model_dump() == {"title": "t", "tags": [t1.id, t2.id]}


def test_idschema_self_sufficient_under_plain_from_attributes(sync_db):
    """``IDSchema`` keeps the nested ``{"id": ...}`` shape from a related row."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"), init=False)
        post: Mapped[Post] = relationship(default=None)

    class CommentSchema(fr.BaseSchema):
        content: str
        post: fr.IDSchema[Post]

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        post = Post(title="p1")
        comment = Comment(content="hi", post=post)
        session.add_all([post, comment])
        session.flush()

        validated = CommentSchema.model_validate(comment, from_attributes=True)
        assert validated.model_dump() == {"content": "hi", "post": {"id": post.id}}


# ---------------------------------------------------------------------------
# 2. ``to_response_schema`` output, called directly on a loaded row. Covers the
#    relationship-named form (read yields the related ROW), which the existing
#    e2e suite does not -- it only exercises scalar-named FK columns.
# ---------------------------------------------------------------------------


def test_to_response_schema_idref_relationship_serializes_flat(sync_db):
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"), init=False)
        post: Mapped[Post] = relationship(default=None)

    class CommentSchema(fr.IDSchema):
        content: str
        post: fr.IDRef[Post]

    class CommentView(fr.RestView):
        model = Comment
        schema = CommentSchema

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        post = Post(title="p1")
        comment = Comment(content="hi", post=post)
        session.add_all([post, comment])
        session.flush()

        dumped = CommentView().to_response_schema(comment).model_dump(mode="json")
        assert dumped == {"id": comment.id, "content": "hi", "post": post.id}


def test_to_response_schema_idschema_relationship_serializes_nested(sync_db):
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"), init=False)
        post: Mapped[Post] = relationship(default=None)

    class CommentSchema(fr.IDSchema):
        content: str
        post: fr.IDSchema[Post]

    class CommentView(fr.RestView):
        model = Comment
        schema = CommentSchema

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        post = Post(title="p1")
        comment = Comment(content="hi", post=post)
        session.add_all([post, comment])
        session.flush()

        dumped = CommentView().to_response_schema(comment).model_dump(mode="json")
        assert dumped == {
            "id": comment.id,
            "content": "hi",
            "post": {"id": post.id},
        }


def test_to_response_schema_nested_idschema_subclass_keeps_all_fields(sync_db):
    """The guard: an IDSchema *subclass* that adds fields is a nested schema, not
    a bare reference, so its row is serialized in full -- never collapsed to id."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"), init=False)
        post: Mapped[Post] = relationship(default=None)

    class PostSchema(fr.IDSchema):
        title: str

    class CommentSchema(fr.IDSchema):
        content: str
        post: PostSchema

    class CommentView(fr.RestView):
        model = Comment
        schema = CommentSchema

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        post = Post(title="p1")
        comment = Comment(content="hi", post=post)
        session.add_all([post, comment])
        session.flush()

        dumped = CommentView().to_response_schema(comment).model_dump(mode="json")
        assert dumped == {
            "id": comment.id,
            "content": "hi",
            "post": {"id": post.id, "title": "p1"},
        }


# ---------------------------------------------------------------------------
# 3. End-to-end through the real async read path. A relationship-named ``IDRef``
#    (read yields the related row) is eager-loaded for the response; a react-admin
#    view serializes a (scalar-named) ``IDRef`` flat through its own dumper.
# ---------------------------------------------------------------------------


def test_idref_relationship_field_serializes_flat_e2e(client):
    """Read path: a relationship-named ``IDRef[Post]`` field is eager-loaded and
    serialized to the flat id over real HTTP (list + detail)."""

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"), init=False)
        post: Mapped[Post] = relationship(default=None)

    class PostSchema(fr.IDSchema):
        title: str

    class CommentSchema(fr.IDSchema):
        content: str
        post: fr.IDRef[Post]

    @fr.include_view(client.app)
    class PostView(fr.AsyncRestView):
        prefix = "/posts"
        model = Post
        schema = PostSchema

    @fr.include_view(client.app)
    class CommentView(fr.AsyncRestView):
        prefix = "/comments"
        model = Comment
        schema = CommentSchema

    create_tables()

    async def insert():
        async with fr.open_async_session() as session:
            post = Post(title="p1")
            comment = Comment(content="hi", post=post)
            session.add_all([post, comment])
            await session.commit()
            return post.id, comment.id

    post_id, comment_id = asyncio.run(insert())

    fetched = client.get(f"/comments/{comment_id}").json()
    assert fetched["post"] == post_id

    listed = client.get("/comments/").json()
    assert listed[0]["post"] == post_id


def test_react_admin_serializes_idref_flat_e2e(client):
    """The react-admin serializer (``to_response_schema(...).model_dump``) renders
    an ``IDRef`` field as the flat id, same as the plain REST path."""

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_fk: Mapped[int] = mapped_column(ForeignKey("post.id"))

    class PostSchema(fr.IDSchema):
        title: str

    class CommentSchema(fr.IDSchema):
        content: str
        post_fk: fr.IDRef[Post]

    @fr.include_view(client.app)
    class PostView(fr.AsyncRestView):
        prefix = "/posts"
        model = Post
        schema = PostSchema

    @fr.include_view(client.app)
    class CommentView(fr.AsyncReactAdminView):
        prefix = "/comments"
        model = Comment
        schema = CommentSchema

    create_tables()

    p1 = client.post("/posts/", json={"title": "p1"}).json()
    client.post("/comments/", json={"content": "hi", "post_fk": p1["id"]})

    listed = client.get("/comments/").json()
    assert listed[0]["post_fk"] == p1["id"]
