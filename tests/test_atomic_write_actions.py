"""``atomic_write_actions = False``: code in a write action may commit.

The steps stay, but no block is open, so the code works as in a plain route.
The tests of this module run on SQLite here, and on PostgreSQL through
tests/postgres and tests/postgres_asyncpg. tests/test_atomic_write_actions_http.py
has the tests that bring their own setup.
"""

import warnings
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import ForeignKey, create_engine, delete, inspect, select
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship
from sqlalchemy.orm.exc import StaleDataError

import fastapi_restly as fr
from fastapi_restly.db._session import _arm_uncommitted_warning, _warn_if_uncommitted
from fastapi_restly.exc import (
    Conflict,
    RestlyConfigurationError,
    RestlyUncommittedChangesWarning,
)

from ._models import SharedBase
from ._write_env import call, enter, registered

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _drive(coroutine):
    """Run a step from a sync view. It does not suspend there, because all that
    it awaits is the plain value of a sync session."""
    try:
        coroutine.send(None)
    except StopIteration as stop:
        return stop.value
    raise RuntimeError("the steps of a sync view suspended")


@asynccontextmanager
async def tags_env(engine, base, *, asynchronous):
    """Declare the model and the views on ``base``, create the table on
    ``engine``, and yield what the tests use, with an open session.

    A test sets ``steps["create"]`` and so on: the code of its action. A step is
    an ``async def``, also for the sync views. A ``create`` step may return
    another row, for the view to return instead.
    """

    class Tag(fr.IDMixin, base):
        name: Mapped[str] = mapped_column(unique=True)
        note: Mapped[str | None] = mapped_column(default=None)
        parent_id: Mapped[int | None] = mapped_column(
            ForeignKey("tag.id"), default=None
        )
        parent: Mapped["Tag | None"] = relationship(
            lambda: Tag, remote_side=lambda: Tag.id, default=None
        )

    class TagSchema(fr.IDSchema):
        name: str

    steps = {}
    calls = []
    news = []

    async def run(name, *args):
        if name in steps:
            return await steps[name](*args)

    if asynchronous:

        class TagView(fr.AsyncRestView):
            model = Tag
            schema = TagSchema
            prefix = "/tags"
            atomic_write_actions = False

            async def authorize(self, action, obj=None, data=None):
                await run("authorize", self.session)

            async def create(self, data):
                tag = await super().create(data)
                other = await run("create", self.session, tag)
                return tag if other is None else other

            async def update(self, obj, data):
                tag = await super().update(obj, data)
                other = await run("update", self.session, tag)
                return tag if other is None else other

            async def delete(self, obj):
                await super().delete(obj)
                await run("delete", self.session, obj)

            async def before_action_commit(self, action, new, old=None):
                calls.append(f"before {action}")
                await run("before", self.session, new)

            async def after_action_commit(self, action, new, old=None):
                calls.append(f"after {action}")
                news.append(new)
                await run("after", self.session, new)

        class StrictView(fr.AsyncRestView):
            model = Tag
            schema = TagSchema
            prefix = "/strict-tags"

            async def create(self, data):
                tag = await super().create(data)
                await run("create", self.session, tag)
                return tag

    else:

        class TagView(fr.RestView):
            model = Tag
            schema = TagSchema
            prefix = "/tags"
            atomic_write_actions = False

            def authorize(self, action, obj=None, data=None):
                _drive(run("authorize", self.session))

            def create(self, data):
                tag = super().create(data)
                other = _drive(run("create", self.session, tag))
                return tag if other is None else other

            def update(self, obj, data):
                tag = super().update(obj, data)
                other = _drive(run("update", self.session, tag))
                return tag if other is None else other

            def delete(self, obj):
                super().delete(obj)
                _drive(run("delete", self.session, obj))

            def before_action_commit(self, action, new, old=None):
                calls.append(f"before {action}")
                _drive(run("before", self.session, new))

            def after_action_commit(self, action, new, old=None):
                calls.append(f"after {action}")
                news.append(new)
                _drive(run("after", self.session, new))

        class StrictView(fr.RestView):
            model = Tag
            schema = TagSchema
            prefix = "/strict-tags"

            def create(self, data):
                tag = super().create(data)
                _drive(run("create", self.session, tag))
                return tag

    registered(TagView, StrictView)

    if asynchronous:
        make_session = lambda: AsyncSession(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(base.metadata.create_all)
    else:
        make_session = lambda: Session(engine, expire_on_commit=False)
        base.metadata.create_all(engine)

    async with enter(make_session()) as session:
        yield SimpleNamespace(
            view=TagView(request=None, session=session),
            strict=StrictView(request=None, session=session),
            session=session,
            make_session=make_session,
            model=Tag,
            schema=TagView.create_schema,
            update_schema=TagView.update_schema,
            steps=steps,
            calls=calls,
            news=news,
        )


@asynccontextmanager
async def tags_on_new_tables(url, *, asynchronous):
    """``tags_env`` on a new engine for ``url`` and a base of its own, for a
    database that outlives the test: the table is dropped at the end."""

    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    engine = create_async_engine(url) if asynchronous else create_engine(url)

    async def drop_all():
        if asynchronous:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.drop_all)
        else:
            Base.metadata.drop_all(engine)

    try:
        async with tags_env(engine, Base, asynchronous=asynchronous) as env:
            yield env
    finally:
        await drop_all()
        await call(engine.dispose())


