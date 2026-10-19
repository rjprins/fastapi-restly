"""``MustExist``: existence-checked scalar foreign keys.

``MustExist[int, Post]`` is a checked ``int`` foreign key -- a plain scalar
everywhere (wire, column, ``data.<field>``) plus a batched existence check on
write (404 on a missing id). ``MustExist[UUID, Account]`` covers a non-int pk.
Unlike ``IDRef`` / ``IDSchema`` it is NOT a wrapper.

These pin: the desugaring (non-int pk), the scalar identity, that the write
path does not treat it as a reference, the batched check (sync + async, one
query per model), that the model must be named, optional fields, the removed
public ``RefExists``, and the e2e flat wire.
"""

import asyncio
from typing import get_args
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import ForeignKey, Uuid, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import (
    Mapped,
    ORMExecuteState,
    Session,
    mapped_column,
    relationship,
    with_loader_criteria,
)

import fastapi_restly as fr
from fastapi_restly.objects import async_make_new_object, make_new_object, update_object
from fastapi_restly.schemas._base import (
    RefExists,
    create_model_without_read_only_fields,
    is_reference_annotation,
)

from ._models import SharedBase
from .conftest import create_tables

# ---------------------------------------------------------------------------
# Desugaring + scalar identity (no DB)
# ---------------------------------------------------------------------------


def test_mustexist_desugars_to_checked_scalar():
    class Post(fr.IDMixin, SharedBase):
        title: Mapped[str]

    pk_type, marker = get_args(fr.MustExist[int, Post])
    assert pk_type is int
    assert isinstance(marker, RefExists)
    assert marker.model is Post


def test_mustexist_needs_the_model():
    # the same schema can write another model, so a model taken from a
    # foreign key could check the wrong table
    with pytest.raises(TypeError, match=r"MustExist\[int, Post\]"):
        fr.MustExist[int]  # type: ignore[misc]
    with pytest.raises(TypeError, match="the model class"):
        fr.MustExist[int, "Post"]  # type: ignore[misc]


def test_ref_exists_is_not_public():
    with pytest.raises(AttributeError, match=r"fr\.MustExist\[int, Post\]"):
        fr.RefExists  # type: ignore[attr-defined]  # noqa: B018
    with pytest.raises(AttributeError, match="authorize"):
        fr.schemas.RefExists  # type: ignore[attr-defined]  # noqa: B018


def test_mustexist_non_int_pk_type_is_the_first_arg():
    class Account(SharedBase):
        __tablename__ = "mustexist_desugar_account"
        id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid4)

    pk_type, marker = get_args(fr.MustExist[UUID, Account])
    assert pk_type is UUID
    assert marker.model is Account


def test_mustexist_field_stays_scalar_and_is_not_a_reference():
    class Post(fr.IDMixin, SharedBase):
        title: Mapped[str]

    class CommentSchema(fr.BaseSchema):
        content: str
        post_id: fr.MustExist[int, Post]

    field_info = CommentSchema.model_fields["post_id"]
    assert field_info.annotation is int
    assert any(isinstance(m, RefExists) for m in field_info.metadata)

    # NOT a wrapper: data.post_id is the int itself, not a `.id` wrapper.
    schema_obj = CommentSchema(content="hi", post_id=5)
    assert schema_obj.post_id == 5
    assert isinstance(schema_obj.post_id, int)

    # The write path must NOT route it as a reference field.
    assert is_reference_annotation(field_info.annotation) is False


# ---------------------------------------------------------------------------
# Existence check (sync)
# ---------------------------------------------------------------------------


