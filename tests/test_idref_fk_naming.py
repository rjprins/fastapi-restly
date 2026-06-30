"""IDRef foreign-key fields must work under any column name, not only ``_id``
(ticket z0oy).

Resolution keys on the field's *type* (``IDSchema``), so a reference is resolved
to an ORM row regardless of the field name. Routing the resolved row onto the
object used to key on the *name* (``field_name.endswith("_id")``), so a FK column
named anything else (``post_fk``, ``linked_post``, ...) was misrouted: the ORM
object was assigned into the integer FK column and the request blew up at flush
instead of validation. These tests pin the mapper-introspection routing that
fixes it, for both the sync and async write paths (which share the helpers).
"""

import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import ForeignKey
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.objects import (
    async_make_new_object,
    async_update_object,
    make_new_object,
    update_object,
)
from fastapi_restly.views._base import (
    build_create_plan,
    validate_resolved_reference_consistency,
)

from .conftest import create_tables


def _make_async_engine_and_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    make_session = async_sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False
    )
    return engine, make_session


def test_async_non_id_fk_field_create_update_get_e2e(client):
    """The headline bug: a ``post_fk: IDRef[Post]`` field (FK column not ending
    in ``_id``) must create, update, and serialize like the ``_id`` form does."""

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        # FK column whose name does NOT end in "_id".
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
    class CommentView(fr.AsyncRestView):
        prefix = "/comments"
        model = Comment
        schema = CommentSchema

    create_tables()

    p1 = client.post("/posts/", json={"title": "p1"}).json()
    p2 = client.post("/posts/", json={"title": "p2"}).json()

    created = client.post(
        "/comments/", json={"content": "hi", "post_fk": p1["id"]}
    ).json()
    assert created["post_fk"] == p1["id"]

    fetched = client.get(f"/comments/{created['id']}").json()
    assert fetched["post_fk"] == p1["id"]

    updated = client.patch(
        f"/comments/{created['id']}", json={"post_fk": p2["id"]}
    ).json()
    assert updated["post_fk"] == p2["id"]

    # A reference to a non-existent row still 404s by id, same as the _id form.
    client.post(
        "/comments/",
        json={"content": "bad", "post_fk": 99999},
        assert_status_code=404,
    )


def test_async_non_id_fk_object_helpers_populate_column_and_relationship():
    """Async write path: the resolved row's id lands on the FK column and the
    partner relationship (derived from the mapper, not the name) is populated."""

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_fk: Mapped[int] = mapped_column(ForeignKey("post.id"))
        # Partner relationship whose name has no textual relation to "post_fk".
        post: Mapped[Post] = relationship(default=None)

    class CommentSchema(fr.BaseSchema):
        content: str
        post_fk: fr.IDRef[Post]

    async def run():
        engine, make_session = _make_async_engine_and_session()
        async with engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)
        async with make_session() as session:
            p1 = Post(title="p1")
            p2 = Post(title="p2")
            session.add_all([p1, p2])
            await session.flush()

            comment = await async_make_new_object(
                session, Comment, CommentSchema(content="hi", post_fk=p1.id)
            )
            await session.flush()
            assert comment.post_fk == p1.id
            assert comment.post is p1  # convenience pairing, by mapper not by name

            await async_update_object(
                session, comment, CommentSchema(content="hi", post_fk=p2.id)
            )
            assert comment.post_fk == p2.id
            assert comment.post is p2
        await engine.dispose()

    asyncio.run(run())


def test_sync_non_id_fk_object_helpers_populate_column_and_relationship(sync_db):
    """Sync parity with the async write path above."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_fk: Mapped[int] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post] = relationship(default=None)

    class CommentSchema(fr.BaseSchema):
        content: str
        post_fk: fr.IDRef[Post]

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        p1 = Post(title="p1")
        p2 = Post(title="p2")
        session.add_all([p1, p2])
        session.flush()

        comment = make_new_object(
            session, Comment, CommentSchema(content="hi", post_fk=p1.id)
        )
        session.flush()
        assert comment.post_fk == p1.id
        assert comment.post is p1

        update_object(
            session, comment, CommentSchema(content="hi", post_fk=p2.id)
        )
        assert comment.post_fk == p2.id
        assert comment.post is p2


def test_arbitrary_fk_column_name_routes_by_mapper(sync_db):
    """Not special-cased to ``_fk`` either: a column with an unrelated name
    (``linked_post``) and no partner relationship still gets the raw id."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        linked_post: Mapped[int] = mapped_column(ForeignKey("post.id"))

    class CommentSchema(fr.BaseSchema):
        content: str
        linked_post: fr.IDRef[Post]

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        post = Post(title="p1")
        session.add(post)
        session.flush()

        plan = build_create_plan(
            Comment,
            CommentSchema(content="hi", linked_post=post.id),
            CommentSchema,
            resolved={"linked_post": post},
        )
        assert plan.kwargs["linked_post"] == post.id  # the id, not the object
        assert "linked_post" not in plan.post_assignments

        comment = make_new_object(
            session, Comment, CommentSchema(content="hi", linked_post=post.id)
        )
        session.flush()  # would raise ProgrammingError if the object were misrouted
        assert comment.linked_post == post.id