@pytest.fixture(params=["sync", "async"])
async def tags(request, tmp_path):
    """Plain engines, as in production."""
    asynchronous = request.param == "async"
    database = tmp_path / "tags.db"
    if asynchronous:
        engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    else:
        engine = create_engine(f"sqlite:///{database}")
    try:
        async with tags_env(engine, SharedBase, asynchronous=asynchronous) as env:
            yield env
    finally:
        await call(engine.dispose())


async def _rows(tags):
    """What other connections see: only what is committed. By name, because
    SQLAlchemy may insert the rows of a self-referential table in any order."""
    async with enter(tags.make_session()) as session:
        stmt = select(tags.model.name, tags.model.note).order_by(tags.model.name)
        return [tuple(row) for row in await call(session.execute(stmt))]


async def _seed(tags, *names):
    """Save rows in a session of their own, and return their ids."""
    async with enter(tags.make_session()) as session:
        rows = [tags.model(name=name) for name in names]
        session.add_all(rows)
        await call(session.commit())
        return [row.id for row in rows]


async def _commit(session, _row=None):
    await call(session.commit())


async def _add_and_commit(session, tag):
    session.add(tag)
    await call(session.commit())


async def _create(tags, name, view=None):
    return await call((view or tags.view).handle_create(tags.schema(name=name)))


def _expire_on_commit(tags):
    getattr(tags.session, "sync_session", tags.session).expire_on_commit = True


# The action may commit, and the steps stay.


async def test_a_commit_in_create_saves_the_row_and_the_steps_go_on(tags):
    tags.steps["create"] = _add_and_commit
    tag = await _create(tags, "a")
    assert tag.id is not None
    assert await _rows(tags) == [("a", None)]
    assert tags.calls == ["before create", "after create"]
    assert tags.news == [tag]


async def test_a_commit_in_update_saves_the_change(tags):
    [tag_id] = await _seed(tags, "a")
    tags.steps["update"] = _commit
    tag = await call(tags.view.handle_update(tag_id, tags.update_schema(name="b")))
    assert tag.name == "b"
    assert await _rows(tags) == [("b", None)]


async def test_a_commit_in_delete_gives_the_hooks_no_row(tags):
    [tag_id] = await _seed(tags, "a")
    tags.steps["delete"] = _commit
    await call(tags.view.handle_delete(tag_id))
    assert await _rows(tags) == []
    assert tags.news == [None]


async def test_a_commit_in_delete_under_expire_on_commit(tags):
    _expire_on_commit(tags)
    [tag_id] = await _seed(tags, "a")
    tags.steps["delete"] = _commit
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        await call(tags.view.handle_delete(tag_id))
    assert await _rows(tags) == []
    assert tags.news == [None]


async def test_more_than_one_commit(tags):
    async def two_commits(session, tag):
        session.add(tag)
        await call(session.commit())
        tag.note = "second"
        await call(session.commit())

    tags.steps["create"] = two_commits
    await _create(tags, "a")
    assert await _rows(tags) == [("a", "second")]


async def test_a_commit_in_a_write_action_block(tags):
    tag = tags.model(name="a")
    async with enter(tags.view.write_action("create", obj=tag)):
        await call(tags.session.flush())
        await call(tags.session.commit())
        assert await _rows(tags) == [("a", None)]
        tag.note = "after"
    assert await _rows(tags) == [("a", "after")]
    assert tags.calls == ["before create", "after create"]


