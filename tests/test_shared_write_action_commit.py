"""Commit ownership is shared by actions on the same session, in both variants."""

from asyncio import CancelledError
from contextlib import asynccontextmanager
from inspect import isawaitable
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, Session, mapped_column

import fastapi_restly as fr
from fastapi_restly.db._session import _arm_uncommitted_warning, _warn_if_uncommitted

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


@pytest.fixture(params=["sync", "async"])
async def writes(request, tmp_path):
    class Entry(fr.IDBase):
        name: Mapped[str] = mapped_column(unique=True)

    class Audit(fr.IDBase):
        name: Mapped[str] = mapped_column(unique=True)

    class EntrySchema(fr.IDSchema):
        name: str

    events = []
    after_calls = []

    def authorize(self, action, obj=None, data=None):
        events.append(("authorize", action))

    def before(self, action, new, old=None):
        events.append(("before", action))
        if new is not None:
            self.session.add(Audit(name=f"{action}:{new.name}"))

    def after(self, action, new, old=None):
        events.append(("after", action))
        after_calls.append((action, new, old))

    async def async_authorize(self, action, obj=None, data=None):
        authorize(self, action, obj, data)

    async def async_before(self, action, new, old=None):
        before(self, action, new, old)

    async def async_after(self, action, new, old=None):
        after(self, action, new, old)

    asynchronous = request.param == "async"
    view_type = type(
        "EntryView",
        (fr.AsyncRestView if asynchronous else fr.RestView,),
        {
            "model": Entry,
            "schema": EntrySchema,
            "prefix": "/entries",
            "authorize": async_authorize if asynchronous else authorize,
            "before_action_commit": async_before if asynchronous else before,
            "after_action_commit": async_after if asynchronous else after,
        },
    )
    fr.include_view(FastAPI())(view_type)

    database = tmp_path / "writes.db"
    if asynchronous:
        engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
        target_engine = engine.sync_engine
        make_session = lambda: AsyncSession(engine, expire_on_commit=False)
    else:
        engine = target_engine = create_engine(f"sqlite:///{database}")
        make_session = lambda: Session(engine, expire_on_commit=False)

    # Give SQLite savepoints a real outer transaction, including before any DML.
    @event.listens_for(target_engine, "connect")
    def connect(connection, record):
        connection.isolation_level = None

    @event.listens_for(target_engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    try:
        if asynchronous:
            async with engine.begin() as connection:
                await connection.run_sync(fr.DataclassBase.metadata.create_all)
        else:
            fr.DataclassBase.metadata.create_all(engine)

        async with _enter(make_session()) as session:
            target = getattr(session, "sync_session", session)

            @event.listens_for(target, "after_commit")
            def committed(session):
                if not session.in_nested_transaction():
                    events.append(("commit", None))

            view = view_type(request=None, session=session)
            yield SimpleNamespace(
                view=view,
                session=session,
                target=target,
                make_session=make_session,
                model=Entry,
                audit=Audit,
                schema=view_type.schema_create,
                events=events,
                after_calls=after_calls,
                asynchronous=asynchronous,
            )
    finally:
        await _call(engine.dispose())


async def _names(writes, model):
    async with _enter(writes.make_session()) as session:
        return list(await _call(session.scalars(select(model.name).order_by(model.id))))


def _replace_hook(writes, monkeypatch, name, hook):
    async def async_hook(*args, **kwargs):
        return hook(*args, **kwargs)

    monkeypatch.setattr(writes.view, name, async_hook if writes.asynchronous else hook)


async def test_handlers_share_one_commit_and_defer_after_hooks(writes):
    view = writes.view
    async with _enter(view.shared_write_action_commit()):
        first = await _call(view.handle_create(writes.schema(name="first")))
        await _call(view.handle_create(writes.schema(name="second")))
        assert first.id is not None
        assert writes.events == [
            ("authorize", "create"),
            ("before", "create"),
            ("authorize", "create"),
            ("before", "create"),
        ]
        assert await _names(writes, writes.model) == []
        assert await _names(writes, writes.audit) == []

    assert writes.events[-3:] == [
        ("commit", None),
        ("after", "create"),
        ("after", "create"),
    ]
    assert await _names(writes, writes.model) == ["first", "second"]
    assert await _names(writes, writes.audit) == ["create:first", "create:second"]


async def test_nested_views_share_the_session_owner(writes):
    outer = writes.view
    inner = type(outer)(request=None, session=outer.session)
    async with _enter(outer.shared_write_action_commit()):
        await _call(outer.handle_create(writes.schema(name="outer")))
        async with _enter(inner.shared_write_action_commit()):
            await _call(inner.handle_create(writes.schema(name="inner")))
        assert all(phase not in ("commit", "after") for phase, _ in writes.events)

    assert writes.events.count(("commit", None)) == 1
    assert writes.events.count(("after", "create")) == 2


async def test_other_sessions_do_not_join_the_deferred_commit(writes):
    view = writes.view
    async with _enter(view.shared_write_action_commit()):
        async with _enter(writes.make_session()) as other_session:
            other = type(view)(request=None, session=other_session)
            await _call(other.handle_create(writes.schema(name="independent")))
        assert len(writes.after_calls) == 1
        await _call(view.handle_create(writes.schema(name="deferred")))
        assert len(writes.after_calls) == 1
        assert await _names(writes, writes.model) == ["independent"]

    assert len(writes.after_calls) == 2
    assert await _names(writes, writes.model) == ["independent", "deferred"]


async def test_scope_failure_leaves_rollback_to_the_session_owner(writes):
    with pytest.raises(ValueError, match="abort"):
        async with _enter(writes.view.shared_write_action_commit()):
            await _call(writes.view.handle_create(writes.schema(name="pending")))
            raise ValueError("abort")

    assert all(phase not in ("commit", "after") for phase, _ in writes.events)
    assert list(await _call(writes.session.scalars(select(writes.model.name)))) == [
        "pending"
    ]
    assert await _names(writes, writes.model) == []
    await _call(writes.session.rollback())

    # A later ordinary action must neither defer nor replay the abandoned hook.
    writes.events.clear()
    await _call(writes.view.handle_create(writes.schema(name="later")))
    assert writes.events == [
        ("authorize", "create"),
        ("before", "create"),
        ("commit", None),
        ("after", "create"),
    ]
    assert await _names(writes, writes.model) == ["later"]


async def test_rollback_after_action_returns_discards_its_after_hook(writes):
    view = writes.view
    async with _enter(view.shared_write_action_commit()):
        await _call(view.handle_create(writes.schema(name="first")))
        with pytest.raises(ValueError, match="later row step"):
            async with _enter(writes.session.begin_nested()):
                await _call(view.handle_create(writes.schema(name="discarded")))
                raise ValueError("later row step")
        await _call(view.handle_create(writes.schema(name="last")))

    assert writes.events.count(("commit", None)) == 1
    assert writes.events.count(("after", "create")) == 2
    assert await _names(writes, writes.model) == ["first", "last"]
    assert await _names(writes, writes.audit) == ["create:first", "create:last"]


async def test_create_update_and_delete_handlers_share_the_commit(writes):
    view = writes.view
    async with _enter(view.shared_write_action_commit()):
        obj = await _call(view.handle_create(writes.schema(name="created")))
        updated = await _call(
            view.handle_update(obj.id, view.schema_update(name="updated"))
        )
        assert updated is obj
        assert await _call(view.handle_delete(obj.id)) is None
        assert writes.after_calls == []

    assert writes.events.count(("commit", None)) == 1
    assert [action for action, _, _ in writes.after_calls] == [
        "create",
        "update",
        "delete",
    ]
    assert writes.after_calls[0][2] is None
    assert writes.after_calls[1][2]["name"] == "created"
    assert writes.after_calls[2][1] is None
    assert writes.after_calls[2][2]["name"] == "updated"
    assert await _names(writes, writes.model) == []


async def test_context_brackets_keep_snapshots_and_live_new_objects(writes):
    view = writes.view
    async with _enter(view.shared_write_action_commit()):
        async with _enter(view.write_action("copy")) as handle:
            handle.obj = writes.model(name="first")
            writes.session.add(handle.obj)
        obj = handle.obj
        assert obj.id is not None
        for name in ("middle", "last"):
            async with _enter(view.write_action("rename", obj=obj)) as handle:
                assert handle.obj is obj
                obj.name = name
        async with _enter(view.write_action("recompute", obj=None)):
            pass
        assert writes.after_calls == []

    assert [old["name"] if old else None for _, _, old in writes.after_calls] == [
        None,
        "first",
        "middle",
        None,
    ]
    assert [new.name if new else None for _, new, _ in writes.after_calls] == [
        "last",
        "last",
        "last",
        None,
    ]
    assert await _names(writes, writes.audit) == [
        "copy:first",
        "rename:middle",
        "rename:last",
    ]


async def test_missing_create_deposit_still_fails_before_hooks(writes):
    with pytest.raises(RuntimeError, match="create-shaped"):
        async with _enter(writes.view.shared_write_action_commit()):
            async with _enter(writes.view.write_action("create")):
                writes.session.add(writes.model(name="forgotten"))
                await _call(writes.session.flush())
    assert writes.events == [("authorize", "create")]
    assert await _names(writes, writes.model) == []


@pytest.mark.parametrize("error_type", [ValueError, CancelledError])
async def test_caught_nested_block_failure_still_aborts_outer(writes, error_type):
    view = writes.view
    with pytest.raises(RuntimeError, match="was aborted"):
        async with _enter(view.shared_write_action_commit()):
            await _call(view.handle_create(writes.schema(name="outer")))
            with pytest.raises(error_type):
                async with _enter(view.shared_write_action_commit()):
                    await _call(view.handle_create(writes.schema(name="inner")))
                    raise error_type()

    assert writes.after_calls == []
    assert ("commit", None) not in writes.events
    assert await _names(writes, writes.model) == []


@pytest.mark.parametrize("phase", ["authorize", "body", "before_action_commit"])
async def test_row_failure_keeps_other_rows_and_their_audits(
    writes, monkeypatch, phase
):
    view = writes.view
    original = getattr(view, phase) if phase != "body" else None

    def reject(action, **kwargs):
        raise ValueError("row rejected")

    async with _enter(view.shared_write_action_commit()):
        await _call(view.handle_create(writes.schema(name="first")))
        if original is not None:
            _replace_hook(writes, monkeypatch, phase, reject)
        with pytest.raises(ValueError, match="row rejected"):
            async with _enter(writes.session.begin_nested()):
                async with _enter(view.write_action("rejected")) as handle:
                    handle.obj = writes.model(name="discarded")
                    writes.session.add(handle.obj)
                    writes.session.add(writes.audit(name="discarded audit"))
                    await _call(writes.session.flush())
                    if phase == "body":
                        raise ValueError("row rejected")
        if original is not None:
            monkeypatch.setattr(view, phase, original)
        await _call(view.handle_create(writes.schema(name="last")))

    assert await _names(writes, writes.model) == ["first", "last"]
    assert await _names(writes, writes.audit) == ["create:first", "create:last"]
    assert len(writes.after_calls) == 2


async def test_audit_flush_failure_stays_inside_caller_savepoint(writes):
    writes.session.add(writes.audit(name="create:duplicate"))
    await _call(writes.session.commit())
    writes.events.clear()
    view = writes.view
    async with _enter(view.shared_write_action_commit()):
        with pytest.raises(IntegrityError):
            async with _enter(writes.session.begin_nested()):
                await _call(view.handle_create(writes.schema(name="duplicate")))
        await _call(view.handle_create(writes.schema(name="survivor")))

    assert writes.events.count(("commit", None)) == 1
    assert len(writes.after_calls) == 1
    assert await _names(writes, writes.model) == ["survivor"]
    assert await _names(writes, writes.audit) == ["create:duplicate", "create:survivor"]


async def test_enclosing_savepoint_rollback_discards_released_child_hooks(writes):
    view = writes.view
    async with _enter(view.shared_write_action_commit()):
        with pytest.raises(ValueError):
            async with _enter(writes.session.begin_nested()):
                async with _enter(writes.session.begin_nested()):
                    await _call(view.handle_create(writes.schema(name="discarded")))
                raise ValueError()
        async with _enter(writes.session.begin_nested()):
            await _call(view.handle_create(writes.schema(name="survivor")))

    assert len(writes.after_calls) == 1
    assert await _names(writes, writes.model) == ["survivor"]
    assert await _names(writes, writes.audit) == ["create:survivor"]


async def test_commit_failure_discards_hooks_and_allows_reuse_after_rollback(writes):
    view = writes.view
    with pytest.raises(IntegrityError):
        async with _enter(view.shared_write_action_commit()):
            await _call(view.handle_create(writes.schema(name="duplicate")))
            writes.session.add(writes.model(name="duplicate"))
    assert writes.after_calls == []
    assert ("commit", None) not in writes.events
    assert await _names(writes, writes.model) == []
    await _call(writes.session.rollback())
    async with _enter(view.shared_write_action_commit()):
        await _call(view.handle_create(writes.schema(name="later")))
    assert len(writes.after_calls) == 1
    assert await _names(writes, writes.model) == ["later"]


async def test_root_rollback_aborts_the_deferred_commit(writes):
    with pytest.raises(RuntimeError, match="was aborted"):
        async with _enter(writes.view.shared_write_action_commit()):
            await _call(writes.view.handle_create(writes.schema(name="discarded")))
            await _call(writes.session.rollback())

    assert ("commit", None) not in writes.events
    assert writes.after_calls == []
    assert await _names(writes, writes.model) == []
    assert await _names(writes, writes.audit) == []


async def test_after_hook_failure_leaves_writes_durable_and_stops_queue(
    writes, monkeypatch
):
    view = writes.view
    calls = []

    def fail(action, new, old=None):
        calls.append(new.name)
        raise ValueError("after-hook failed")

    _replace_hook(writes, monkeypatch, "after_action_commit", fail)
    with pytest.raises(ValueError, match="after-hook failed"):
        async with _enter(view.shared_write_action_commit()):
            for name in ("first", "second"):
                await _call(view.handle_create(writes.schema(name=name)))
    assert calls == ["first"]
    assert writes.events.count(("commit", None)) == 1
    assert await _names(writes, writes.model) == ["first", "second"]
    # Reusing the session must not replay either callback.
    async with _enter(view.shared_write_action_commit()):
        pass
    assert calls == ["first"]


@pytest.mark.parametrize("raw_write", [False, True])
async def test_block_without_actions_commits_once_and_preserves_session_info(
    writes, raw_write
):
    writes.session.info["application_key"] = object()
    original_info = dict(writes.session.info)
    async with _enter(writes.view.shared_write_action_commit()):
        if raw_write:
            writes.session.add(writes.model(name="raw"))
    assert writes.events == [("commit", None)]
    assert writes.session.info == original_info
    assert await _names(writes, writes.model) == (["raw"] if raw_write else [])


async def test_completed_block_does_not_suppress_uncommitted_warnings(
    writes, monkeypatch
):
    _arm_uncommitted_warning(writes.session)
    async with _enter(writes.view.shared_write_action_commit()):
        await _call(writes.view.handle_create(writes.schema(name="committed")))
    _warn_if_uncommitted(writes.session)  # Warnings are errors in this suite.

    def stray_write(action, new, old=None):
        writes.session.add(writes.model(name="forgotten"))

    _replace_hook(writes, monkeypatch, "after_action_commit", stray_write)
    async with _enter(writes.view.shared_write_action_commit()):
        await _call(writes.view.handle_create(writes.schema(name="also committed")))
    with pytest.warns(fr.exc.RestlyUncommittedChangesWarning):
        _warn_if_uncommitted(writes.session)
    assert await _names(writes, writes.model) == ["committed", "also committed"]


@pytest.mark.parametrize("writes", ["async"], indirect=True)
async def test_async_owner_drains_sync_hooks_through_run_sync(writes):
    seen = []

    @fr.include_view(FastAPI())
    class SyncEntryView(fr.RestView):
        prefix = "/sync_entries"
        model = writes.model
        schema = writes.view.schema

        def after_action_commit(self, action, new, old=None):
            writes.events.append(("after", "sync"))
            # A sync hook must retain SQLAlchemy's bridge when reading the DB.
            seen.extend(
                self.session.scalars(select(self.model.name).order_by(self.model.id))
            )

    def sync_create(session):
        view = SyncEntryView(request=None, session=session)
        with view.shared_write_action_commit():
            return view.handle_create(view.schema_create(name="sync"))

    async with writes.view.shared_write_action_commit():
        await writes.view.handle_create(writes.schema(name="first"))
        await writes.session.run_sync(sync_create)
        await writes.view.handle_create(writes.schema(name="last"))
        assert seen == []

    assert writes.events[-4:] == [
        ("commit", None),
        ("after", "create"),
        ("after", "sync"),
        ("after", "create"),
    ]
    assert seen == ["first", "sync", "last"]
    assert await _names(writes, writes.model) == ["first", "sync", "last"]


@pytest.mark.parametrize("writes", ["async"], indirect=True)
@pytest.mark.parametrize("nested_block", [False, True])
async def test_sync_owner_rejects_async_work_before_mutation(writes, nested_block):
    from fastapi_restly.views._lifecycle import _shared_write_action_commit

    with pytest.raises(RuntimeError, match="async outermost"):
        with _shared_write_action_commit(writes.target):
            if nested_block:
                async with writes.view.shared_write_action_commit():
                    pytest.fail("The async block must not be entered")
            else:
                await writes.view.handle_create(writes.schema(name="never"))

    assert writes.events == []
    assert not writes.session.in_transaction()
