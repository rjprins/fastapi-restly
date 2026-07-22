"""IDRef foreign-key fields must work under any column name, not only ``_id``.

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
from sqlalchemy import ForeignKey, ForeignKeyConstraint
from sqlalchemy.exc import IntegrityError
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


def test_relationship_field_with_required_init_fk_sync(sync_db):
    """Relationship exposed as a reference (``post: IDRef[Post]``) when the local
    FK column is a *required* init kwarg -- no ``init=False`` and no default
    The FK id must be passed at construction, not post-assigned,
    or the dataclass ``__init__`` rejects the missing required kwarg. SQLAlchemy
    accepts receiving both the relationship object and its FK id (consistent)."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        # Required init kwarg: no init=False, no default.
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post] = relationship(default=None)

    class CommentSchema(fr.BaseSchema):
        content: str
        post: fr.IDRef[Post]

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        p1 = Post(title="p1")
        p2 = Post(title="p2")
        session.add_all([p1, p2])
        session.flush()

        # The required FK id is constructed, not post-assigned.
        plan = build_create_plan(
            Comment,
            CommentSchema(content="hi", post=p1.id),
            CommentSchema,
            resolved={"post": p1},
        )
        assert plan.kwargs["post_id"] == p1.id
        assert "post_id" not in plan.post_assignments

        comment = make_new_object(
            session, Comment, CommentSchema(content="hi", post=p1.id)
        )
        session.flush()  # would TypeError at construction if the FK were post-assigned
        assert comment.post is p1
        assert comment.post_id == p1.id

        update_object(session, comment, CommentSchema(content="hi", post=p2.id))
        session.flush()
        assert comment.post is p2
        assert comment.post_id == p2.id


def test_relationship_field_with_required_init_fk_async():
    """Async parity for the required-init FK relationship field above."""

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post] = relationship(default=None)

    class CommentSchema(fr.BaseSchema):
        content: str
        post: fr.IDRef[Post]

    async def run():
        engine, make_session = _make_async_engine_and_session()
        async with engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)
        async with make_session() as session:
            p1 = Post(title="p1")
            p2 = Post(title="p2")
            session.add_all([p1, p2])
            await session.flush()

            # The required FK id is constructed, not post-assigned.
            plan = build_create_plan(
                Comment,
                CommentSchema(content="hi", post=p1.id),
                CommentSchema,
                resolved={"post": p1},
            )
            assert plan.kwargs["post_id"] == p1.id
            assert "post_id" not in plan.post_assignments

            comment = await async_make_new_object(
                session, Comment, CommentSchema(content="hi", post=p1.id)
            )
            await session.flush()
            assert comment.post is p1
            assert comment.post_id == p1.id

            await async_update_object(
                session, comment, CommentSchema(content="hi", post=p2.id)
            )
            await session.flush()
            assert comment.post is p2
            assert comment.post_id == p2.id
        await engine.dispose()

    asyncio.run(run())