async def test_the_refresh_of_the_save_works_after_a_commit(tags):
    """The save reads the row back, in a new transaction."""
    _expire_on_commit(tags)
    tags.steps["create"] = _add_and_commit
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tag = await _create(tags, "a")
    assert await _rows(tags) == [("a", None)]
    assert tag in tags.news


async def test_the_save_reads_the_other_written_rows_again(tags):
    """As on a strict view: a changed foreign key never shows the old row."""
    [a_id, b_id, c_id] = await _seed(tags, "a", "b", "c")
    other = await call(tags.session.get(tags.model, b_id))
    other.parent = await call(tags.session.get(tags.model, a_id))
    await call(tags.session.flush())

    async def move(session, tag):
        other.parent_id = c_id
        await call(session.flush())
        await call(session.commit())

    tags.steps["update"] = move
    await call(tags.view.handle_update(a_id, tags.update_schema(name="d")))
    assert "parent" not in inspect(other).dict


async def test_what_a_before_hook_writes_is_saved(tags):
    async def note(session, new):
        new.note = "hook"

    tags.steps["create"] = _add_and_commit
    tags.steps["before"] = note
    await _create(tags, "a")
    assert await _rows(tags) == [("a", "hook")]


async def test_a_query_does_not_flush_as_on_a_strict_view(tags):
    """The setting changes only the savepoint and the refusal, so a query does
    not flush a half-built object. A commit still writes it."""
    getattr(tags.session, "sync_session", tags.session).autoflush = True
    counts = []

    async def count_then_commit(session, tag):
        session.add(tag)
        stmt = select(tags.model.id)
        counts.append(len(list(await call(session.scalars(stmt)))))
        await call(session.commit())

    tags.steps["create"] = count_then_commit
    await _create(tags, "a")
    assert counts == [0]
    assert await _rows(tags) == [("a", None)]


# What a commit means for an error.


async def test_an_error_after_a_commit_keeps_the_commit_and_undoes_the_rest(tags):
    async def commit_then_fail(session, tag):
        session.add(tag)
        await call(session.commit())
        tag.note = "not saved"
        await call(session.flush())
        raise HTTPException(418)

    tags.steps["create"] = commit_then_fail
    with pytest.raises(HTTPException):
        await _create(tags, "a")
    assert await _rows(tags) == [("a", None)]
    assert tags.calls == []


async def test_a_before_hook_that_raises_cannot_undo_a_commit(tags):
    async def fail(session, new):
        new.note = "not saved"
        raise HTTPException(418)

    tags.steps["create"] = _add_and_commit
    tags.steps["before"] = fail
    with pytest.raises(HTTPException):
        await _create(tags, "a")
    assert await _rows(tags) == [("a", None)]
    assert tags.calls == ["before create"]


async def test_an_error_without_a_commit_saves_nothing(tags):
    async def fail(session, tag):
        session.add(tag)
        await call(session.flush())
        raise HTTPException(418)

    tags.steps["create"] = fail
    with pytest.raises(HTTPException):
        await _create(tags, "a")
    assert await _rows(tags) == []


async def test_an_error_rolls_back_the_work_of_the_route_too(tags):
    """There is no savepoint, so the rollback undoes all that is not committed."""
    tags.session.add(tags.model(name="before"))
    await call(tags.session.flush())

    async def fail(session, tag):
        raise HTTPException(418)

    tags.steps["create"] = fail
    with pytest.raises(HTTPException):
        await _create(tags, "a")
    await call(tags.session.commit())
    assert await _rows(tags) == []


async def test_a_caught_error_leaves_no_half_done_change_behind(tags):
    """Nothing of the failed action is saved by a later commit on the session."""

    async def fail(session, tag):
        tag.note = "half done"
        session.add(tag)
        raise HTTPException(418)

    tags.steps["create"] = fail
    with pytest.raises(HTTPException):
        await _create(tags, "a")
    del tags.steps["create"]
    await _create(tags, "b")
    assert await _rows(tags) == [("b", None)]


async def test_a_refused_authorize_keeps_what_the_route_did_before(tags):
    """Nothing of the action began, so nothing is undone."""
    [tag_id] = await _seed(tags, "a")
    row = await call(tags.session.get(tags.model, tag_id))

    async def refuse(session):
        raise HTTPException(403)

    tags.steps["authorize"] = refuse
    with pytest.raises(HTTPException):
        await _create(tags, "b")
    assert row in tags.session
    assert "name" in row.__dict__


