"""A create keeps the foreign key the request supplied.

A many-to-one relationship the request did not supply still holds ``None``
after construction: the dataclass ``__init__`` assigns
``relationship(default=None)``, and the create plan passes ``None`` for an
omitted reference field or a required relationship argument. SQLAlchemy's
flush copies that ``None`` over the foreign key, so a create that sent
``post_id`` answered 201 with ``post_id`` null, or 409 on a NOT NULL column.
These tests pin that the supplied id survives, for every model declaration and
schema shape that reaches the path, through the sync and async object helpers
and over HTTP.
"""

import asyncio

import pytest
from fastapi import FastAPI
from sqlalchemy import ForeignKey
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.objects import async_make_new_object, make_new_object
from fastapi_restly.testing import RestlyTestClient
from fastapi_restly.views._base import build_create_plan

from .conftest import create_tables


def _fk_default_relationship_default_none():
    # Both sides of the pair are optional in __init__.
    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int | None] = mapped_column(ForeignKey("post.id"), default=None)
        post: Mapped[Post | None] = relationship(default=None)

    return Post, Comment, fr.DataclassBase.metadata


def _required_fk_relationship_default_none():
    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post] = relationship(default=None)

    return Post, Comment, fr.DataclassBase.metadata


def _required_fk_relationship_init_false():
    # Nothing assigns the relationship at construction, so only the None
    # for an omitted reference field can override the FK.
    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post] = relationship(init=False)

    return Post, Comment, fr.DataclassBase.metadata


def _required_fk_relationship_init_false_default_none():
    # __init__ assigns an init=False field's default too.
    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post] = relationship(init=False, default=None)

    return Post, Comment, fr.DataclassBase.metadata


def _fk_init_false_relationship_default_none():
    # The id is set after construction, on top of the None __init__
    # already assigned.
    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int | None] = mapped_column(ForeignKey("post.id"), init=False)
        post: Mapped[Post | None] = relationship(default=None)

    return Post, Comment, fr.DataclassBase.metadata


def _fk_default_relationship_required_init():
    # A required relationship argument: the plan passes None for it.
    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int | None] = mapped_column(ForeignKey("post.id"), default=None)
        post: Mapped[Post | None] = relationship()

    return Post, Comment, fr.DataclassBase.metadata


def _declarative_base():
    # No dataclass __init__: the plan's None for an omitted reference is the
    # only one.
    class Base(DeclarativeBase):
        pass

    class Post(Base):
        __tablename__ = "post"
        id: Mapped[int] = mapped_column(primary_key=True)
        title: Mapped[str]

    class Comment(Base):
        __tablename__ = "comment"
        id: Mapped[int] = mapped_column(primary_key=True)
        content: Mapped[str]
        post_id: Mapped[int | None] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post | None] = relationship()

    return Post, Comment, Base.metadata


DECLARATIONS = {
    "fk-default/relationship-default-none": _fk_default_relationship_default_none,
    "required-fk/relationship-default-none": _required_fk_relationship_default_none,
    "required-fk/relationship-init-false": _required_fk_relationship_init_false,
    "required-fk/relationship-init-false-default-none": (
        _required_fk_relationship_init_false_default_none
    ),
    "fk-init-false/relationship-default-none": (
        _fk_init_false_relationship_default_none
    ),
    "fk-default/relationship-required-init": _fk_default_relationship_required_init,
    "declarative-base": _declarative_base,
}


