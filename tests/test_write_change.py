"""The change window, brackets inside brackets, and the reference record.

The business method and the ``write_action`` block run under
``session.no_autoflush``, so a query in the change does not see the rows
that it added until a ``flush()``. References in ``data`` are checked once,
before the change, and a per-action record stops the second check. These
tests run on a SQLite file, sync and async.
"""

import pytest
from sqlalchemy import ForeignKey, func, select
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr

from ._models import SharedBase
from ._write_env import call, enter, env, registered  # noqa: F401

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# no_autoflush
# ---------------------------------------------------------------------------


def _tag_models():
    class Tag(fr.IDMixin, SharedBase):
        name: Mapped[str] = mapped_column(unique=True)

    class Post(fr.IDMixin, SharedBase):
        title: Mapped[str]
        tag_id: Mapped[int] = mapped_column(ForeignKey("tag.id"), init=False)
        tag: Mapped[Tag] = relationship()

    class PostSchema(fr.IDSchema):
        title: str

    return Tag, Post, PostSchema


async def test_get_or_create_in_a_block_finds_the_row_after_a_flush(env):
    """A bulk import with get-or-create of a related row by a unique name: the
    second item looks up the tag that the first item added. Under
    ``no_autoflush`` the lookup does not see the new tag until a ``flush()``;
    with the flush, the second item finds it, and there is one tag."""
    Tag, Post, PostSchema = _tag_models()
    await env.create_all()
    view_type = registered(
        type(
            "PostView",
            (env.base,),
            {"prefix": "/posts", "model": Post, "schema": PostSchema},
        )
    )
    session = env.make_session()

    async def find(name):
        return await call(session.scalar(select(Tag).where(Tag.name == name)))

    async with enter(session):
        view = view_type(request=None, session=session)
        async with enter(view.write_action("import", obj=None)):
            for title, tag_name in [("a", "x"), ("b", "x")]:
                tag = await find(tag_name)
                if tag is None:
                    tag = Tag(name=tag_name)
                    session.add(tag)
                    assert await find(tag_name) is None  # not flushed yet
                    await call(session.flush())
                session.add(Post(title=title, tag=tag))
        assert await call(session.scalar(select(func.count()).select_from(Tag))) == 1
        assert await call(session.scalar(select(func.count()).select_from(Post))) == 2


async def test_a_limit_check_in_a_block_counts_the_rows_after_a_flush(env):
    """A rule "at most 3 posts per tag", checked by a COUNT after the block
    adds posts. Without a flush, the count misses the new posts. With it, the
    rule sees them and refuses the fourth post."""
    Tag, Post, PostSchema = _tag_models()
    await env.create_all()
    view_type = registered(
        type(
            "PostView",
            (env.base,),
            {"prefix": "/posts", "model": Post, "schema": PostSchema},
        )
    )
    session = env.make_session()
    async with enter(session):
        tag = Tag(name="x")
        session.add_all([tag, Post(title="old", tag=tag)])
        await call(session.commit())
        view = view_type(request=None, session=session)

        async def count():
            stmt = select(func.count()).where(Post.tag_id == tag.id)
            return await call(session.scalar(stmt))

        with pytest.raises(fr.exc.Conflict):
            async with enter(view.write_action("add_three", obj=None)):
                session.add_all(Post(title=f"p{i}", tag=tag) for i in range(3))
                assert await count() == 1  # the new posts are not flushed yet
                await call(session.flush())
                if await count() > 3:
                    raise fr.exc.Conflict("At most 3 posts per tag")


async def test_a_handler_inside_a_write_action_block_joins_its_commit(env):
    """A handler called inside a ``write_action`` block joins the commit of
    the block, as inside ``shared_write_action_commit``. When the outer block
    then fails, nothing of it is committed: not the inner create, and not
    the outer change."""
    Tag, Post, PostSchema = _tag_models()

    class TagSchema(fr.IDSchema):
        name: str

    await env.create_all()
    post_view_type, tag_view_type = registered(
        type(
            "PostView",
            (env.base,),
            {"prefix": "/posts", "model": Post, "schema": PostSchema},
        ),
        type(
            "TagView",
            (env.base,),
            {"prefix": "/tags", "model": Tag, "schema": TagSchema},
        ),
    )
    session = env.make_session()
    async with enter(session):
        tag = Tag(name="x")
        session.add(tag)
        await call(session.commit())
        posts = post_view_type(request=None, session=session)
        tags = tag_view_type(request=None, session=session)
        with pytest.raises(RuntimeError, match="later step"):
            async with enter(posts.write_action("relabel", obj=tag)):
                tag.name = "outer change"
                await call(tags.handle_create(tags.schema_create(name="inner")))
                raise RuntimeError("a later step of the outer block fails")
        await call(session.rollback())
    check = env.make_session()
    async with enter(check):
        names = sorted(await call(check.scalars(select(Tag.name))))
    assert names == ["x"]