async def test_a_conflict_at_a_commit_in_the_action_is_a_conflict(tags):
    await _seed(tags, "a")
    tags.steps["create"] = _add_and_commit
    with pytest.raises(Conflict):
        await _create(tags, "a")
    assert await _rows(tags) == [("a", None)]


async def test_a_conflict_at_the_second_commit_keeps_the_first(tags):
    await _seed(tags, "taken")

    async def two_commits(session, tag):
        session.add(tag)
        await call(session.commit())
        tag.name = "taken"
        await call(session.commit())

    tags.steps["create"] = two_commits
    with pytest.raises(Conflict):
        await _create(tags, "a")
    assert await _rows(tags) == [("a", None), ("taken", None)]


async def test_an_error_in_the_after_hook_leaves_the_write_saved(tags):
    async def fail(session, new):
        raise HTTPException(418)

    tags.steps["after"] = fail
    with pytest.raises(HTTPException):
        await _create(tags, "a")
    assert await _rows(tags) == [("a", None)]


async def test_an_integrity_error_in_the_after_hook_stays_raw(tags):
    """The write is saved, so a 409 would be wrong, also from an outer action."""

    async def fail(session, new):
        raise IntegrityError("stmt", {}, Exception("from the hook"))

    tags.steps["after"] = fail
    tag = tags.model(name="outer")
    with pytest.raises(IntegrityError):
        async with enter(tags.view.write_action("create", obj=tag)):
            await _create(tags, "a")
    assert await _rows(tags) == [("a", None), ("outer", None)]


# SQLAlchemy decides: nothing guards the code of the action.


async def test_session_begin_raises_while_a_transaction_is_open(tags):
    [tag_id] = await _seed(tags, "a")

    async def begin(session, tag):
        async with enter(session.begin()):
            pass

    tags.steps["update"] = begin
    with pytest.raises(InvalidRequestError, match="already begun"):
        await call(tags.view.handle_update(tag_id, tags.update_schema(name="b")))
    assert await _rows(tags) == [("a", None)]


async def test_a_savepoint_of_the_caller_works(tags):
    async def savepoint(session, tag):
        session.add(tag)
        async with enter(session.begin_nested()):
            tag.note = "in savepoint"
            await call(session.flush())

    tags.steps["create"] = savepoint
    await _create(tags, "a")
    assert await _rows(tags) == [("a", "in savepoint")]


async def test_a_row_the_code_expunged_stays_out(tags):
    """A plain route would not touch it, and the code may still use it, as a
    task does that reads it later."""
    [tag_id] = await _seed(tags, "a")

    async def expunge(session, tag):
        await call(session.commit())
        session.expunge(tag)

    tags.steps["update"] = expunge
    tag = await call(tags.view.handle_update(tag_id, tags.update_schema(name="b")))
    assert tag not in tags.session
    assert inspect(tag).detached
    assert tag.name == "b"
    assert tags.news == [tag]
    assert await _rows(tags) == [("b", None)]


async def test_an_expired_session_does_not_reach_a_row_that_stays_out(tags):
    """Why the row stays out: a later ``expire_all`` must not expire it."""
    [tag_id] = await _seed(tags, "a")

    async def expunge(session, tag):
        await call(session.commit())
        session.expunge(tag)

    tags.steps["update"] = expunge
    tag = await call(tags.view.handle_update(tag_id, tags.update_schema(name="b")))
    tags.session.expire_all()
    assert tag.name == "b"


async def test_a_change_to_an_expunged_row_is_saved(tags):
    [tag_id] = await _seed(tags, "a")

    async def expunge(session, tag):
        await call(session.commit())
        session.expunge(tag)
        tag.note = "after the expunge"

    tags.steps["update"] = expunge
    tag = await call(tags.view.handle_update(tag_id, tags.update_schema(name="b")))
    assert tag in tags.session
    assert await _rows(tags) == [("b", "after the expunge")]


async def test_a_change_to_an_expunged_row_that_is_gone_is_an_error(tags):
    """Never a response that says a change was saved when it was not."""
    [tag_id] = await _seed(tags, "a")

    async def gone(session, tag):
        await call(session.commit())
        session.expunge(tag)
        async with enter(tags.make_session()) as other:
            await call(other.execute(delete(tags.model)))
            await call(other.commit())
        tag.note = "goes nowhere"

    tags.steps["update"] = gone
    with pytest.raises(StaleDataError):
        await call(tags.view.handle_update(tag_id, tags.update_schema(name="b")))
    assert await _rows(tags) == []
    assert tags.calls == []


