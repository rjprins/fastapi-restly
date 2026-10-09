"""An async update can write a relationship that the view does not load.

The view loads the relationships of its response class. An update can write
others: a ``WriteOnly`` field, or a field of a ``schema_update`` that the
response class does not have. To replace a collection, SQLAlchemy reads the
old one first, which on an async session fails inside the assignment. So the
async update loads these relationships before it writes them.
"""

import asyncio

import pytest
from sqlalchemy import ForeignKey, select
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

import fastapi_restly as fr

from .conftest import create_tables


def _models():
    class Team(fr.IDBase):
        name: Mapped[str] = mapped_column()

    class Member(fr.IDBase):
        name: Mapped[str] = mapped_column()
        teams: Mapped[list[Team]] = relationship(
            secondary="member_team", default_factory=list
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