def _schema(
    shape: str, Post: type, base: type[fr.BaseSchema] = fr.BaseSchema
) -> type[fr.BaseSchema]:
    if shape == "plain":

        class PlainSchema(base):
            content: str
            post_id: int | None = None

        return PlainSchema
    if shape == "must-exist":

        class MustExistSchema(base):
            content: str
            post_id: fr.MustExist[int, Post]

        return MustExistSchema
    if shape == "plain+reference":

        class PlainThenReferenceSchema(base):
            content: str
            post_id: int | None = None
            post: fr.IDRef[Post] | None = None

        return PlainThenReferenceSchema
    if shape == "reference+plain":

        class ReferenceThenPlainSchema(base):
            content: str
            post: fr.IDRef[Post] | None = None
            post_id: int | None = None

        return ReferenceThenPlainSchema
    if shape == "must-exist+reference":

        class MustExistThenReferenceSchema(base):
            content: str
            post_id: fr.MustExist[int, Post]
            post: fr.IDRef[Post] | None = None

        return MustExistThenReferenceSchema
    if shape == "reference+must-exist":

        class ReferenceThenMustExistSchema(base):
            content: str
            post: fr.IDRef[Post] | None = None
            post_id: fr.MustExist[int, Post]

        return ReferenceThenMustExistSchema
    raise AssertionError(shape)


SHAPES = [
    "plain",
    "must-exist",
    "plain+reference",
    "reference+plain",
    "must-exist+reference",
    "reference+must-exist",
]

CASES = [
    pytest.param(declaration, shape, id=f"{declaration}-{shape}")
    for declaration in DECLARATIONS
    for shape in SHAPES
]


@pytest.mark.parametrize(("declaration", "shape"), CASES)
def test_create_keeps_supplied_fk_sync(sync_db, declaration, shape):
    engine, make_session = sync_db
    Post, Comment, metadata = DECLARATIONS[declaration]()
    schema_cls = _schema(shape, Post)
    metadata.create_all(engine)

    with make_session() as session:
        post = Post(title="p1")
        session.add(post)
        session.flush()

        comment = make_new_object(
            session, Comment, schema_cls(content="hi", post_id=post.id)
        )
        session.flush()
        session.expire(comment)
        assert comment.post_id == post.id
        assert comment.post is post


@pytest.mark.parametrize(("declaration", "shape"), CASES)
def test_create_keeps_supplied_fk_async(declaration, shape):
    Post, Comment, metadata = DECLARATIONS[declaration]()
    schema_cls = _schema(shape, Post)

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        make_session = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        async with make_session() as session:
            post = Post(title="p1")
            session.add(post)
            await session.flush()

            comment = await async_make_new_object(
                session, Comment, schema_cls(content="hi", post_id=post.id)
            )
            await session.flush()
            await session.refresh(comment, ["post_id", "post"])
            assert comment.post_id == post.id
            assert comment.post is post
        await engine.dispose()

    asyncio.run(run())


def test_plan_lists_the_relationships_the_input_did_not_supply():
    """Only an omitted relationship is unset after construction; an explicit
    ``null`` or an id the client sent for it stays."""
    Post, Comment, _ = _fk_default_relationship_default_none()
    schema_cls = _schema("plain+reference", Post)

    omitted = build_create_plan(Comment, schema_cls(content="hi", post_id=1))
    assert omitted.unsupplied_relationships == ["post"]

    explicit_null = build_create_plan(
        Comment, schema_cls(content="hi", post_id=None, post=None)
    )
    assert explicit_null.unsupplied_relationships == []

    sent = build_create_plan(Comment, schema_cls(content="hi", post=1))
    assert sent.unsupplied_relationships == []


def test_only_a_none_the_input_did_not_supply_is_unset(sync_db):
    """The omitted relationship is unset, so it takes no part in the flush. A
    ``null`` the client sent, or a row it referenced, stays on the object."""
    engine, make_session = sync_db
    Post, Comment, metadata = _fk_default_relationship_default_none()
    schema_cls = _schema("plain+reference", Post)
    metadata.create_all(engine)

    with make_session() as session:
        post = Post(title="p1")
        session.add(post)
        session.flush()

        omitted = make_new_object(
            session, Comment, schema_cls(content="hi", post_id=post.id)
        )
        assert "post" not in omitted.__dict__

        explicit_null = make_new_object(
            session, Comment, schema_cls(content="hi", post=None)
        )
        assert "post" in explicit_null.__dict__
        assert explicit_null.post is None

        sent = make_new_object(session, Comment, schema_cls(content="hi", post=post.id))
        assert sent.post is post

        session.flush()
        assert omitted.post_id == post.id
        assert explicit_null.post_id is None
        assert sent.post_id == post.id