async def test_a_row_of_another_session_stays_there(tags):
    """A task may hold the row. Restly does not take it from there."""
    [tag_id] = await _seed(tags, "a")

    async with enter(tags.make_session()) as other:

        async def in_another_session(session, tag):
            await call(session.commit())
            session.expunge(tag)
            other.add(tag)

        tags.steps["update"] = in_another_session
        tag = await call(tags.view.handle_update(tag_id, tags.update_schema(name="b")))
        assert tag in other
        assert tag not in tags.session
        assert tags.news == [tag]
    assert await _rows(tags) == [("b", None)]


async def test_a_stale_row_of_another_session_is_left_alone(tags):
    """The code changed the foreign key and not the loaded relationship. The
    save must not expire that relationship through its own session."""
    [tag_id, parent_id] = await _seed(tags, "a", "parent")

    async with enter(tags.make_session()) as other:

        async def in_another_session(session, tag):
            assert tag.parent is None
            tag.parent_id = parent_id
            await call(session.flush())
            await call(session.commit())
            session.expunge(tag)
            other.add(tag)

        tags.steps["update"] = in_another_session
        await call(tags.view.handle_update(tag_id, tags.update_schema(name="b")))
    assert tags.calls == ["before update", "after update"]
    assert await _rows(tags) == [("b", None), ("parent", None)]


async def test_the_commit_does_not_refresh_a_row_of_another_session(tags):
    """The outer action refreshes what the inner one loaded, but not a row that
    is in another session now."""
    [tag_id] = await _seed(tags, "a")

    async with enter(tags.make_session()) as other:

        async def in_another_session(session, tag):
            await call(session.commit())
            session.expunge(tag)
            other.add(tag)

        tags.steps["update"] = in_another_session
        outer = tags.model(name="o")
        async with enter(tags.view.write_action("create", obj=outer)):
            update = tags.update_schema(name="b")
            tag = await call(tags.view.handle_update(tag_id, update))
            other.expire(tag)
    assert await _rows(tags) == [("b", None), ("o", None)]


async def test_a_row_of_another_session_keeps_what_it_loaded(tags):
    """The save does not expire or reload a row that it left alone."""
    [tag_id] = await _seed(tags, "a")

    async with enter(tags.make_session()) as other:

        async def in_another_session(session, tag):
            tag.parent = None
            await call(session.flush())
            await call(session.commit())
            session.expunge(tag)
            other.add(tag)

        tags.steps["update"] = in_another_session
        tag = await call(tags.view.handle_update(tag_id, tags.update_schema(name="b")))
        assert "parent" in inspect(tag).dict


async def test_a_change_to_a_row_that_a_commit_deleted_is_an_error(tags):
    """Under expire_on_commit, the commit detaches the deleted row."""
    _expire_on_commit(tags)
    [tag_id] = await _seed(tags, "a")

    async def delete_then_change(session, tag):
        await call(session.delete(tag))
        await call(session.commit())
        tag.note = "goes nowhere"

    tags.steps["update"] = delete_then_change
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(InvalidRequestError, match="has been deleted"):
            await call(tags.view.handle_update(tag_id, tags.update_schema(name="b")))
    assert await _rows(tags) == []


async def test_a_new_row_of_a_helper_with_its_own_session_is_returned_as_it_is(tags):
    """The helper saved the row. The save does not need it in this session."""

    async def own_session(session, tag):
        session.expunge(tag)
        async with enter(tags.make_session()) as other:
            row = tags.model(name="own")
            other.add(row)
            await call(other.commit())
        return row

    tags.steps["create"] = own_session
    tag = await _create(tags, "unused")
    assert tag.name == "own"
    assert tag not in tags.session
    assert tags.news == [tag]
    assert await _rows(tags) == [("own", None)]


async def test_the_row_that_a_method_returns_is_saved_after_a_rollback(tags):
    """A rollback does not end the action here. The save writes what the method
    returns, so a method that rolled back must raise instead of return."""

    async def roll_back(session, tag):
        session.add(tag)
        await call(session.flush())
        await call(session.rollback())

    tags.steps["create"] = roll_back
    await _create(tags, "a")
    assert await _rows(tags) == [("a", None)]


# The outermost block decides, and the blocks inside it join it.