def test_non_id_fk_conflict_detection_is_name_independent(sync_db):
    """The both-supplied consistency check pairs the FK column with its partner
    relationship via the mapper, so it fires for non-``_id`` FK names too."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_fk: Mapped[int] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post] = relationship(default=None)

    class CommentSchema(fr.BaseSchema):
        content: str
        post_fk: fr.IDRef[Post]
        post: fr.IDSchema[Post]

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        p1 = Post(title="p1")
        p2 = Post(title="p2")
        session.add_all([p1, p2])
        session.flush()

        # Agreeing references: no error.
        validate_resolved_reference_consistency(
            Comment,
            CommentSchema(content="ok", post_fk=p1.id, post={"id": p1.id}),
            CommentSchema,
            resolved={"post_fk": p1, "post": p1},
        )

        # Conflicting references: 422, exactly like the _id form.
        with pytest.raises(HTTPException) as exc:
            validate_resolved_reference_consistency(
                Comment,
                CommentSchema(content="bad", post_fk=p1.id, post={"id": p2.id}),
                CommentSchema,
                resolved={"post_fk": p1, "post": p2},
            )
        assert exc.value.status_code == 422


def test_renamed_db_column_fk_field_sync(sync_db):
    """A FK column declared with an explicit DB name (``mapped_column("db", ...)``)
    where the Python attribute name differs from the DB column name. Routing
    works in attribute-name space, so the FK is written and the partner
    relationship synced — it must not leave the relationship at its default and
    NULL the FK at flush."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        # Python attribute is `post_id`; the DB column is named `post_fk`.
        post_id: Mapped[int] = mapped_column("post_fk", ForeignKey("post.id"))
        post: Mapped[Post] = relationship(default=None)

    class CommentSchema(fr.BaseSchema):
        content: str
        post_id: fr.IDRef[Post]

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        p1 = Post(title="p1")
        p2 = Post(title="p2")
        session.add_all([p1, p2])
        session.flush()

        comment = make_new_object(
            session, Comment, CommentSchema(content="hi", post_id=p1.id)
        )
        session.flush()  # would raise IntegrityError if the relationship NULLed the FK
        assert comment.post_id == p1.id
        assert comment.post is p1

        update_object(
            session, comment, CommentSchema(content="hi", post_id=p2.id)
        )
        session.flush()
        assert comment.post_id == p2.id
        assert comment.post is p2


def test_renamed_db_column_fk_field_async():
    """Async parity for the renamed-DB-column FK field above."""

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column("post_fk", ForeignKey("post.id"))
        post: Mapped[Post] = relationship(default=None)

    class CommentSchema(fr.BaseSchema):
        content: str
        post_id: fr.IDRef[Post]

    async def run():
        engine, make_session = _make_async_engine_and_session()
        async with engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)
        async with make_session() as session:
            p1 = Post(title="p1")
            p2 = Post(title="p2")
            session.add_all([p1, p2])
            await session.flush()

            comment = await async_make_new_object(
                session, Comment, CommentSchema(content="hi", post_id=p1.id)
            )
            await session.flush()
            assert comment.post_id == p1.id
            assert comment.post is p1

            await async_update_object(
                session, comment, CommentSchema(content="hi", post_id=p2.id)
            )
            await session.flush()
            assert comment.post_id == p2.id
            assert comment.post is p2
        await engine.dispose()

    asyncio.run(run())


def test_renamed_db_column_relationship_field_sync(sync_db):
    """Exposing the relationship when its local FK column has an explicit DB name:
    the mirrored FK must target the mapped attribute, not the DB column key."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(
            "post_fk", ForeignKey("post.id"), init=False
        )
        post: Mapped[Post] = relationship(default=None)

    class CommentSchema(fr.BaseSchema):
        content: str
        post: fr.IDRef[Post]

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        p1 = Post(title="p1")
        session.add(p1)
        session.flush()

        comment = make_new_object(
            session, Comment, CommentSchema(content="hi", post=p1.id)
        )
        session.flush()
        assert comment.post is p1
        assert comment.post_id == p1.id  # FK mirrored onto the mapped attribute
