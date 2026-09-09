"""``save_object`` / ``async_save_object`` refresh ``obj`` without the cascade.

A plain ``session.refresh(obj)`` runs the ``refresh-expire`` cascade that
``cascade="all"`` implies: every related object is expired and not reloaded,
which under ``AsyncSession`` turns the next attribute access on a child into
``MissingGreenlet``. The helpers refresh by attribute name instead. These pin
that related objects keep their loaded state, and that ``obj`` itself ends up
as a plain refresh leaves it: columns from the row, an eager relationship
reloaded, a lazy relationship unloaded so a changed foreign key is not served
from a stale relationship.
"""

import asyncio

import sqlalchemy as sa
from sqlalchemy import ForeignKey
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.objects import async_save_object, save_object


def _models():
    class Author(fr.IDBase):
        name: Mapped[str]

    class Post(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
        author: Mapped[Author] = relationship(init=False)
        comments: Mapped[list["Comment"]] = relationship(
            back_populates="post", default_factory=list, cascade="all, delete-orphan"
        )
        tags: Mapped[list["Tag"]] = relationship(
            default_factory=list, cascade="all, delete-orphan", lazy="selectin"
        )

    class Comment(fr.IDBase):
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"), init=False)
        body: Mapped[str]
        post: Mapped[Post] = relationship(back_populates="comments", init=False)

    class Tag(fr.IDBase):
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"), init=False)
        label: Mapped[str]

    return Author, Post, Comment, Tag


def test_sync_save_leaves_cascaded_children_loaded(sync_db):
    engine, make_session = sync_db
    Author, Post, Comment, Tag = _models()
    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        alice = Author(name="alice")
        session.add(alice)
        session.flush()
        post = Post(
            title="t",
            author_id=alice.id,
            comments=[Comment(body="c")],
            tags=[Tag(label="x")],
        )
        session.add(post)
        session.flush()
        comment, tag = post.comments[0], post.tags[0]

        post.title = "t2"
        save_object(session, post)

        # the children behind cascade="all" are untouched by the refresh
        assert sa.inspect(comment).expired_attributes == set()
        assert sa.inspect(tag).expired_attributes == set()
        # obj itself: columns from the row, the eager relationship reloaded,
        # the lazy relationship unloaded
        assert post.title == "t2"
        assert "tags" not in sa.inspect(post).unloaded
        assert "comments" in sa.inspect(post).unloaded


def test_sync_save_does_not_serve_a_stale_lazy_relationship(sync_db):
    engine, make_session = sync_db
    Author, Post, Comment, Tag = _models()
    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        alice, bob = Author(name="alice"), Author(name="bob")
        session.add_all([alice, bob])
        session.flush()
        post = Post(title="t", author_id=alice.id)
        session.add(post)
        session.flush()
        assert post.author is alice  # loaded, now stale once the key changes

        post.author_id = bob.id
        save_object(session, post)

        assert "author" in sa.inspect(post).unloaded
        assert post.author is bob


def test_async_save_leaves_cascaded_children_loaded():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        make_session = async_sessionmaker(bind=engine, expire_on_commit=False)
        Author, Post, Comment, Tag = _models()
        async with engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)

        async with make_session() as session:
            alice = Author(name="alice")
            session.add(alice)
            await session.flush()
            post = Post(
                title="t",
                author_id=alice.id,
                comments=[Comment(body="c")],
                tags=[Tag(label="x")],
            )
            session.add(post)
            await session.flush()
            comment, tag = post.comments[0], post.tags[0]

            post.title = "t2"
            await async_save_object(session, post)

            # a plain refresh would have expired these; reading them here,
            # outside any greenlet, would then raise MissingGreenlet
            assert sa.inspect(comment).expired_attributes == set()
            assert comment.body == "c"
            assert tag.label == "x"
            assert post.title == "t2"
            assert "tags" not in sa.inspect(post).unloaded
            assert "comments" in sa.inspect(post).unloaded
        await engine.dispose()

    asyncio.run(run())
