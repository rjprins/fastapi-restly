"""``obj`` names the subject of a write; the save makes the view's rows ready.

The subject is the one row that the action is about. Its SQLAlchemy state
decides what ``authorize``, ``old`` and ``new`` are. After the change, the
bracket flushes, then refreshes the subject and every other row of the view's
model that the action wrote, and loads the relationships of the response on
them. The change runs under ``session.no_autoflush``. Sync and async run the
same tests.
"""

from contextlib import asynccontextmanager
from inspect import isawaitable
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import ForeignKey, create_engine, event, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

import fastapi_restly as fr

from ._models import SharedBase

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _call(value):
    return await value if isawaitable(value) else value


@asynccontextmanager
async def _enter(manager):
    if hasattr(manager, "__aenter__"):
        async with manager as value:
            yield value
    else:
        with manager as value:
            yield value


class Move(fr.BaseSchema):
    team_id: int


class CookbookUpdate(fr.BaseSchema):
    id: int
    name: str
    team_id: int


def _slugify(name: str) -> str:
    return name.lower().replace(" ", "-")


@pytest.fixture(params=["sync", "async"])
async def subject(request, tmp_path):
    class Team(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Member(fr.IDMixin, SharedBase):
        name: Mapped[str]
        team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
        left: Mapped[bool] = mapped_column(default=False)
        team: Mapped[Team] = relationship(init=False)

    class TeamSchema(fr.IDSchema):
        name: str

    class MemberSchema(fr.IDSchema):
        name: str
        team_id: fr.MustExist[int, Team]
        left: fr.ReadOnly[bool] = False
        team: fr.ReadOnly[TeamSchema | None] = None

    class Cookbook(fr.IDMixin, SharedBase):
        name: Mapped[str]
        slug: Mapped[str] = mapped_column(unique=True)
        team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
        team: Mapped[Team] = relationship(init=False)

    class CookbookSchema(fr.IDSchema):
        name: str
        slug: fr.ReadOnly[str]
        team_id: int
        team: fr.ReadOnly[TeamSchema | None] = None

    calls: list[tuple] = []

    def authorize(self, action, obj=None, data=None):
        calls.append(("authorize", action, obj))

    def after(self, action, new, old=None):
        calls.append(("after", action, new, old))

    def soft_delete(self, obj):
        obj.left = True  # the row stays

    async def async_authorize(self, action, obj=None, data=None):
        authorize(self, action, obj, data)

    async def async_after(self, action, new, old=None):
        after(self, action, new, old)

    async def async_soft_delete(self, obj):
        soft_delete(self, obj)

    class Action(fr.Action):
        RENAME = "rename"
        ENROL = "enrol"
        RECOUNT = "recount"
        MERGE = "merge"
        MOVE = "move"
        MOVE_ALL = "move_all"
        RENAME_TEAM = "rename_team"
        UPDATE_MANY = "update_many"

    asynchronous = request.param == "async"
    base = fr.AsyncRestView if asynchronous else fr.RestView
    namespace = {
        "model": Member,
        "schema": MemberSchema,
        "Action": Action,
        "authorize": async_authorize if asynchronous else authorize,
        "after_action_commit": async_after if asynchronous else after,
    }
    view_type = type("MemberView", (base,), {**namespace, "prefix": "/members"})
    soft_type = type(
        "SoftMemberView",
        (base,),
        {
            **namespace,
            "prefix": "/soft-members",
            "delete": async_soft_delete if asynchronous else soft_delete,
        },
    )

    # An update that writes the row with a free slug, and retries in a
    # savepoint while the slug is taken.
    def cookbook_update(self, obj, schema_obj):
        for i in range(10):
            slug = _slugify(schema_obj.name) + (f"-{i}" if i else "")
            try:
                with self.session.begin_nested():
                    obj.name, obj.slug = schema_obj.name, slug
                    obj.team_id = schema_obj.team_id
                    self.session.flush()
                return obj
            except IntegrityError:
                continue
        raise fr.exc.Conflict("This item already exists.")

    async def async_cookbook_update(self, obj, schema_obj):
        for i in range(10):
            slug = _slugify(schema_obj.name) + (f"-{i}" if i else "")
            try:
                async with self.session.begin_nested():
                    obj.name, obj.slug = schema_obj.name, slug
                    obj.team_id = schema_obj.team_id
                    await self.session.flush()
                return obj
            except IntegrityError:
                continue
        raise fr.exc.Conflict("This item already exists.")

    cookbook_type = type(
        "CookbookView",
        (base,),
        {
            "prefix": "/cookbooks",
            "model": Cookbook,
            "schema": CookbookSchema,
            "Action": Action,
            "update": async_cookbook_update if asynchronous else cookbook_update,
        },
    )
    app = FastAPI()
    fr.include_view(app)(view_type)
    fr.include_view(app)(soft_type)
    fr.include_view(app)(cookbook_type)

    database = tmp_path / "subject.db"
    if asynchronous:
        engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
        target_engine = engine.sync_engine
        # Restly's async factory: no autoflush, no expire on commit
        make_session = lambda: AsyncSession(
            engine, expire_on_commit=False, autoflush=False
        )
    else:
        engine = target_engine = create_engine(f"sqlite:///{database}")
        # Restly's sync factory keeps SQLAlchemy's autoflush=True
        make_session = lambda: Session(engine, expire_on_commit=False)

    # Give SQLite savepoints a real outer transaction, including before any DML.
    @event.listens_for(target_engine, "connect")
    def connect(connection, record):
        connection.isolation_level = None

    @event.listens_for(target_engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    statements: list[str] = []

    @event.listens_for(target_engine, "before_cursor_execute")
    def record(conn, cursor, statement, params, context, executemany):
        statements.append(" ".join(statement.split()))

    try:
        if asynchronous:
            async with engine.begin() as connection:
                await connection.run_sync(SharedBase.metadata.create_all)
        else:
            SharedBase.metadata.create_all(engine)

        async with _enter(make_session()) as session:
            a, b = Team(name="a"), Team(name="b")
            session.add_all([a, b])
            await _call(session.flush())
            session.add_all(
                [Member(name="x", team_id=a.id), Member(name="y", team_id=a.id)]
            )
            session.add_all(
                [
                    Cookbook(name="Lunch", slug="lunch", team_id=a.id),
                    Cookbook(name="Brunch", slug="brunch", team_id=a.id),
                ]
            )
            await _call(session.commit())
            statements.clear()
            yield SimpleNamespace(
                view=view_type(request=None, session=session),
                soft=soft_type(request=None, session=session),
                cookbooks=cookbook_type(request=None, session=session),
                session=session,
                Team=Team,
                Member=Member,
                team_a=a,
                team_b=b,
                calls=calls,
                statements=statements,
                asynchronous=asynchronous,
            )
    finally:
        await _call(engine.dispose())


def _loaded_team(member) -> str | None:
    """The name of the member's loaded team, read without IO."""
    team = inspect(member).dict.get("team")
    return None if team is None else team.name


# ---------------------------------------------------------------------------
# What authorize, old and new are, from the subject's state
# ---------------------------------------------------------------------------


async def test_a_row_is_the_subject(subject):
    view, Member = subject.view, subject.Member
    member = await _call(view.get_one(1))
    async with _enter(view.write_action("rename", obj=member)):
        member.name = "renamed"

    assert subject.calls[0] == ("authorize", "rename", member)
    _, action, new, old = subject.calls[1]
    assert (action, new, old["name"]) == ("rename", member, "x")
    assert isinstance(new, Member)


async def test_a_new_object_is_added_when_the_block_starts(subject):
    view, Member = subject.view, subject.Member
    member = Member(name="new", team_id=subject.team_b.id)
    async with _enter(view.write_action("enrol", obj=member)):
        assert member in subject.session.new  # Restly added it
        await _call(subject.session.flush())  # so a flush gives it an id
        assert member.id is not None

    assert subject.calls[0] == ("authorize", "enrol", None)  # as on POST /
    assert subject.calls[1] == ("after", "enrol", member, None)
    assert _loaded_team(member) == "b"  # the response relationship is loaded


async def test_no_subject(subject):
    async with _enter(subject.view.write_action("recount", obj=None)):
        pass
    assert subject.calls == [
        ("authorize", "recount", None),
        ("after", "recount", None, None),
    ]


async def test_a_subject_that_the_block_deletes_gives_new_none(subject):
    view = subject.view
    member = await _call(view.get_one(1))
    async with _enter(view.write_action("merge", obj=member)):
        await _call(subject.session.delete(member))
    _, _, new, old = subject.calls[-1]
    assert new is None
    assert old["name"] == "x"

    await _call(view.handle_delete(2))
    _, action, new, old = subject.calls[-1]
    assert (action, new, old["name"]) == ("delete", None, "y")


async def test_a_soft_delete_gives_the_row_in_the_handler_and_in_a_block(subject):
    soft = subject.soft
    await _call(soft.handle_delete(1))
    _, action, new, old = subject.calls[-1]
    assert action == "delete"
    assert new.left is True and old["left"] is False

    member = await _call(soft.get_one(2))
    async with _enter(soft.write_action("delete", obj=member)):
        await _call(soft.delete(member))
    _, action, new, old = subject.calls[-1]
    assert action == "delete"
    assert new is member and new.left is True and old["left"] is False


async def test_create_and_update_handlers_name_their_result(subject):
    view = subject.view
    created = await _call(
        view.handle_create(view.schema_create(name="z", team_id=subject.team_b.id))
    )
    assert subject.calls[0] == ("authorize", "create", None)
    assert subject.calls[1] == ("after", "create", created, None)
    assert _loaded_team(created) == "b"

    updated = await _call(
        view.handle_update(created.id, Move(team_id=subject.team_a.id))
    )
    assert subject.calls[2] == ("authorize", "update", created)
    _, _, new, old = subject.calls[3]
    assert new is updated is created
    assert old["team_id"] == subject.team_b.id
    assert _loaded_team(updated) == "a"  # the changed foreign key is not stale


# ---------------------------------------------------------------------------
# Several rows: the save makes every written row of the view's model ready
# ---------------------------------------------------------------------------


async def test_rows_that_update_changes_in_one_block_are_ready(subject):
    """One action that calls update() for each row."""
    view = subject.view
    members = []
    async with _enter(view.write_action("move_all", obj=None)):
        for member_id in (1, 2):
            member = await _call(view.get_one(member_id))
            assert _loaded_team(member) == "a"
            members.append(await _call(view.update(member, Move(team_id=2))))
    assert [_loaded_team(m) for m in members] == ["b", "b"]


async def test_an_update_many_route_with_a_slug_retry(subject):
    """One ``update_many`` action that loads, authorizes and updates each
    cookbook. The update flushes inside a
    savepoint, and retries with another slug when the slug is taken. Both
    cookbooks come back with their new team, and with one reload."""
    view = subject.cookbooks
    items = [
        CookbookUpdate(id=1, name="Dinner", team_id=subject.team_b.id),
        CookbookUpdate(id=2, name="Dinner", team_id=subject.team_b.id),
    ]
    cookbooks = []
    async with _enter(view.write_action("update_many", obj=None, data=items)):
        for item in items:
            cookbook = await _call(view.get_one(item.id))
            await _call(view.authorize("update", obj=cookbook, data=item))
            cookbooks.append(await _call(view.update(cookbook, item)))

    assert [c.slug for c in cookbooks] == ["dinner", "dinner-1"]
    assert [_loaded_team(c) for c in cookbooks] == ["b", "b"]
    reloads = [s for s in subject.statements if "WHERE cookbook.id IN" in s]
    assert len(reloads) == 1, subject.statements


async def test_rows_that_a_savepoint_flushed_in_the_block_are_ready(subject):
    """A retry that flushes each row inside begin_nested()."""
    view = subject.view
    member = await _call(view.get_one(1))
    async with _enter(view.write_action("move", obj=None)):
        async with _enter(subject.session.begin_nested()):
            member.team_id = subject.team_b.id
            await _call(subject.session.flush())
    assert _loaded_team(member) == "b"


async def test_rows_of_another_model_are_flushed_but_not_refreshed(subject):
    view, Team = subject.view, subject.Team
    team = subject.team_a
    async with _enter(view.write_action("rename_team", obj=None)):
        team.name = "renamed"
    team_selects = [s for s in subject.statements if s.startswith("SELECT team.")]
    assert team_selects == []
    names = await _call(subject.session.scalars(select(Team.name).order_by(Team.id)))
    assert list(names) == ["renamed", "b"]


async def test_one_row_costs_the_same_as_before(subject):
    """A handler writes one row of its model: one refresh and one reload of
    the response relationships, and nothing for the other rows."""
    view = subject.view
    await _call(view.handle_update(1, Move(team_id=2)))
    selects = [s for s in subject.statements if s.startswith("SELECT")]
    # get_one and its selectin of team (2), the refresh (1), the reload of
    # the response relationship (2)
    assert len(selects) == 5, selects


async def test_the_other_rows_are_reloaded_in_one_select(subject):
    """However many rows the block changes, the save reloads them with one
    SELECT, plus one for each relationship of the response."""
    view, Member = subject.view, subject.Member
    async with _enter(subject.session.begin_nested()):
        subject.session.add_all(
            Member(name=f"m{i}", team_id=subject.team_a.id) for i in range(8)
        )
    rows = list(await _call(subject.session.scalars(select(Member))))
    subject.statements.clear()
    async with _enter(view.write_action("move_all", obj=None)):
        for row in rows:
            row.team_id = subject.team_b.id
    selects = [s for s in subject.statements if s.startswith("SELECT")]
    assert len(selects) == 2, selects  # the members, and their teams
    assert {_loaded_team(row) for row in rows} == {"b"}


# ---------------------------------------------------------------------------
# The change runs under no_autoflush: nothing is written before the save
# ---------------------------------------------------------------------------


async def test_a_query_in_the_block_does_not_write_the_new_object(subject):
    view, Member = subject.view, subject.Member
    member = Member(name="new", team_id=subject.team_b.id)
    async with _enter(view.write_action("enrol", obj=member)):
        count = await _call(subject.session.scalar(select(func.count(Member.id))))
        assert count == 2  # the new member is not flushed by the query
        assert inspect(member).pending  # not flushed by the query
    assert member.id is not None
    inserts = [s for s in subject.statements if s.startswith("INSERT INTO member")]
    assert len(inserts) == 1