@pytest.mark.parametrize("shape", ["plain+reference", "reference+plain"])
def test_async_view_create_keeps_supplied_fk(client, shape):
    """HTTP end to end, a plain ``post_id`` beside an omitted ``post``
    reference, in both declaration orders: 201 with the supplied ``post_id``,
    and the relationship reads back the same row."""
    Post, Comment, _ = _fk_default_relationship_default_none()

    class PostSchema(fr.IDSchema):
        title: str

    CommentSchema = _schema(shape, Post, base=fr.IDSchema)

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

    post = client.post("/posts/", json={"title": "p1"}).json()
    created = client.post(
        "/comments/",
        json={"content": "hi", "post_id": post["id"]},
        assert_status_code=201,
    ).json()
    assert created["post_id"] == post["id"]
    assert created["post"] == post["id"]

    fetched = client.get(f"/comments/{created['id']}").json()
    assert fetched["post_id"] == post["id"]
    assert fetched["post"] == post["id"]


@pytest.mark.parametrize("shape", ["plain+reference", "reference+plain"])
def test_sync_view_create_keeps_supplied_fk(sync_db, shape):
    """Sync parity for the HTTP end-to-end test above."""
    engine, _ = sync_db
    Post, Comment, _ = _fk_default_relationship_default_none()

    class PostSchema(fr.IDSchema):
        title: str

    CommentSchema = _schema(shape, Post, base=fr.IDSchema)

    app = FastAPI()

    @fr.include_view(app)
    class PostView(fr.RestView):
        prefix = "/posts"
        model = Post
        schema = PostSchema

    @fr.include_view(app)
    class CommentView(fr.RestView):
        prefix = "/comments"
        model = Comment
        schema = CommentSchema

    fr.DataclassBase.metadata.create_all(engine)
    client = RestlyTestClient(app)

    post = client.post("/posts/", json={"title": "p1"}).json()
    created = client.post(
        "/comments/",
        json={"content": "hi", "post_id": post["id"]},
        assert_status_code=201,
    ).json()
    assert created["post_id"] == post["id"]
    assert created["post"] == post["id"]

    fetched = client.get(f"/comments/{created['id']}").json()
    assert fetched["post_id"] == post["id"]
    assert fetched["post"] == post["id"]


def test_async_view_with_generated_schema_keeps_supplied_fk(client):
    """A view with no declared schema writes the FK column as a plain field.
    On a NOT NULL column the create used to answer 409."""
    Post, Comment, _ = _required_fk_relationship_default_none()

    @fr.include_view(client.app)
    class PostView(fr.AsyncRestView):
        prefix = "/posts"
        model = Post

    @fr.include_view(client.app)
    class CommentView(fr.AsyncRestView):
        prefix = "/comments"
        model = Comment

    create_tables()

    post = client.post("/posts/", json={"title": "p1"}).json()
    created = client.post(
        "/comments/",
        json={"content": "hi", "post_id": post["id"]},
        assert_status_code=201,
    ).json()
    assert created["post_id"] == post["id"]


def test_sync_view_with_generated_schema_keeps_supplied_fk(sync_db):
    """Sync parity for the generated-schema test above."""
    engine, _ = sync_db
    Post, Comment, _ = _required_fk_relationship_default_none()

    app = FastAPI()

    @fr.include_view(app)
    class PostView(fr.RestView):
        prefix = "/posts"
        model = Post

    @fr.include_view(app)
    class CommentView(fr.RestView):
        prefix = "/comments"
        model = Comment

    fr.DataclassBase.metadata.create_all(engine)
    client = RestlyTestClient(app)

    post = client.post("/posts/", json={"title": "p1"}).json()
    created = client.post(
        "/comments/",
        json={"content": "hi", "post_id": post["id"]},
        assert_status_code=201,
    ).json()
    assert created["post_id"] == post["id"]
