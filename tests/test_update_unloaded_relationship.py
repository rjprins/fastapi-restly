"""An update can write a relationship that the view does not load.

The view loads the relationships of its response class. An update can write
others: a ``WriteOnly`` field, or a field of a ``schema_update`` that the
response class does not have. To replace a collection, SQLAlchemy reads the
old one first. On an async session that fails inside the assignment, and a
relationship with ``lazy="raise"`` refuses it on both. So the update loads
such a collection before it writes it. A scalar relationship needs no load.
"""

import asyncio
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from sqlalchemy import ForeignKey, select
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

import fastapi_restly as fr
from fastapi_restly.db._globals import _fr_globals
from fastapi_restly.objects import _unloaded_written_collections
from fastapi_restly.testing import RestlyTestClient

from .conftest import create_tables


def _models(lazy: str = "select"):
    class Team(fr.IDBase):
        name: Mapped[str] = mapped_column()

    class Member(fr.IDBase):
        name: Mapped[str] = mapped_column()
        teams: Mapped[list[Team]] = relationship(
            secondary="member_team", default_factory=list, lazy=lazy
        )

    class MemberTeam(fr.DataclassBase):
        __tablename__ = "member_team"
        member_id: Mapped[int] = mapped_column(
            ForeignKey("member.id"), primary_key=True, init=False
        )
        team_id: Mapped[int] = mapped_column(
            ForeignKey("team.id"), primary_key=True, init=False
        )

    return Member, Team


def _insert(Member, Team) -> None:
    async def insert() -> None:
        async with fr.open_async_session() as session:
            red, blue = Team(name="red"), Team(name="blue")
            member = Member(name="ann")
            member.teams.append(red)
            session.add_all([member, red, blue])
            await session.commit()

    asyncio.run(insert())


def _team_ids(Member) -> list[int]:
    async def read() -> list[int]:
        async with fr.open_async_session() as session:
            member = await session.scalar(
                select(Member).options(selectinload(Member.teams))
            )
            assert member is not None
            return sorted(team.id for team in member.teams)

    return asyncio.run(read())


@pytest.mark.parametrize("field", ["write_only", "update_only"])
def test_an_async_update_writes_a_collection_the_response_does_not_name(client, field):
    Member, Team = _models()

    if field == "write_only":

        class MemberSchema(fr.IDSchema):
            name: str
            teams: fr.WriteOnly[list[fr.IDRef[Team]]] = []

        @fr.include_view(client.app)
        class MemberView(fr.AsyncRestView):
            prefix = "/members"
            model = Member
            schema = MemberSchema

    else:

        class MemberSchema(fr.IDSchema):  # type: ignore[no-redef]
            name: str

        class MemberUpdate(fr.BaseSchema):
            name: str | None = None
            teams: list[fr.IDRef[Team]] | None = None

        @fr.include_view(client.app)
        class MemberView(fr.AsyncRestView):  # type: ignore[no-redef]
            prefix = "/members"
            model = Member
            schema = MemberSchema
            schema_update = MemberUpdate

    create_tables()
    _insert(Member, Team)

    response = client.patch("/members/1", json={"teams": [2]})

    assert response.status_code == 200, response.text
    assert response.json() == {"id": 1, "name": "ann"}
    assert _team_ids(Member) == [2]


@pytest.fixture
def sync_client(sync_db) -> Iterator[RestlyTestClient]:
    yield RestlyTestClient(FastAPI())


def test_a_sync_update_writes_a_collection_with_lazy_raise(sync_client):
    Member, Team = _models(lazy="raise")

    class MemberSchema(fr.IDSchema):
        name: str
        teams: fr.WriteOnly[list[fr.IDRef[Team]]] = []

    @fr.include_view(sync_client.app)
    class MemberView(fr.RestView):
        prefix = "/members"
        model = Member
        schema = MemberSchema

    make_session = _fr_globals.make_session
    fr.DataclassBase.metadata.create_all(make_session.kw["bind"])
    with make_session() as session:
        red, blue = Team(name="red"), Team(name="blue")
        member = Member(name="ann")
        member.teams.append(red)
        session.add_all([member, red, blue])
        session.commit()

    response = sync_client.patch("/members/1", json={"teams": [2]})

    assert response.status_code == 200, response.text
    with make_session() as session:
        member = session.scalar(select(Member).options(selectinload(Member.teams)))
        assert member is not None
        assert [team.id for team in member.teams] == [2]


def test_an_update_loads_only_the_collections_it_writes(sync_db):
    class Club(fr.IDBase):
        name: Mapped[str] = mapped_column()

    class Player(fr.IDBase):
        name: Mapped[str] = mapped_column()
        club_id: Mapped[int | None] = mapped_column(ForeignKey("club.id"), default=None)
        club: Mapped[Club | None] = relationship(default=None)
        rivals: Mapped[list[Club]] = relationship(
            secondary="player_rival", default_factory=list
        )

    class PlayerRival(fr.DataclassBase):
        __tablename__ = "player_rival"
        player_id: Mapped[int] = mapped_column(
            ForeignKey("player.id"), primary_key=True, init=False
        )
        club_id: Mapped[int] = mapped_column(
            ForeignKey("club.id"), primary_key=True, init=False
        )

    class PlayerUpdate(fr.BaseSchema):
        club: fr.IDRef[Club] | None = None
        rivals: list[fr.IDRef[Club]] | None = None

    _, make_session = sync_db
    fr.DataclassBase.metadata.create_all(make_session.kw["bind"])
    with make_session() as session:
        session.add(Player(name="ann"))
        session.commit()

    with make_session() as session:
        player = session.get(Player, 1)
        update = PlayerUpdate.model_validate({"club": 1, "rivals": [1]})

        # A scalar relationship is set without its old value.
        assert _unloaded_written_collections(player, update) == ["rivals"]
