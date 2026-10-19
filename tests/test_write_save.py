"""The save step of the commit bracket.

After the change, the bracket flushes, refreshes the subject, and reloads the
other written rows of the view's model, in chunks. The reload expires only
their relationships and fills what is unloaded, so it keeps every value that
the route loaded or that SQLAlchemy changed after the flush. These tests run
on a SQLite file, sync and async.
"""

import sqlite3

import pytest
from sqlalchemy import ForeignKey, delete, event, func, inspect, literal, select, update
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    query_expression,
    relationship,
    undefer,
    with_expression,
)

import fastapi_restly as fr

from ._models import SharedBase
from ._write_env import call, enter, env, registered  # noqa: F401

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _models():
    class Team(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Member(fr.IDMixin, SharedBase):
        name: Mapped[str]
        team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
        bio: Mapped[str] = mapped_column(deferred=True, default="")
        search: Mapped[str] = mapped_column(default="")
        team: Mapped[Team] = relationship(init=False)

    class TeamSchema(fr.IDSchema):
        name: str

    class MemberSchema(fr.IDSchema):
        name: str
        team_id: int
        team: fr.ReadOnly[TeamSchema | None] = None

    return Team, Member, MemberSchema


async def _seed(env, Team, Member, members=2):
    await env.create_all()
    session = env.make_session()
    has_bio = "bio" in inspect(Member).attrs
    async with enter(session):
        a, b = Team(name="a"), Team(name="b")
        session.add_all([a, b])
        await call(session.flush())
        session.add_all(
            [
                Member(
                    name=f"m{i}",
                    team_id=a.id,
                    **({"bio": f"bio{i}"} if has_bio else {}),
                )
                for i in range(members)
            ]
        )
        await call(session.commit())
    return env.make_session()


def _view(env, Member, MemberSchema, **namespace):
    return registered(
        type(
            "MemberView",
            (env.base,),
            {
                "prefix": "/members",
                "model": Member,
                "schema": MemberSchema,
                **namespace,
            },
        )
    )


# ---------------------------------------------------------------------------
# The reload of the other written rows
# ---------------------------------------------------------------------------


async def test_a_python_attribute_on_another_row_survives_the_reload(env):
    Team, Member, MemberSchema = _models()
    session = await _seed(env, Team, Member)
    async with enter(session):
        view = _view(env, Member, MemberSchema)(request=None, session=session)
        rows = list(await call(session.scalars(select(Member).order_by(Member.id))))
        async with enter(view.write_action("rename_all", obj=None)):
            for row in rows:
                row.name += "!"
                row.can_edit = True  # a value for the response, not a column
        assert [row.can_edit for row in rows] == [True, True]
        assert [row.name for row in rows] == ["m0!", "m1!"]


async def test_a_deferred_column_that_the_route_loaded_stays_loaded_on_other_rows(env):
    """A route loads rows with ``undefer`` and changes them in one block. The
    reload of the other rows fills only what is unloaded, so the deferred
    column stays loaded. Without that, the response would raise
    ``MissingGreenlet`` on async."""
    Team, Member, MemberSchema = _models()
    session = await _seed(env, Team, Member)
    async with enter(session):
        view = _view(env, Member, MemberSchema)(request=None, session=session)
        stmt = select(Member).options(undefer(Member.bio)).order_by(Member.id)
        rows = list(await call(session.scalars(stmt)))
        assert all("bio" in inspect(row).dict for row in rows)
        async with enter(view.write_action("rename_all", obj=None)):
            for row in rows:
                row.name += "!"
        assert all("bio" in inspect(row).dict for row in rows), [
            sorted(inspect(row).unloaded) for row in rows
        ]


async def test_a_deferred_column_of_the_subject_stays_loaded(env):
    Team, Member, MemberSchema = _models()
    session = await _seed(env, Team, Member)
    async with enter(session):
        view = _view(env, Member, MemberSchema)(request=None, session=session)
        stmt = select(Member).options(undefer(Member.bio)).where(Member.id == 1)
        row = await call(session.scalar(stmt))
        async with enter(view.write_action("rename", obj=row)):
            row.name = "renamed"
        assert inspect(row).dict["bio"] == "bio0"


async def test_subclass_columns_of_other_rows_stay_loaded(env):
    """Joined inheritance: the view's model is ``Animal``, and the block
    changes two ``Dog`` rows. The reload selects ``Animal``, so the ``Dog``
    columns are not in the SELECT. They stay loaded, because the reload
    fills only what is unloaded."""

    class Animal(SharedBase):
        __tablename__ = "animal"
        id: Mapped[int] = mapped_column(primary_key=True, init=False)
        name: Mapped[str]
        kind: Mapped[str] = mapped_column(init=False)
        __mapper_args__ = {"polymorphic_on": "kind", "polymorphic_identity": "animal"}

    class Dog(Animal):
        __tablename__ = "dog"
        id: Mapped[int] = mapped_column(
            ForeignKey("animal.id"), primary_key=True, init=False
        )
        breed: Mapped[str] = mapped_column(default="")
        __mapper_args__ = {"polymorphic_identity": "dog"}

    class AnimalSchema(fr.IDSchema):
        name: str

    await env.create_all()
    session = env.make_session()
    async with enter(session):
        session.add_all([Dog(name="a", breed="pug"), Dog(name="b", breed="pug")])
        await call(session.commit())
    session = env.make_session()
    async with enter(session):
        view = registered(
            type(
                "AnimalView",
                (env.base,),
                {"prefix": "/animals", "model": Animal, "schema": AnimalSchema},
            )
        )(request=None, session=session)
        dogs = list(await call(session.scalars(select(Dog).order_by(Dog.id))))
        async with enter(view.write_action("rename_all", obj=None)):
            for dog in dogs:
                dog.name += "!"
        assert all("breed" in inspect(dog).dict for dog in dogs), [
            sorted(inspect(dog).unloaded) for dog in dogs
        ]


def test_building_the_reload_expires_only_relationships(tmp_path):
    """The reload expires the relationships of each row. For a model with no
    relationships, it expires nothing: SQLAlchemy reads
    ``session.expire(row, [])`` as "every attribute"."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from fastapi_restly.views._base import _rows_reload_statements

    class Note(fr.IDMixin, SharedBase):
        text: Mapped[str]

    engine = create_engine("sqlite://")
    SharedBase.metadata.create_all(engine)
    with Session(engine) as session:
        note = Note(text="a")
        session.add(note)
        session.flush()
        statements = _rows_reload_statements(Note, [note], [])
        assert len(statements) == 1
        assert sorted(inspect(note).expired_attributes) == []
    engine.dispose()


class _PlainBase(DeclarativeBase):
    pass


def _expression_models():
    """A plain declarative base: ``query_expression`` is not a dataclass field."""
    _PlainBase.metadata.clear()
    _PlainBase.registry.dispose()

    class Doc(_PlainBase):
        __tablename__ = "doc"
        id: Mapped[int] = mapped_column(primary_key=True)
        title: Mapped[str]
        note: Mapped[str | None] = query_expression()

    class DocSchema(fr.IDSchema):
        title: str

    return Doc, DocSchema


async def _seed_docs(env, Doc):
    await env.create_all(_PlainBase.metadata)
    session = env.make_session()
    async with enter(session):
        session.add_all([Doc(title="a"), Doc(title="b")])
        await call(session.commit())
    return env.make_session()


async def test_a_with_expression_value_survives_the_save_of_the_subject(env):
    """A route loads its row with ``with_expression`` (response work in SQL),
    changes it in a ``write_action`` block, and returns it. The subject's
    refresh by attribute names keeps the value.
    """
    Doc, DocSchema = _expression_models()
    session = await _seed_docs(env, Doc)
    async with enter(session):
        view = registered(
            type(
                "DocView",
                (env.base,),
                {"prefix": "/docs", "model": Doc, "schema": DocSchema},
            )
        )(request=None, session=session)
        stmt = (
            select(Doc)
            .where(Doc.id == 1)
            .options(with_expression(Doc.note, literal("computed")))
        )
        doc = await call(session.scalar(stmt))
        assert doc.note == "computed"
        async with enter(view.write_action("retitle", obj=doc)):
            doc.title = "new"
        assert doc.note == "computed"


async def test_a_with_expression_in_the_views_loader_options_survives(env):
    """An expression from ``get_loader_options``: the reload of
    the other rows carries the view's options, so they keep it."""
    Doc, DocSchema = _expression_models()

    def options(self):
        return [with_expression(Doc.note, literal("computed"))]

    session = await _seed_docs(env, Doc)
    async with enter(session):
        view = registered(
            type(
                "DocView",
                (env.base,),
                {
                    "prefix": "/docs",
                    "model": Doc,
                    "schema": DocSchema,
                    "get_loader_options": options,
                },
            )
        )(request=None, session=session)
        stmt = select(Doc).options(*view.get_loader_options())
        docs = list(await call(session.scalars(stmt.order_by(Doc.id))))
        async with enter(view.write_action("retitle_all", obj=None)):
            for doc in docs:
                doc.title += "!"
        assert [doc.note for doc in docs] == ["computed", "computed"]


async def test_a_change_from_after_flush_postexec_on_a_written_row_is_kept(env):
    """``after_flush_postexec`` is SQLAlchemy's place to change objects after a
    flush; the change goes into the next flush. Here it fills a search column
    of each row that the flush wrote. The reload of the other rows does not
    overwrite the pending change, so the commit writes it, also on async,
    where nothing autoflushes before the reload."""
    Team, Member, MemberSchema = _models()
    session = await _seed(env, Team, Member)
    async with enter(session):
        plain = getattr(session, "sync_session", session)

        @event.listens_for(plain, "after_flush_postexec")
        def fill_search(session, flush_context):
            for obj in list(session.identity_map.values()):
                if isinstance(obj, Member) and obj.search != obj.name.lower():
                    obj.search = obj.name.lower()

        view = _view(env, Member, MemberSchema)(request=None, session=session)
        rows = list(await call(session.scalars(select(Member).order_by(Member.id))))
        async with enter(view.write_action("rename_all", obj=None)):
            for row in rows:
                row.name = row.name.upper()

    check = env.make_session()
    async with enter(check):
        stored = list(await call(check.scalars(select(Member.search))))
    assert stored == ["m0", "m1"]


# ---------------------------------------------------------------------------
# Many rows, composite keys, odd subjects
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not hasattr(sqlite3.Connection, "setlimit"),
    reason="sqlite3.Connection.setlimit is new in Python 3.11",
)
def test_a_bulk_insert_in_one_block_and_the_parameter_limit(tmp_path):
    """A bulk import in one block: ``session.add_all`` of many rows of the
    view's model, with ``obj=None``.

    The save reloads the new rows in chunks, so each ``WHERE id IN (...)``
    stays below the parameter limit of the database. SQLite allows 32766
    parameters today (999 before 3.32), asyncpg 32767. This test sets the
    old limit of 999 on the connection, so 1000 rows show what 33000 rows
    would do on a default SQLite.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    Team, Member, MemberSchema = _models()
    engine = create_engine(f"sqlite:///{tmp_path / 'limit.db'}")

    @event.listens_for(engine, "connect")
    def limit(connection, record):
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

    SharedBase.metadata.create_all(engine)
    view_type = registered(
        type(
            "MemberView",
            (fr.RestView,),
            {"prefix": "/members", "model": Member, "schema": MemberSchema},
        )
    )
    try:
        with Session(engine, expire_on_commit=False) as session:
            session.add(Team(name="a"))
            session.commit()
            view = view_type(request=None, session=session)
            with view.write_action("import", obj=None):
                session.add_all(Member(name=f"n{i}", team_id=1) for i in range(1000))
            assert session.scalar(select(func.count()).select_from(Member)) == 1000
    finally:
        engine.dispose()


async def test_recording_the_written_rows_reads_session_new_once_per_flush(env):
    """SQLAlchemy builds a new set on each access of ``session.new``. The
    record of the written rows reads it once per flush, so a flush of N new
    rows costs N, not N*N. (A block of 30000 rows took 47 s with one read per
    row, and 1 s with one read per flush.)"""
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import Session

    reads = []

    class CountingSession(Session):
        @property
        def new(self):
            reads.append(1)
            return super().new

    Team, Member, MemberSchema = _models()
    seeded = await _seed(env, Team, Member, members=0)
    engine = seeded.bind
    if env.asynchronous:
        session = AsyncSession(
            engine,
            expire_on_commit=False,
            autoflush=False,
            sync_session_class=CountingSession,
        )
    else:
        session = CountingSession(engine, expire_on_commit=False)
    async with enter(session):
        view = _view(env, Member, MemberSchema)(request=None, session=session)
        async with enter(view.write_action("import", obj=None)):
            session.add_all(Member(name=f"n{i}", team_id=1) for i in range(200))
    assert len(reads) < 10, len(reads)


async def test_the_reload_of_rows_with_a_composite_key(env):
    class Shelf(SharedBase):
        __tablename__ = "shelf"
        room: Mapped[str] = mapped_column(primary_key=True)
        number: Mapped[int] = mapped_column(primary_key=True)
        label: Mapped[str]

    class ShelfSchema(fr.BaseSchema):
        room: str
        number: int
        label: str

    await env.create_all()
    session = env.make_session()
    async with enter(session):
        session.add_all(
            [
                Shelf(room="a", number=1, label="x"),
                Shelf(room="a", number=2, label="y"),
                Shelf(room="b", number=1, label="z"),
            ]
        )
        await call(session.commit())
    session = env.make_session()
    async with enter(session):
        view = registered(
            type(
                "ShelfView",
                (env.base,),
                {
                    "prefix": "/shelves",
                    "model": Shelf,
                    "schema": ShelfSchema,
                    "exclude_routes": (
                        fr.ViewRoute.GET_ONE,
                        fr.ViewRoute.UPDATE,
                        fr.ViewRoute.DELETE,
                    ),
                },
            )
        )(request=None, session=session)
        rows = list(await call(session.scalars(select(Shelf).where(Shelf.room == "a"))))
        first = rows[0]
        env.statements.clear()
        async with enter(view.write_action("relabel", obj=first)):
            for row in rows:
                row.label += "!"
        assert sorted(row.label for row in rows) == ["x!", "y!"]
        reloads = [s for s in env.statements if s.startswith("SELECT shelf.room")]
        assert any("IN (VALUES" in s for s in reloads), reloads


async def test_a_subject_of_another_model(env):
    Team, Member, MemberSchema = _models()
    calls = []

    def after(self, action, new, old=None):
        calls.append((type(new).__name__, old))

    async def async_after(self, action, new, old=None):
        after(self, action, new, old)

    session = await _seed(env, Team, Member)
    async with enter(session):
        view = _view(
            env, Member, MemberSchema, after_action_commit=env.pick(after, async_after)
        )(request=None, session=session)
        team = await call(session.get(Team, 1))
        rows = list(await call(session.scalars(select(Member))))
        async with enter(view.write_action("move_team", obj=team)):
            team.name = "renamed"
            for row in rows:
                row.team_id = 2
        assert calls == [("Team", {"id": 1, "name": "a"})]
        # the members are other rows of the view's model: fresh team loaded
        assert {inspect(row).dict["team"].name for row in rows} == {"b"}


async def test_rows_inserted_in_a_savepoint_that_rolls_back(env):
    Team, Member, MemberSchema = _models()
    session = await _seed(env, Team, Member)
    async with enter(session):
        view = _view(env, Member, MemberSchema)(request=None, session=session)
        row = await call(view.get_one(1))
        async with enter(view.write_action("try_extra", obj=row)):
            row.name = "kept"
            nested = await call(session.begin_nested())
            session.add(Member(name="gone", team_id=1))
            await call(session.flush())
            await call(nested.rollback())
        assert row.name == "kept"
    check = env.make_session()
    async with enter(check):
        names = list(await call(check.scalars(select(Member.name).order_by(Member.id))))
    assert names == ["kept", "m1"]


async def test_a_core_delete_of_another_written_row(env):
    Team, Member, MemberSchema = _models()
    session = await _seed(env, Team, Member)
    async with enter(session):
        view = _view(env, Member, MemberSchema)(request=None, session=session)
        first, second = list(
            await call(session.scalars(select(Member).order_by(Member.id)))
        )
        async with enter(view.write_action("x", obj=first)):
            first.name = "a"
            second.name = "b"
            await call(session.flush())
            await call(
                session.execute(delete(Member.__table__).where(Member.id == second.id))
            )
        assert first.name == "a"


async def test_an_orm_update_statement_on_the_subject(env):
    Team, Member, MemberSchema = _models()
    session = await _seed(env, Team, Member)
    async with enter(session):
        view = _view(env, Member, MemberSchema)(request=None, session=session)
        row = await call(view.get_one(1))
        async with enter(view.write_action("x", obj=row)):
            await call(
                session.execute(
                    update(Member.__table__).where(Member.id == 1).values(name="core")
                )
            )
        assert row.name == "core"  # the refresh reads the Core change