def _post_comment_models():
    class Post(fr.IDMixin, SharedBase):
        title: Mapped[str]

    class Comment(fr.IDMixin, SharedBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

    class CommentSchema(fr.BaseSchema):
        content: str
        post_id: fr.MustExist[int, Post]

    return Post, Comment, CommentSchema


def test_sync_existing_id_resolves_and_stays_scalar(sync_db):
    engine, make_session = sync_db
    Post, Comment, CommentSchema = _post_comment_models()
    SharedBase.metadata.create_all(engine)

    with make_session() as session:
        post = Post(title="p")
        session.add(post)
        session.flush()

        comment = make_new_object(
            session, Comment, CommentSchema(content="hi", post_id=post.id)
        )
        session.flush()
        assert comment.post_id == post.id
        assert isinstance(comment.post_id, int)


def test_sync_missing_id_404s_naming_field_and_id(sync_db):
    engine, make_session = sync_db
    Post, Comment, CommentSchema = _post_comment_models()
    SharedBase.metadata.create_all(engine)

    with make_session() as session:
        with pytest.raises(HTTPException) as exc:
            make_new_object(session, Comment, CommentSchema(content="x", post_id=99999))
        assert exc.value.status_code == 404
        assert "post_id" in str(exc.value.detail)
        assert "99999" in str(exc.value.detail)


def test_sync_update_also_checks_existence(sync_db):
    engine, make_session = sync_db
    Post, Comment, CommentSchema = _post_comment_models()
    SharedBase.metadata.create_all(engine)

    with make_session() as session:
        post = Post(title="p")
        session.add(post)
        session.flush()
        comment = make_new_object(
            session, Comment, CommentSchema(content="hi", post_id=post.id)
        )
        session.flush()

        with pytest.raises(HTTPException) as exc:
            update_object(session, comment, CommentSchema(content="hi", post_id=88888))
        assert exc.value.status_code == 404


def test_sync_explicit_model_second_arg(sync_db):
    """``MustExist[int, Post]`` spells the model out instead of inferring it."""
    engine, make_session = sync_db

    class Post(fr.IDMixin, SharedBase):
        title: Mapped[str]

    class Comment(fr.IDMixin, SharedBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

    class CommentSchema(fr.BaseSchema):
        content: str
        post_id: fr.MustExist[int, Post]

    SharedBase.metadata.create_all(engine)

    with make_session() as session:
        post = Post(title="p")
        session.add(post)
        session.flush()
        comment = make_new_object(
            session, Comment, CommentSchema(content="hi", post_id=post.id)
        )
        session.flush()
        assert comment.post_id == post.id

        with pytest.raises(HTTPException) as exc:
            make_new_object(session, Comment, CommentSchema(content="x", post_id=404))
        assert exc.value.status_code == 404


def test_sync_check_is_batched_one_query_per_model(sync_db):
    """Two ``MustExist[int, Post]`` fields to the same model trigger a single
    ``SELECT ... WHERE id IN (...)`` -- no N+1."""
    engine, make_session = sync_db

    class Post(fr.IDMixin, SharedBase):
        title: Mapped[str]

    class Pairing(fr.IDMixin, SharedBase):
        left_id: Mapped[int] = mapped_column(ForeignKey("post.id"))
        right_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

    class PairingSchema(fr.BaseSchema):
        left_id: fr.MustExist[int, Post]
        right_id: fr.MustExist[int, Post]

    SharedBase.metadata.create_all(engine)

    with make_session() as session:
        p1, p2 = Post(title="a"), Post(title="b")
        session.add_all([p1, p2])
        session.flush()

        post_selects: list[str] = []

        def _record(conn, cursor, statement, parameters, context, executemany):
            normalized = statement.lstrip().lower()
            if normalized.startswith("select") and "post" in normalized:
                post_selects.append(statement)

        event.listen(engine, "before_cursor_execute", _record)
        try:
            make_new_object(
                session, Pairing, PairingSchema(left_id=p1.id, right_id=p2.id)
            )
        finally:
            event.remove(engine, "before_cursor_execute", _record)

        assert len(post_selects) == 1


def _restrict_to_tenant(model, tenant: int):
    """A session rule the way an application adds one: SQLAlchemy's
    ``with_loader_criteria`` from a ``do_orm_execute`` listener."""

    def restrict(state: ORMExecuteState) -> None:
        if state.is_select and any(m.class_ is model for m in state.all_mappers):
            state.statement = state.statement.options(
                with_loader_criteria(
                    model, lambda cls: cls.tenant_id == tenant, include_aliases=True
                )
            )

    return restrict


def test_sync_check_is_an_orm_statement_so_a_session_rule_applies(sync_db):
    """The check selects the mapped primary-key attribute, not the Core
    column, so a ``with_loader_criteria`` rule reaches it like any read."""
    engine, make_session = sync_db

    class Post(fr.IDMixin, SharedBase):
        tenant_id: Mapped[int]

    class Comment(fr.IDMixin, SharedBase):
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

    class CommentSchema(fr.BaseSchema):
        post_id: fr.MustExist[int, Post]

    SharedBase.metadata.create_all(engine)
    restrict = _restrict_to_tenant(Post, tenant=1)

    with make_session() as session:
        ours, theirs = Post(tenant_id=1), Post(tenant_id=2)
        session.add_all([ours, theirs])
        session.flush()

        event.listen(Session, "do_orm_execute", restrict)
        try:
            make_new_object(session, Comment, CommentSchema(post_id=ours.id))
            with pytest.raises(HTTPException) as exc:
                make_new_object(session, Comment, CommentSchema(post_id=theirs.id))
            assert exc.value.status_code == 404
        finally:
            event.remove(Session, "do_orm_execute", restrict)


def test_async_check_is_an_orm_statement_so_a_session_rule_applies():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        make_session = async_sessionmaker(bind=engine, expire_on_commit=False)

        class Post(fr.IDMixin, SharedBase):
            tenant_id: Mapped[int]

        class Comment(fr.IDMixin, SharedBase):
            post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

        class CommentSchema(fr.BaseSchema):
            post_id: fr.MustExist[int, Post]

        async with engine.begin() as conn:
            await conn.run_sync(SharedBase.metadata.create_all)
        restrict = _restrict_to_tenant(Post, tenant=1)

        async with make_session() as session:
            ours, theirs = Post(tenant_id=1), Post(tenant_id=2)
            session.add_all([ours, theirs])
            await session.flush()

            event.listen(Session, "do_orm_execute", restrict)
            try:
                await async_make_new_object(
                    session, Comment, CommentSchema(post_id=ours.id)
                )
                with pytest.raises(HTTPException) as exc:
                    await async_make_new_object(
                        session, Comment, CommentSchema(post_id=theirs.id)
                    )
                assert exc.value.status_code == 404
            finally:
                event.remove(Session, "do_orm_execute", restrict)
        await engine.dispose()

    asyncio.run(run())


def test_sync_uuid_pk_existence_check(sync_db):
    """A UUID FK: ``MustExist[UUID, Account]``, same as the int case."""
    engine, make_session = sync_db

    class Account(SharedBase):
        __tablename__ = "mustexist_uuid_account"
        id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid4)

    class Membership(fr.IDMixin, SharedBase):
        account_id: Mapped[UUID] = mapped_column(
            Uuid, ForeignKey("mustexist_uuid_account.id")
        )

    class MembershipSchema(fr.BaseSchema):
        account_id: fr.MustExist[UUID, Account]

    SharedBase.metadata.create_all(engine)

    with make_session() as session:
        account = Account()
        session.add(account)
        session.flush()

        member = make_new_object(
            session, Membership, MembershipSchema(account_id=account.id)
        )
        session.flush()
        assert member.account_id == account.id
        assert isinstance(member.account_id, UUID)

        with pytest.raises(HTTPException) as exc:
            make_new_object(session, Membership, MembershipSchema(account_id=uuid4()))
        assert exc.value.status_code == 404


def test_optional_mustexist_checks_when_provided_and_skips_none(sync_db):
    """``MustExist[int, Post] | None`` buries the marker in the union, but the check is
    recovered from the annotation: a provided id is still verified, ``None`` is
    skipped."""
    engine, make_session = sync_db

    class Post(fr.IDMixin, SharedBase):
        title: Mapped[str]

    class Comment(fr.IDMixin, SharedBase):
        content: Mapped[str]
        post_id: Mapped[int | None] = mapped_column(ForeignKey("post.id"), default=None)

    class CommentSchema(fr.BaseSchema):
        content: str
        post_id: fr.MustExist[int, Post] | None = None

    SharedBase.metadata.create_all(engine)

    with make_session() as session:
        # None -> no check, no error.
        none_comment = make_new_object(
            session, Comment, CommentSchema(content="hi", post_id=None)
        )
        session.flush()
        assert none_comment.post_id is None

        # Provided but missing -> still 404 (marker recovered from the union).
        with pytest.raises(HTTPException) as exc:
            make_new_object(session, Comment, CommentSchema(content="x", post_id=55555))
        assert exc.value.status_code == 404

        # Provided and existing -> resolves.
        post = Post(title="p")
        session.add(post)
        session.flush()
        ok_comment = make_new_object(
            session, Comment, CommentSchema(content="ok", post_id=post.id)
        )
        session.flush()
        assert ok_comment.post_id == post.id


# ---------------------------------------------------------------------------
# Async parity
# ---------------------------------------------------------------------------


def test_async_existence_check_parity():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        make_session = async_sessionmaker(bind=engine, expire_on_commit=False)

        class Post(fr.IDMixin, SharedBase):
            title: Mapped[str]

        class Comment(fr.IDMixin, SharedBase):
            content: Mapped[str]
            post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

        class CommentSchema(fr.BaseSchema):
            content: str
            post_id: fr.MustExist[int, Post]

        async with engine.begin() as conn:
            await conn.run_sync(SharedBase.metadata.create_all)
        async with make_session() as session:
            post = Post(title="p")
            session.add(post)
            await session.flush()

            comment = await async_make_new_object(
                session, Comment, CommentSchema(content="hi", post_id=post.id)
            )
            await session.flush()
            assert comment.post_id == post.id

            with pytest.raises(HTTPException) as exc:
                await async_make_new_object(
                    session, Comment, CommentSchema(content="x", post_id=77777)
                )
            assert exc.value.status_code == 404
        await engine.dispose()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# End-to-end wire shape (flat scalar both directions; clean 404)
# ---------------------------------------------------------------------------


def test_mustexist_e2e_flat_wire_and_404(client):
    class Post(fr.IDMixin, SharedBase):
        title: Mapped[str]

    class Comment(fr.IDMixin, SharedBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

    class PostSchema(fr.IDSchema):
        title: str

    class CommentSchema(fr.IDSchema):
        content: str
        post_id: fr.MustExist[int, Post]

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

    created = client.post(
        "/comments/", json={"content": "hi", "post_id": p1["id"]}
    ).json()
    assert created["post_id"] == p1["id"]  # flat scalar on the wire

    fetched = client.get(f"/comments/{created['id']}").json()
    assert fetched["post_id"] == p1["id"]

    # A missing id is a clean 404 at validation, not a flush-time IntegrityError.
    client.post(
        "/comments/", json={"content": "x", "post_id": 99999}, assert_status_code=404
    )


def test_mustexist_with_read_only_relationship_keeps_foreign_key(sync_db):
    """The dataclass relationship shape documented for nested responses keeps
    the scalar foreign key written by ``MustExist``."""

    engine, make_session = sync_db

    class Writer(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Book(fr.IDMixin, SharedBase):
        title: Mapped[str]
        writer_id: Mapped[int] = mapped_column(ForeignKey("writer.id"))
        writer: Mapped[Writer] = relationship(init=False)

    class WriterSchema(fr.IDSchema):
        name: str

    class BookSchema(fr.IDSchema):
        title: str
        writer_id: fr.MustExist[int, Writer]
        writer: fr.ReadOnly[WriterSchema]

    BookCreate = create_model_without_read_only_fields(BookSchema)

    SharedBase.metadata.create_all(engine)

    with make_session() as session:
        writer = Writer(name="Octavia")
        session.add(writer)
        session.flush()

        book = make_new_object(
            session, Book, BookCreate(title="Kindred", writer_id=writer.id)
        )
        session.flush()

        assert book.writer_id == writer.id