def test_relationship_field_required_fk_with_init_false_relationship_sync(sync_db):
    """Adjacent to the required-init FK fix above (shares its new branch's else
    arm, but is not guarded by it): when the relationship is ``init=False`` and the FK is a
    required init kwarg, the FK id is constructed and the relationship -- which
    can't be an init kwarg -- is mirrored on afterward. This shape already routed
    correctly via the FK-accepts-init path; the test pins that it stays put."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        # Required init kwarg paired with an init=False relationship.
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post] = relationship(default=None, init=False)

    class CommentSchema(fr.BaseSchema):
        content: str
        post: fr.IDRef[Post]

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        p1 = Post(title="p1")
        p2 = Post(title="p2")
        session.add_all([p1, p2])
        session.flush()

        # FK id constructed; the init=False relationship post-assigned.
        plan = build_create_plan(
            Comment,
            CommentSchema(content="hi", post=p1.id),
            CommentSchema,
            resolved={"post": p1},
        )
        assert plan.kwargs["post_id"] == p1.id
        assert "post" not in plan.kwargs
        assert plan.post_assignments["post"] is p1

        comment = make_new_object(
            session, Comment, CommentSchema(content="hi", post=p1.id)
        )
        session.flush()
        assert comment.post is p1
        assert comment.post_id == p1.id

        update_object(session, comment, CommentSchema(content="hi", post=p2.id))
        session.flush()
        assert comment.post is p2
        assert comment.post_id == p2.id


def test_explicit_null_reference_nullable_required_init_fk_sync(sync_db):
    """Sibling of the required-init FK fix above, for the NULL side: an explicit
    ``post=None`` (or an omitted field defaulting to None) never entered the
    reference branch, so the required-init FK kwarg went missing and dataclass
    ``__init__`` raised ``TypeError: missing ... 'post_id'``. A null reference on
    a nullable FK must instead create the row with a NULL FK, and an update to
    null must clear an existing reference."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        # Nullable, but still a *required* init kwarg: no init=False, no default.
        post_id: Mapped[int | None] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post | None] = relationship(default=None)

    class CommentSchema(fr.BaseSchema):
        content: str
        post: fr.IDRef[Post] | None = None

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        p1 = Post(title="p1")
        session.add(p1)
        session.flush()

        # The null reaches both sides of the pair at construction.
        plan = build_create_plan(
            Comment, CommentSchema(content="hi", post=None), CommentSchema
        )
        assert plan.kwargs["post_id"] is None
        assert plan.kwargs["post"] is None

        # Explicit null: row created with a NULL FK (used to TypeError).
        orphan = make_new_object(
            session, Comment, CommentSchema(content="hi", post=None)
        )
        session.flush()
        assert orphan.post is None
        assert orphan.post_id is None

        # Omitted field defaulting to None: same path, same result.
        implicit = make_new_object(session, Comment, CommentSchema(content="alone"))
        session.flush()
        assert implicit.post_id is None

        # Updating an existing reference to null clears FK and relationship.
        attached = make_new_object(
            session, Comment, CommentSchema(content="hi", post=p1.id)
        )
        session.flush()
        assert attached.post_id == p1.id
        update_object(session, attached, CommentSchema(content="hi", post=None))
        session.flush()
        assert attached.post is None
        assert attached.post_id is None


def test_explicit_null_reference_nullable_required_init_fk_async():
    """Async parity for the explicit-null nullable required-init FK above."""

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int | None] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post | None] = relationship(default=None)

    class CommentSchema(fr.BaseSchema):
        content: str
        post: fr.IDRef[Post] | None = None

    async def run():
        engine, make_session = _make_async_engine_and_session()
        async with engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)
        async with make_session() as session:
            p1 = Post(title="p1")
            session.add(p1)
            await session.flush()

            plan = build_create_plan(
                Comment, CommentSchema(content="hi", post=None), CommentSchema
            )
            assert plan.kwargs["post_id"] is None
            assert plan.kwargs["post"] is None

            orphan = await async_make_new_object(
                session, Comment, CommentSchema(content="hi", post=None)
            )
            await session.flush()
            assert orphan.post is None
            assert orphan.post_id is None

            implicit = await async_make_new_object(
                session, Comment, CommentSchema(content="alone")
            )
            await session.flush()
            assert implicit.post_id is None

            attached = await async_make_new_object(
                session, Comment, CommentSchema(content="hi", post=p1.id)
            )
            await session.flush()
            assert attached.post_id == p1.id
            await async_update_object(
                session, attached, CommentSchema(content="hi", post=None)
            )
            await session.flush()
            assert attached.post is None
            assert attached.post_id is None
        await engine.dispose()

    asyncio.run(run())


