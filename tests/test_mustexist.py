"""``MustExist``: existence-checked scalar foreign keys.

``MustExist[int]`` is a checked ``int`` foreign key -- a plain scalar everywhere
(wire, column, ``data.<field>``) plus a batched existence check on write (404 on a
missing id), with the target model inferred from the column's ``ForeignKey``.
``MustExist[int, Post]`` names the model explicitly; ``MustExist[UUID, Account]``
covers a non-int pk. Unlike ``IDRef`` / ``IDSchema`` it is NOT a wrapper.

These pin: the desugaring (inferred vs explicit model, non-int pk), the scalar
identity, that the write path does not treat it as a reference, the batched check
(sync + async, one query per model), the FK-model inference (and its clear failure
on a non-FK column), optional fields, the explicit marker form, and the e2e flat
wire.
"""

import asyncio
from typing import Annotated, get_args
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import ForeignKey, Uuid, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError
from fastapi_restly.objects import async_make_new_object, make_new_object, update_object
from fastapi_restly.schemas._base import RefExists, _Infer, is_reference_annotation

from .conftest import create_tables

# ---------------------------------------------------------------------------
# Desugaring + scalar identity (no DB)
# ---------------------------------------------------------------------------


def test_mustexist_desugars_to_checked_scalar():
    class Post(fr.IDBase):
        title: Mapped[str]

    # One arg: pk type in the slot, model left to infer.
    pk_type, marker = get_args(fr.MustExist[int])
    assert pk_type is int
    assert isinstance(marker, RefExists)
    assert marker.model is _Infer

    # Two args: pk type + explicit model.
    pk_type2, marker2 = get_args(fr.MustExist[int, Post])
    assert pk_type2 is int
    assert marker2.model is Post


def test_mustexist_non_int_pk_type_is_the_first_arg():
    class Account(fr.DataclassBase):
        __tablename__ = "mustexist_desugar_account"
        id: Mapped[UUID] = mapped_column(
            Uuid, primary_key=True, default_factory=uuid4
        )

    pk_type, marker = get_args(fr.MustExist[UUID, Account])
    assert pk_type is UUID
    assert marker.model is Account


def test_mustexist_field_stays_scalar_and_is_not_a_reference():
    class Post(fr.IDBase):
        title: Mapped[str]

    class CommentSchema(fr.BaseSchema):
        content: str
        post_id: fr.MustExist[int]

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
# Existence check (sync) -- model inferred from the column's ForeignKey
# ---------------------------------------------------------------------------


def _post_comment_models():
    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

    class CommentSchema(fr.BaseSchema):
        content: str
        post_id: fr.MustExist[int]  # target Post inferred from the FK

    return Post, Comment, CommentSchema


def test_sync_existing_id_resolves_and_stays_scalar(sync_db):
    engine, make_session = sync_db
    Post, Comment, CommentSchema = _post_comment_models()
    fr.DataclassBase.metadata.create_all(engine)

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
    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        with pytest.raises(HTTPException) as exc:
            make_new_object(session, Comment, CommentSchema(content="x", post_id=99999))
        assert exc.value.status_code == 404
        assert "post_id" in str(exc.value.detail)
        assert "99999" in str(exc.value.detail)


def test_sync_update_also_checks_existence(sync_db):
    engine, make_session = sync_db
    Post, Comment, CommentSchema = _post_comment_models()
    fr.DataclassBase.metadata.create_all(engine)

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

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

    class CommentSchema(fr.BaseSchema):
        content: str
        post_id: fr.MustExist[int, Post]

    fr.DataclassBase.metadata.create_all(engine)

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


def test_sync_infer_fails_clearly_on_non_fk_column(sync_db):
    """``MustExist[int]`` on a field that does not map to a single-FK column raises
    a configuration error steering to the explicit form."""
    engine, make_session = sync_db

    class Widget(fr.IDBase):
        count: Mapped[int]  # a plain int column, no ForeignKey

    class WidgetSchema(fr.BaseSchema):
        count: fr.MustExist[int]

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        with pytest.raises(RestlyConfigurationError) as exc:
            make_new_object(session, Widget, WidgetSchema(count=1))
        assert "count" in str(exc.value)
        assert "MustExist[<pk>, <Model>]" in str(exc.value)


def test_sync_check_is_batched_one_query_per_model(sync_db):
    """Two inferred ``MustExist[int]`` fields to the same model trigger a single
    ``SELECT ... WHERE id IN (...)`` -- no N+1."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Pairing(fr.IDBase):
        left_id: Mapped[int] = mapped_column(ForeignKey("post.id"))
        right_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

    class PairingSchema(fr.BaseSchema):
        left_id: fr.MustExist[int]
        right_id: fr.MustExist[int]

    fr.DataclassBase.metadata.create_all(engine)

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


def test_sync_uuid_pk_existence_check(sync_db):
    """A UUID FK: ``MustExist[UUID]`` infers the model, same as the int case."""
    engine, make_session = sync_db

    class Account(fr.DataclassBase):
        __tablename__ = "mustexist_uuid_account"
        id: Mapped[UUID] = mapped_column(
            Uuid, primary_key=True, default_factory=uuid4
        )

    class Membership(fr.IDBase):
        account_id: Mapped[UUID] = mapped_column(
            Uuid, ForeignKey("mustexist_uuid_account.id")
        )

    class MembershipSchema(fr.BaseSchema):
        account_id: fr.MustExist[UUID]

    fr.DataclassBase.metadata.create_all(engine)

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


def test_explicit_marker_form_behaves_like_mustexist(sync_db):
    """``Annotated[int, RefExists(Model)]`` (no sugar) is equivalent."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

    class CommentSchema(fr.BaseSchema):
        content: str
        post_id: Annotated[int, RefExists(Post)]

    fr.DataclassBase.metadata.create_all(engine)

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
            make_new_object(session, Comment, CommentSchema(content="x", post_id=12345))
        assert exc.value.status_code == 404


def test_optional_mustexist_checks_when_provided_and_skips_none(sync_db):
    """``MustExist[int] | None`` buries the marker in the union, but the check is
    recovered from the annotation: a provided id is still verified, ``None`` is
    skipped."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int | None] = mapped_column(
            ForeignKey("post.id"), default=None
        )

    class CommentSchema(fr.BaseSchema):
        content: str
        post_id: fr.MustExist[int] | None = None

    fr.DataclassBase.metadata.create_all(engine)

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

        class Post(fr.IDBase):
            title: Mapped[str]

        class Comment(fr.IDBase):
            content: Mapped[str]
            post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

        class CommentSchema(fr.BaseSchema):
            content: str
            post_id: fr.MustExist[int]

        async with engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)
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
    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

    class PostSchema(fr.IDSchema):
        title: str

    class CommentSchema(fr.IDSchema):
        content: str
        post_id: fr.MustExist[int]

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
        "/comments/",
        json={"content": "x", "post_id": 99999},
        assert_status_code=404,
    )
