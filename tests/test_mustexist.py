"""``MustExist`` / ``RefExists``: existence-checked scalar foreign keys (ticket
``0lq.9``, stage 1).

``MustExist[M]`` desugars to ``Annotated[<M's pk type>, RefExists(M)]`` -- a plain
scalar (int / UUID / ...) that stays a scalar everywhere (wire, column,
``data.<field>``), plus a batched existence check on write (404 on a missing id).
Unlike ``IDRef`` / ``IDSchema`` it is NOT a wrapper.

These pin: the desugaring (incl. non-int pk types), the scalar identity, that the
write path does not treat it as a reference, the batched check (sync + async, one
query per model), optional fields, the explicit marker form, and the e2e flat
wire shape.
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
from fastapi_restly.objects import async_make_new_object, make_new_object, update_object
from fastapi_restly.schemas._base import RefExists, is_reference_annotation

from .conftest import create_tables

# ---------------------------------------------------------------------------
# Desugaring + scalar identity (no DB)
# ---------------------------------------------------------------------------


def test_mustexist_desugars_to_annotated_scalar_with_marker():
    class Post(fr.IDBase):
        title: Mapped[str]

    pk_type, marker = get_args(fr.MustExist[Post])
    assert pk_type is int
    assert isinstance(marker, RefExists)
    assert marker.model is Post


def test_mustexist_second_arg_sets_pk_type_default_int():
    """The pk type is the optional second argument, defaulting to int -- it is
    not derived from the model. Use the second arg for a non-int pk."""

    class UPost(fr.DataclassBase):
        __tablename__ = "mustexist_desugar_upost"
        id: Mapped[UUID] = mapped_column(
            Uuid, primary_key=True, default_factory=uuid4
        )
        title: Mapped[str]

    # One-arg form defaults to int (even for a UUID-keyed model -- not derived).
    pk_type, marker = get_args(fr.MustExist[UPost])
    assert pk_type is int
    assert marker.model is UPost

    # Two-arg form sets the pk type explicitly.
    pk_type2, marker2 = get_args(fr.MustExist[UPost, UUID])
    assert pk_type2 is UUID
    assert marker2.model is UPost


def test_mustexist_field_stays_scalar_and_is_not_a_reference():
    class Post(fr.IDBase):
        title: Mapped[str]

    class CommentSchema(fr.BaseSchema):
        content: str
        post_id: fr.MustExist[Post]

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
    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

    class CommentSchema(fr.BaseSchema):
        content: str
        post_id: fr.MustExist[Post]

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


def test_sync_check_is_batched_one_query_per_model(sync_db):
    """Two ``MustExist`` fields to the same model trigger a single
    ``SELECT ... WHERE id IN (...)`` -- no N+1."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Pairing(fr.IDBase):
        left_id: Mapped[int] = mapped_column(ForeignKey("post.id"))
        right_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

    class PairingSchema(fr.BaseSchema):
        left_id: fr.MustExist[Post]
        right_id: fr.MustExist[Post]

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
    engine, make_session = sync_db

    class UPost(fr.DataclassBase):
        __tablename__ = "mustexist_uuid_post"
        id: Mapped[UUID] = mapped_column(
            Uuid, primary_key=True, default_factory=uuid4
        )
        title: Mapped[str]

    class UComment(fr.IDBase):
        post_id: Mapped[UUID] = mapped_column(
            Uuid, ForeignKey("mustexist_uuid_post.id")
        )

    class UCommentSchema(fr.BaseSchema):
        post_id: fr.MustExist[UPost, UUID]  # non-int pk: explicit second arg

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        post = UPost(title="p")
        session.add(post)
        session.flush()

        comment = make_new_object(session, UComment, UCommentSchema(post_id=post.id))
        session.flush()
        assert comment.post_id == post.id
        assert isinstance(comment.post_id, UUID)

        with pytest.raises(HTTPException) as exc:
            make_new_object(session, UComment, UCommentSchema(post_id=uuid4()))
        assert exc.value.status_code == 404


def test_explicit_marker_form_behaves_like_mustexist(sync_db):
    """``Annotated[int, RefExists(M)]`` (no sugar) is equivalent."""
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
    """``MustExist[M] | None`` buries the marker in the union, but the check is
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
        post_id: fr.MustExist[Post] | None = None

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
            post_id: fr.MustExist[Post]

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
        post_id: fr.MustExist[Post]

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