def _country_models():
    class Country(SharedBase):
        __tablename__ = "country"
        code: Mapped[str] = mapped_column(primary_key=True)

    class City(fr.IDMixin, SharedBase):
        name: Mapped[str]
        country_code: Mapped[str] = mapped_column(ForeignKey("country.code"))

    class CitySchema(fr.IDSchema):
        name: str
        country_code: fr.MustExist[str, Country]

    class CountrySchema(fr.BaseSchema):
        code: str

    return Country, City, CitySchema, CountrySchema


async def test_a_reference_to_a_row_that_the_outer_block_added(env):
    """A natural key: the outer block adds a country, and a handler in the
    block creates a city that names it. The reference check does not see the
    country until a ``flush()``: before it, a 404; after it, the city is
    created."""
    Country, City, CitySchema, CountrySchema = _country_models()
    await env.create_all()
    city_view_type, country_view_type = registered(
        type(
            "CityView",
            (env.base,),
            {"prefix": "/cities", "model": City, "schema": CitySchema},
        ),
        type(
            "CountryView",
            (env.base,),
            {"prefix": "/countries", "model": Country, "schema": CountrySchema},
        ),
    )
    session = env.make_session()
    async with enter(session):
        cities = city_view_type(request=None, session=session)
        countries = country_view_type(request=None, session=session)
        body = cities.schema_create(name="Utrecht", country_code="NL")
        async with enter(cities.shared_write_action_commit()):
            async with enter(countries.write_action("create", obj=Country(code="NL"))):
                with pytest.raises(fr.exc.NotFound, match="country_code: NL"):
                    await call(cities.handle_create(body))
                await call(session.flush())
                await call(cities.handle_create(body))
    check = env.make_session()
    async with enter(check):
        assert list(await call(check.scalars(select(City.name)))) == ["Utrecht"]


# ---------------------------------------------------------------------------
# The reference record and the reference check of data
# ---------------------------------------------------------------------------


def _team_models(default_scope=None):
    class Team(fr.IDMixin, SharedBase):
        name: Mapped[str]
        archived: Mapped[bool] = mapped_column(default=False)

    class TeamClauses(fr.ClauseNamespace):
        model = Team
        default_scope = fr.where_clause(Team.archived.is_(False))

    class Member(fr.IDMixin, SharedBase):
        name: Mapped[str]
        team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))

    class MemberSchema(fr.IDSchema):
        name: str
        team_id: fr.MustExist[int, Team]

    class MemberRefSchema(fr.IDSchema):
        name: str
        team: fr.IDRef[Team]

    return Team, Member, MemberSchema, MemberRefSchema


async def _seed_teams(env, Team):
    await env.create_all()
    session = env.make_session()
    async with enter(session):
        session.add_all(
            [Team(name="a"), Team(name="b"), Team(name="old", archived=True)]
        )
        await call(session.commit())
    return env.make_session()


async def test_data_as_a_list_is_checked(env):
    Team, Member, MemberSchema, _ = _team_models()
    session = await _seed_teams(env, Team)
    view_type = registered(
        type(
            "MemberView",
            (env.base,),
            {"prefix": "/m", "model": Member, "schema": MemberSchema},
        )
    )
    async with enter(session):
        view = view_type(request=None, session=session)
        items = [
            view.schema_create(name="x", team_id=1),
            view.schema_create(name="y", team_id=3),
        ]
        with pytest.raises(fr.exc.NotFound, match="team_id: 3"):
            async with enter(view.write_action("bulk", obj=None, data=items)):
                pass


async def test_data_as_a_wrapper_schema_checks_its_items(env):
    """A bulk body is often a wrapper: ``{"items": [...]}``. The bracket walks
    the schemas nested in ``data``, so a block that sets the foreign keys
    itself cannot store a reference to a row that the target's
    ``default_scope`` hides (an archived team)."""
    Team, Member, MemberSchema, _ = _team_models()

    class MemberCreate(fr.BaseSchema):
        name: str
        team_id: fr.MustExist[int, Team]

    class Bulk(fr.BaseSchema):
        items: list[MemberCreate]

    session = await _seed_teams(env, Team)
    view_type = registered(
        type(
            "MemberView",
            (env.base,),
            {"prefix": "/m", "model": Member, "schema": MemberSchema},
        )
    )
    async with enter(session):
        view = view_type(request=None, session=session)
        body = Bulk(items=[MemberCreate(name="x", team_id=3)])
        with pytest.raises(fr.exc.NotFound):
            async with enter(view.write_action("bulk", obj=None, data=body)):
                for item in body.items:
                    session.add(Member(name=item.name, team_id=item.team_id))


async def test_data_without_reference_fields_is_not_checked(env):
    """``data`` of plain values, such as a dict of ids, holds no reference
    field, so Restly checks nothing. The id is your code's to check: here an
    archived team is stored."""
    Team, Member, MemberSchema, _ = _team_models()
    session = await _seed_teams(env, Team)
    view_type = registered(
        type(
            "MemberView",
            (env.base,),
            {"prefix": "/m", "model": Member, "schema": MemberSchema},
        )
    )
    async with enter(session):
        view = view_type(request=None, session=session)
        async with enter(view.write_action("bulk", obj=None, data={"team_id": 3})):
            session.add(Member(name="x", team_id=3))
    check = env.make_session()
    async with enter(check):
        assert await call(check.scalar(select(Member.team_id))) == 3