def test_explicit_null_reference_non_nullable_fk_fails_at_flush_sync(sync_db):
    """A null reference to a NON-nullable FK cannot succeed; it must fail as a
    database ``IntegrityError`` at flush (the framework's standard 409 path),
    not as a ``TypeError`` blown out of the dataclass ``__init__``. The schema
    opted into ``| None`` on a column the database forbids to be NULL, so the
    database stays the authority."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        # NOT NULL and a required init kwarg.
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post] = relationship(default=None)

    class CommentSchema(fr.BaseSchema):
        content: str
        post: fr.IDRef[Post] | None = None

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        # Construction succeeds (used to TypeError)...
        make_new_object(session, Comment, CommentSchema(content="hi", post=None))
        # ...and the constraint speaks at flush.
        with pytest.raises(IntegrityError):
            session.flush()


def test_explicit_null_reference_non_nullable_fk_fails_at_flush_async():
    """Async parity for the non-nullable explicit-null case above."""

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post] = relationship(default=None)

    class CommentSchema(fr.BaseSchema):
        content: str
        post: fr.IDRef[Post] | None = None

    async def run():
        engine, make_session = _make_async_engine_and_session()
        async with engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)
        async with make_session() as session:
            await async_make_new_object(
                session, Comment, CommentSchema(content="hi", post=None)
            )
            with pytest.raises(IntegrityError):
                await session.flush()
        await engine.dispose()

    asyncio.run(run())


def test_unset_sibling_reference_does_not_clobber_supplied_side_sync(sync_db):
    """A schema may declare BOTH names of one FK/relationship pair as optional
    reference fields (the dual shape the consistency validator exists for).
    Creatable-field iteration also yields the UNSET sibling (its default None),
    and that None must not clobber the side the client actually supplied — in
    either declaration order, whichever side is sent."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int | None] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post | None] = relationship(default=None)

    class RelationFirstSchema(fr.BaseSchema):
        content: str
        post: fr.IDRef[Post] | None = None
        post_id: fr.IDRef[Post] | None = None

    class ColumnFirstSchema(fr.BaseSchema):
        content: str
        post_id: fr.IDRef[Post] | None = None
        post: fr.IDRef[Post] | None = None

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        p1 = Post(title="p1")
        session.add(p1)
        session.flush()

        for schema_cls in (RelationFirstSchema, ColumnFirstSchema):
            for supplied in ("post", "post_id"):
                comment = make_new_object(
                    session,
                    Comment,
                    schema_cls(content="hi", **{supplied: p1.id}),
                )
                session.flush()
                assert comment.post_id == p1.id, (schema_cls.__name__, supplied)
                assert comment.post is p1, (schema_cls.__name__, supplied)


def test_unset_sibling_reference_does_not_clobber_supplied_side_async():
    """Async parity for the unset-sibling no-clobber contract above."""

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int | None] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post | None] = relationship(default=None)

    class DualSchema(fr.BaseSchema):
        content: str
        post: fr.IDRef[Post] | None = None
        post_id: fr.IDRef[Post] | None = None

    async def run():
        engine, make_session = _make_async_engine_and_session()
        async with engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)
        async with make_session() as session:
            p1 = Post(title="p1")
            session.add(p1)
            await session.flush()

            for supplied in ("post", "post_id"):
                comment = await async_make_new_object(
                    session, Comment, DualSchema(content="hi", **{supplied: p1.id})
                )
                await session.flush()
                assert comment.post_id == p1.id, supplied
                assert comment.post is p1, supplied
        await engine.dispose()

    asyncio.run(run())


def test_null_reference_on_composite_fk_relationship_creates_row(sync_db):
    """A MANYTOONE relationship over a composite FK has no single partner
    column to infer — but a null reference also has no id to route, so the
    single-FK inference must be skipped, not raised. Creating with the optional
    reference field omitted or explicitly null must succeed with NULL FKs.
    (Supplying a non-null reference to such a relationship remains unsupported
    and still raises the descriptive inference error.)"""
    engine, make_session = sync_db

    class CompositeTenant(fr.DataclassBase):
        __tablename__ = "null_ref_composite_tenant"
        id1: Mapped[int] = mapped_column(primary_key=True)
        id2: Mapped[int] = mapped_column(primary_key=True)

    class TenantItem(fr.DataclassBase):
        __tablename__ = "null_ref_tenant_item"
        __table_args__ = (
            ForeignKeyConstraint(
                ["tenant_a", "tenant_b"],
                ["null_ref_composite_tenant.id1", "null_ref_composite_tenant.id2"],
            ),
        )
        id: Mapped[int] = mapped_column(
            primary_key=True, autoincrement=True, init=False
        )
        name: Mapped[str]
        tenant_a: Mapped[int | None] = mapped_column(default=None)
        tenant_b: Mapped[int | None] = mapped_column(default=None)
        tenant: Mapped[CompositeTenant | None] = relationship(default=None)

    class ItemSchema(fr.BaseSchema):
        name: str
        tenant: fr.IDRef[CompositeTenant] | None = None

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        explicit = make_new_object(
            session, TenantItem, ItemSchema(name="explicit", tenant=None)
        )
        omitted = make_new_object(session, TenantItem, ItemSchema(name="omitted"))
        session.flush()
        assert explicit.tenant is None and explicit.tenant_a is None
        assert omitted.tenant is None and omitted.tenant_a is None