async def test_a_shared_block_on_the_view_still_refuses_a_commit(tags):
    with pytest.raises(RuntimeError, match="owns the commit"):
        async with enter(tags.view.shared_write_action_commit()):
            await _create(tags, "a")
            await call(tags.session.commit())
    assert await _rows(tags) == []


async def test_an_action_inside_a_shared_block_joins_it(tags):
    tags.steps["create"] = _add_and_commit
    with pytest.raises(RuntimeError, match="owns the commit"):
        async with enter(tags.view.shared_write_action_commit()):
            await _create(tags, "a")
    assert await _rows(tags) == []


async def test_actions_inside_a_shared_block_commit_together(tags):
    async with enter(tags.view.shared_write_action_commit()):
        await _create(tags, "a")
        await _create(tags, "b")
        assert tags.calls == ["before create", "before create"]
    assert tags.calls[2:] == ["after create", "after create"]
    assert await _rows(tags) == [("a", None), ("b", None)]


async def test_a_handler_inside_a_write_action_joins_it(tags):
    """Nothing commits before the outermost action ends, as on a strict view."""
    with pytest.raises(HTTPException):
        async with enter(tags.view.write_action("create", obj=tags.model(name="o"))):
            await _create(tags, "a")
            assert tags.calls == ["before create"]
            raise HTTPException(418)
    assert tags.calls == ["before create"]
    assert await _rows(tags) == []


async def test_the_after_hooks_run_after_the_outermost_commit(tags):
    async with enter(tags.view.write_action("create", obj=tags.model(name="o"))):
        await _create(tags, "a")
        await _create(tags, "b")
    assert tags.calls == ["before create"] * 3 + ["after create"] * 3
    assert await _rows(tags) == [("a", None), ("b", None), ("o", None)]


async def test_a_strict_action_inside_a_non_atomic_action_may_commit(tags):
    async def nested(session, tag):
        session.add(tag)
        await call(session.commit())

    tags.steps["create"] = nested
    async with enter(tags.view.write_action("create", obj=tags.model(name="o"))):
        await _create(tags, "inner", view=tags.strict)
    assert await _rows(tags) == [("inner", None), ("o", None)]


async def test_a_shared_block_inside_a_non_atomic_action_joins_it(tags):
    """The outermost action decides, so a commit in the block is not refused."""
    async with enter(tags.view.write_action("create", obj=tags.model(name="o"))):
        async with enter(tags.view.shared_write_action_commit()):
            tags.session.add(tags.model(name="in block"))
            await call(tags.session.commit())
    assert await _rows(tags) == [("in block", None), ("o", None)]


async def test_a_non_atomic_action_inside_a_strict_action_may_not_commit(tags):
    tags.steps["create"] = _add_and_commit
    tag = tags.model(name="o")
    with pytest.raises(RuntimeError, match="owns the commit"):
        async with enter(tags.strict.write_action("create", obj=tag)):
            await _create(tags, "inner")
    assert await _rows(tags) == []


# The default stays strict.


async def test_a_view_refuses_a_commit_by_default(tags):
    tags.steps["create"] = _add_and_commit
    with pytest.raises(RuntimeError, match="atomic_write_actions = False"):
        await _create(tags, "a", view=tags.strict)
    assert await _rows(tags) == []


@pytest.mark.parametrize("value", [None, 0, "false"])
def test_the_setting_must_be_a_bool(value):
    """``None`` is the default of other settings, so it must not turn the
    refusal off without a word."""
    with pytest.raises(RestlyConfigurationError, match="True or False"):

        class TagView(fr.RestView):
            atomic_write_actions = value


def test_the_setting_is_true_by_default_and_a_subclass_inherits_it():
    assert fr.RestView.atomic_write_actions is True
    assert fr.AsyncRestView.atomic_write_actions is True

    class Base(fr.RestView):
        atomic_write_actions = False

    class Child(Base):
        pass

    class Strict(Base):
        atomic_write_actions = True

    assert Child.atomic_write_actions is False
    assert Strict.atomic_write_actions is True


# The other parts of Restly.


async def test_no_uncommitted_warning_after_the_action(tags):
    _arm_uncommitted_warning(tags.session)
    tags.steps["create"] = _add_and_commit
    await _create(tags, "a")
    with warnings.catch_warnings():
        warnings.simplefilter("error", RestlyUncommittedChangesWarning)
        _warn_if_uncommitted(tags.session)