async def test_a_row_that_the_block_deletes_and_then_references(env):
    """The block deletes team 1, then builds a member that names it, with
    ``fr.objects`` (which keeps its checks). The record says "checked", and
    under ``no_autoflush`` a new SELECT would still see the row. The
    foreign key stops it at the save: a 409, not a 404."""
    Team, Member, MemberSchema, _ = _team_models()
    session = await _seed_teams(env, Team)
    view_type = registered(
        type(
            "MemberView",
            (env.base,),
            {"prefix": "/m", "model": Member, "schema": MemberSchema},
        )
    )
    async with enter(session):
        view = view_type(request=None, session=session)
        schema_obj = view.schema_create(name="x", team_id=1)
        build = (
            fr.objects.async_make_new_object
            if env.asynchronous
            else fr.objects.make_new_object
        )
        with pytest.raises(fr.exc.Conflict):
            async with enter(view.write_action("move", obj=None, data=schema_obj)):
                team = await call(session.get(Team, 1))
                await call(session.delete(team))
                await call(build(session, Member, schema_obj))


async def test_an_idref_to_a_row_that_the_block_deleted(env):
    """The same with an ``IDRef``: the record hands back the deleted ``Team``
    object, and make_new_object sets it on the new member. The foreign key stops
    it at the save: a 409."""
    Team, Member, _, MemberRefSchema = _team_models()

    class MemberWithTeam(fr.IDMixin, SharedBase):
        __tablename__ = "member_with_team"
        name: Mapped[str]
        team_id: Mapped[int] = mapped_column(ForeignKey("team.id"), init=False)
        team: Mapped[Team] = relationship()

    session = await _seed_teams(env, Team)
    view_type = registered(
        type(
            "MemberView",
            (env.base,),
            {"prefix": "/m", "model": MemberWithTeam, "schema": MemberRefSchema},
        )
    )
    async with enter(session):
        view = view_type(request=None, session=session)
        schema_obj = view.schema_create(name="x", team={"id": 1})
        build = (
            fr.objects.async_make_new_object
            if env.asynchronous
            else fr.objects.make_new_object
        )
        with pytest.raises(fr.exc.Conflict, match="Foreign key"):
            async with enter(view.write_action("move", obj=None, data=schema_obj)):
                team = await call(session.get(Team, 1))
                await call(session.delete(team))
                await call(build(session, MemberWithTeam, schema_obj))


async def test_two_actions_in_one_shared_commit_check_each_others_rows(env):
    """Action 1 deletes team 2; action 2 then names it. Each action has its
    own record, and action 1 flushed, so action 2's check gives 404."""
    Team, Member, MemberSchema, _ = _team_models()

    class TeamSchema(fr.IDSchema):
        name: str

    session = await _seed_teams(env, Team)
    member_type, team_type = registered(
        type(
            "MemberView",
            (env.base,),
            {"prefix": "/m", "model": Member, "schema": MemberSchema},
        ),
        type(
            "TeamView",
            (env.base,),
            {"prefix": "/t", "model": Team, "schema": TeamSchema},
        ),
    )
    async with enter(session):
        members = member_type(request=None, session=session)
        teams = team_type(request=None, session=session)
        with pytest.raises(fr.exc.NotFound, match="team_id: 2"):
            async with enter(members.shared_write_action_commit()):
                await call(teams.handle_delete(2))
                await call(
                    members.handle_create(members.schema_create(name="x", team_id=2))
                )


async def test_a_reference_changed_after_the_check_inside_update(env):
    """``update`` changes the payload's reference after the bracket checked it
    (``model_copy`` with another team), then calls ``super().update``. The
    record is keyed by value, so ``fr.objects`` checks the new value."""
    Team, Member, MemberSchema, _ = _team_models()

    def update(self, obj, schema_obj):
        return fr.RestView.update(
            self, obj, schema_obj.model_copy(update={"team_id": 3})
        )

    async def async_update(self, obj, schema_obj):
        return await fr.AsyncRestView.update(
            self, obj, schema_obj.model_copy(update={"team_id": 3})
        )

    session = await _seed_teams(env, Team)
    view_type = registered(
        type(
            "MemberView",
            (env.base,),
            {
                "prefix": "/m",
                "model": Member,
                "schema": MemberSchema,
                "update": env.pick(update, async_update),
            },
        )
    )
    async with enter(session):
        session.add(Member(name="x", team_id=1))
        await call(session.commit())
        view = view_type(request=None, session=session)
        with pytest.raises(fr.exc.NotFound, match="team_id: 3"):
            await call(view.handle_update(1, view.schema_update(name="x", team_id=2)))