def test_scalar_named_null_reference_writes_only_its_own_column(sync_db):
    """An explicit null on a scalar-FK-named reference (``post_id:
    IDRef[Post] | None``) writes the column and nothing else: no relationship
    mirror (a null has nothing to pair), no TypeError, a row with a NULL FK."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int | None] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post | None] = relationship(default=None)

    class CommentSchema(fr.BaseSchema):
        content: str
        post_id: fr.IDRef[Post] | None = None

    fr.DataclassBase.metadata.create_all(engine)

    plan = build_create_plan(
        Comment, CommentSchema(content="hi", post_id=None), CommentSchema
    )
    assert plan.kwargs["post_id"] is None
    assert "post" not in plan.kwargs
    assert "post" not in plan.post_assignments

    with make_session() as session:
        comment = make_new_object(
            session, Comment, CommentSchema(content="hi", post_id=None)
        )
        session.flush()
        assert comment.post_id is None


def test_idschema_null_reference_creates_row_with_null_fk(sync_db):
    """The null path covers ``IDSchema``-typed reference fields the same as
    ``IDRef`` ones."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int | None] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post | None] = relationship(default=None)

    class CommentSchema(fr.BaseSchema):
        content: str
        post: fr.IDSchema[Post] | None = None

    fr.DataclassBase.metadata.create_all(engine)

    with make_session() as session:
        comment = make_new_object(
            session, Comment, CommentSchema(content="hi", post=None)
        )
        session.flush()
        assert comment.post is None
        assert comment.post_id is None


def test_async_null_reference_e2e(client):
    """HTTP end-to-end: POSTing an explicit ``"post": null`` (and omitting the
    field) creates a row with a NULL FK on a nullable column, and turns into
    the framework's 409 — not a 500 — when the column is NOT NULL."""

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int | None] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post | None] = relationship(default=None)

    class StrictComment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))
        post: Mapped[Post] = relationship(default=None)

    class PostSchema(fr.IDSchema):
        title: str

    class CommentSchema(fr.IDSchema):
        content: str
        post: fr.IDRef[Post] | None = None

    class StrictCommentSchema(fr.IDSchema):
        content: str
        post: fr.IDRef[Post] | None = None

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

    @fr.include_view(client.app)
    class StrictCommentView(fr.AsyncRestView):
        prefix = "/strict-comments"
        model = StrictComment
        schema = StrictCommentSchema

    create_tables()

    explicit = client.post(
        "/comments/", json={"content": "hi", "post": None}, assert_status_code=201
    ).json()
    assert explicit["post"] is None

    omitted = client.post(
        "/comments/", json={"content": "solo"}, assert_status_code=201
    ).json()
    assert omitted["post"] is None

    # NOT NULL column: the database refuses, surfaced as the standard 409.
    client.post(
        "/strict-comments/",
        json={"content": "hi", "post": None},
        assert_status_code=409,
    )


def test_null_fk_named_reference_with_required_init_relationship(sync_db):
    """Mirror of the required-init FK case: the null arrives via the FK-NAMED
    reference field while the partner *relationship* is the required init
    kwarg (``relationship()`` with no default). The relationship kwarg must be
    constructed as None too, instead of ``__init__`` rejecting it missing."""
    engine, make_session = sync_db

    class Post(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        content: Mapped[str]
        post_id: Mapped[int | None] = mapped_column(ForeignKey("post.id"))
        # No default at all: a required init kwarg on the relationship side.
        post: Mapped[Post | None] = relationship()

    class CommentSchema(fr.BaseSchema):
        content: str
        post_id: fr.IDRef[Post] | None = None

    fr.DataclassBase.metadata.create_all(engine)

    plan = build_create_plan(
        Comment, CommentSchema(content="hi", post_id=None), CommentSchema
    )
    assert plan.kwargs["post_id"] is None
    assert plan.kwargs["post"] is None

    with make_session() as session:
        explicit = make_new_object(
            session, Comment, CommentSchema(content="hi", post_id=None)
        )
        omitted = make_new_object(session, Comment, CommentSchema(content="solo"))
        session.flush()
        assert explicit.post_id is None
        assert omitted.post_id is None
