"""The write business methods change objects; the commit bracket saves them.

``create`` and ``update`` return the object without a flush, and ``delete``
only marks the row. After the business method, the bracket adds a new
object, flushes, refreshes it and loads the relationships of the response.
Then the hooks and the commit run. The reference checks are in
``test_reference_checks_in_bracket.py``.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import ForeignKey, event, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.db._config import _fr_config
from fastapi_restly.testing import RestlyTestClient

from ._models import SharedBase
from .conftest import create_tables


@pytest.fixture
def sync_client(sync_db) -> Iterator[RestlyTestClient]:
    yield RestlyTestClient(FastAPI())


def _async_engine():
    return _fr_config.async_make_session.kw["bind"].sync_engine


def _record_statements(engine) -> list[str]:
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, params, context, executemany):
        statements.append(" ".join(statement.split()))

    return statements


def _models():
    class Team(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Member(fr.IDMixin, SharedBase):
        name: Mapped[str]
        team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
        # NOT NULL, and only the create override sets it: an INSERT before
        # the override's change would fail
        badge: Mapped[str] = mapped_column(default=None)
        team: Mapped[Team] = relationship(init=False)

    class TeamSchema(fr.IDSchema):
        name: str

    class MemberSchema(fr.IDSchema):
        name: str
        badge: fr.ReadOnly[str]
        team_id: fr.MustExist[int, Team]
        team: fr.ReadOnly[TeamSchema | None] = None

    return Team, Member, TeamSchema, MemberSchema


# ---------------------------------------------------------------------------
# A change after super().create() is in the INSERT
# ---------------------------------------------------------------------------


def test_change_after_super_create_is_in_the_insert(client):
    Team, Member, TeamSchema, MemberSchema = _models()

    @fr.include_view(client.app)
    class MemberView(fr.AsyncRestView):
        prefix = "/members"
        model = Member
        schema = MemberSchema

        async def create(self, schema_obj):
            member = await super().create(schema_obj)
            assert sa_inspect(member).pending  # no flush inside create
            member.badge = schema_obj.name.upper()
            return member

    create_tables()
    with_team = _add_team_async(Team)
    statements = _record_statements(_async_engine())
    body = client.post("/members/", json={"name": "ada", "team_id": with_team}).json()

    assert body["badge"] == "ADA"
    assert body["team"] == {"id": with_team, "name": "core"}
    assert sum(s.startswith("INSERT INTO member") for s in statements) == 1
    assert not any(s.startswith("UPDATE member") for s in statements)


def _add_team_async(Team) -> int:
    import asyncio

    async def add() -> int:
        async with _fr_config.async_make_session() as session:
            team = Team(name="core")
            session.add(team)
            await session.commit()
            return team.id

    return asyncio.run(add())


def test_sync_change_after_super_create_is_in_the_insert(sync_client, sync_db):
    engine, make_session = sync_db
    Team, Member, TeamSchema, MemberSchema = _models()

    @fr.include_view(sync_client.app)
    class MemberView(fr.RestView):
        prefix = "/members"
        model = Member
        schema = MemberSchema

        def create(self, schema_obj):
            member = super().create(schema_obj)
            member.badge = schema_obj.name.upper()
            return member

    SharedBase.metadata.create_all(engine)
    with make_session() as session:
        team = Team(name="core")
        session.add(team)
        session.commit()
        team_id = team.id

    statements = _record_statements(engine)
    body = sync_client.post("/members/", json={"name": "ada", "team_id": team_id})

    assert body.json()["badge"] == "ADA"
    assert body.json()["team"] == {"id": team_id, "name": "core"}
    assert sum(s.startswith("INSERT INTO member") for s in statements) == 1
    assert not any(s.startswith("UPDATE member") for s in statements)


# ---------------------------------------------------------------------------
# What the save step does with each kind of result
# ---------------------------------------------------------------------------


def test_delete_business_method_does_not_flush(client):
    Team, Member, TeamSchema, MemberSchema = _models()
    pending: list[bool] = []

    @fr.include_view(client.app)
    class TeamView(fr.AsyncRestView):
        prefix = "/teams"
        model = Team
        schema = TeamSchema

        async def delete(self, obj):
            await super().delete(obj)
            pending.append(obj in self.session.deleted)

    create_tables()
    team_id = _add_team_async(Team)
    client.delete(f"/teams/{team_id}", assert_status_code=204)
    assert pending == [True]
    assert client.get("/teams/").json()["data"] == []


# ---------------------------------------------------------------------------
# Several rows in one action: the save makes each written row ready
# ---------------------------------------------------------------------------


class Move(fr.BaseSchema):
    team_id: int


def _move_view(client, move_all):
    Team, Member, TeamSchema, MemberSchema = _models()

    @fr.include_view(client.app)
    class TeamView(fr.AsyncRestView):
        prefix = "/teams"
        model = Team
        schema = TeamSchema

    async def create(self, schema_obj):
        member = await fr.AsyncRestView.create(self, schema_obj)
        member.badge = "b"
        return member

    namespace = {
        "prefix": "/members",
        "model": Member,
        "schema": MemberSchema,
        "create": create,
        "move_all": fr.post("/move-all")(move_all),
    }
    fr.include_view(client.app)(type("MemberView", (fr.AsyncRestView,), namespace))
    create_tables()
    a = client.post("/teams/", json={"name": "a"}).json()["id"]
    b = client.post("/teams/", json={"name": "b"}).json()["id"]
    for name in ("x", "y"):
        client.post("/members/", json={"name": name, "team_id": a})
    moved = client.post("/members/move-all", json={"team_id": b}).json()
    return [member["team"]["name"] for member in moved]


def test_several_updates_through_handlers_in_one_commit(client):
    async def move_all(self, request: Move):
        async with self.shared_write_action_commit():
            members = [await self.handle_update(i, request) for i in (1, 2)]
        return [self.to_single_response(m).model_dump() for m in members]

    assert _move_view(client, move_all) == ["b", "b"]


# ---------------------------------------------------------------------------
# Nothing is written before the save, on a sync session too
# ---------------------------------------------------------------------------


def test_sync_uniqueness_query_after_super_create_does_not_find_itself(
    sync_client, sync_db
):
    """Restly's sync session keeps SQLAlchemy's autoflush. Without
    ``no_autoflush`` around ``create``, the query below would INSERT the
    half-built account, with the plain password, and then find it: every POST
    would be a 409."""
    engine, _ = sync_db

    class Account(fr.IDMixin, SharedBase):
        email: Mapped[str] = mapped_column(unique=True)
        password: Mapped[str]

    class AccountSchema(fr.IDSchema):
        email: str
        password: fr.WriteOnly[str]

    @fr.include_view(sync_client.app)
    class AccountView(fr.RestView):
        prefix = "/accounts"
        model = Account
        schema = AccountSchema

        def create(self, schema_obj):
            account = super().create(schema_obj)
            taken = self.session.scalar(
                select(Account.id).where(Account.email == schema_obj.email)
            )
            if taken is not None:
                raise fr.exc.Conflict("This email is taken")
            account.password = "hashed:" + schema_obj.password
            return account

    SharedBase.metadata.create_all(engine)
    inserts: list[Any] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, params, context, executemany):
        if statement.startswith("INSERT INTO account"):
            inserts.append(params)

    sync_client.post("/accounts/", json={"email": "a@x", "password": "secret"})
    assert len(inserts) == 1
    assert "hashed:secret" in inserts[0] and "secret" not in inserts[0]

    response = sync_client.post(
        "/accounts/", json={"email": "a@x", "password": "other"}, assert_status_code=409
    )
    assert response.json()["detail"] == "This email is taken"
