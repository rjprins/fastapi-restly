"""``new`` and ``old`` in the hooks, for each shape of write, and the shared
commit with the bracket.

These tests run on a SQLite file, sync and async.
"""

import pytest
from sqlalchemy import ForeignKey, event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, make_transient, mapped_column, relationship

import fastapi_restly as fr

from ._models import SharedBase
from ._write_env import call, enter, env, registered  # noqa: F401

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _doc_models():
    class Owner(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Doc(fr.IDMixin, SharedBase):
        name: Mapped[str] = mapped_column(unique=False)
        version: Mapped[int] = mapped_column(default=1)
        current: Mapped[bool] = mapped_column(default=True)
        owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id"), default=1)
        owner: Mapped[Owner] = relationship(init=False)

    class OwnerSchema(fr.IDSchema):
        name: str

    class DocSchema(fr.IDSchema):
        name: str
        version: fr.ReadOnly[int] = 1
        owner: fr.ReadOnly[OwnerSchema | None] = None

    return Owner, Doc, DocSchema


def _hooked(env, Doc, DocSchema, calls, **namespace):
    def before(self, action, new, old=None):
        calls.append(("before", action, new, old))

    def after(self, action, new, old=None):
        calls.append(("after", action, new, old))

    async def async_before(self, action, new, old=None):
        before(self, action, new, old)

    async def async_after(self, action, new, old=None):
        after(self, action, new, old)

    return registered(
        type(
            "DocView",
            (env.base,),
            {
                "prefix": "/docs",
                "model": Doc,
                "schema": DocSchema,
                "before_action_commit": env.pick(before, async_before),
                "after_action_commit": env.pick(after, async_after),
                **namespace,
            },
        )
    )


async def _seed(env, Owner, Doc):
    await env.create_all()
    session = env.make_session()
    async with enter(session):
        session.add_all([Owner(name="o1"), Owner(name="o2")])
        await call(session.flush())
        session.add_all([Doc(name="d1"), Doc(name="d2")])
        await call(session.commit())
    return env.make_session()


async def test_a_new_version_by_make_transient_in_the_block(env):
    """Versioned rows: the block turns the loaded row into a new row with
    ``make_transient``. ``old`` is the old version, ``new`` the new row, and
    the old row stays in the table."""
    Owner, Doc, DocSchema = _doc_models()
    calls = []
    session = await _seed(env, Owner, Doc)
    async with enter(session):
        view = _hooked(env, Doc, DocSchema, calls)(request=None, session=session)
        doc = await call(view.get_one(1))
        async with enter(view.write_action("revise", obj=doc)):
            session.expunge(doc)
            make_transient(doc)
            doc.id = None
            doc.version += 1
            doc.name = "d1 v2"
            session.add(doc)
        after = [c for c in calls if c[0] == "after"][0]
        assert after[2] is doc and doc.id == 3 and doc.version == 2
        assert after[3]["id"] == 1 and after[3]["version"] == 1
    check = env.make_session()
    async with enter(check):
        names = list(await call(check.scalars(select(Doc.name).order_by(Doc.id))))
    assert names == ["d1", "d2", "d1 v2"]


async def test_a_new_version_by_the_sqlalchemy_before_flush_recipe(env):
    """SQLAlchemy's "versioned rows" recipe makes a changed row transient in
    ``before_flush``. Through ``PATCH``: ``old`` is version 1, ``new`` the new
    row with a new id."""
    Owner, Doc, DocSchema = _doc_models()
    calls = []
    session = await _seed(env, Owner, Doc)
    async with enter(session):
        plain = getattr(session, "sync_session", session)

        @event.listens_for(plain, "before_flush")
        def new_version(session, flush_context, instances):
            for obj in list(session.dirty):
                if isinstance(obj, Doc) and session.is_modified(obj):
                    session.expunge(obj)
                    make_transient(obj)
                    obj.id = None
                    obj.version += 1
                    session.add(obj)

        view = _hooked(env, Doc, DocSchema, calls)(request=None, session=session)
        doc = await call(view.handle_update(1, view.schema_update(name="d1 v2")))
        after = [c for c in calls if c[0] == "after"][0]
        assert after[2] is doc and doc.id == 3 and doc.version == 2
        assert after[3]["id"] == 1


async def test_an_update_that_returns_another_row(env):
    """Copy on write: ``update`` marks the loaded row as old and returns a new
    row. ``new`` is the new row, ``old`` the loaded row's snapshot, and the
    old row is reloaded as another written row."""
    Owner, Doc, DocSchema = _doc_models()
    calls = []

    def update(self, obj, schema_obj):
        obj.current = False
        return Doc(name=schema_obj.name, version=obj.version + 1, owner_id=2)

    async def async_update(self, obj, schema_obj):
        return update(self, obj, schema_obj)

    session = await _seed(env, Owner, Doc)
    async with enter(session):
        view = _hooked(
            env, Doc, DocSchema, calls, update=env.pick(update, async_update)
        )(request=None, session=session)
        old_row = await call(session.get(Doc, 1))
        new_row = await call(view.handle_update(1, view.schema_update(name="copy")))
        after = [c for c in calls if c[0] == "after"][0]
        assert after[2] is new_row and new_row.id == 3
        assert after[3]["id"] == 1 and after[3]["current"] is True
        assert old_row.current is False
        assert view.to_single_response(new_row).owner.name == "o2"


async def test_a_detached_row_as_obj_is_written(env):
    """A row from another, closed session (a cache, a dependency with its own
    session) is detached. The bracket adds it back to the session, so the
    save writes the change, and the hooks get the row as ``new``. Plain
    SQLAlchemy needs ``session.add`` here, but the bracket saves its
    subject."""
    Owner, Doc, DocSchema = _doc_models()
    calls = []
    other = await _seed(env, Owner, Doc)
    async with enter(other):
        doc = await call(other.get(Doc, 1))
    session = env.make_session()
    async with enter(session):
        view = _hooked(env, Doc, DocSchema, calls)(request=None, session=session)
        async with enter(view.write_action("rename", obj=doc)):
            doc.name = "renamed"
        after = [c for c in calls if c[0] == "after"][0]
    check = env.make_session()
    async with enter(check):
        stored = await call(check.scalar(select(Doc.name).where(Doc.id == 1)))
    assert (after[2], stored) == (doc, "renamed")


async def test_react_admin_put_gives_new_and_old(env):
    """``PUT /{id}`` of a React-admin view goes through ``handle_update``."""
    Owner, Doc, DocSchema = _doc_models()
    calls = []
    base = fr.AsyncReactAdminView if env.asynchronous else fr.ReactAdminView

    def after(self, action, new, old=None):
        calls.append((action, new.name, old["name"]))

    async def async_after(self, action, new, old=None):
        after(self, action, new, old)

    view_type = registered(
        type(
            "DocAdmin",
            (base,),
            {
                "prefix": "/admin/docs",
                "model": Doc,
                "schema": DocSchema,
                "after_action_commit": env.pick(after, async_after),
            },
        )
    )
    session = await _seed(env, Owner, Doc)
    async with enter(session):
        view = view_type(request=None, session=session)
        response = await call(view.put(1, view.schema_update(name="put")))
        assert response.name == "put" and response.owner.name == "o1"
    assert calls == [("update", "put", "d1")]


async def test_an_update_that_forgets_to_return_the_object(env):
    """A business method changes the object in memory, so a common slip is a
    missing ``return``. Saving anyway would commit the change and give the
    hooks ``new=None``, the shape of a hard delete. The handler refuses the
    ``None`` with ``TypeError`` before the save, so nothing is committed and
    no hook runs."""
    Owner, Doc, DocSchema = _doc_models()
    calls = []

    def update(self, obj, schema_obj):
        obj.name = schema_obj.name.upper()  # and no return

    async def async_update(self, obj, schema_obj):
        update(self, obj, schema_obj)

    session = await _seed(env, Owner, Doc)
    async with enter(session):
        view = _hooked(
            env, Doc, DocSchema, calls, update=env.pick(update, async_update)
        )(request=None, session=session)
        try:
            result = await call(view.handle_update(1, view.schema_update(name="x")))
        except TypeError:
            result = "refused"
    check = env.make_session()
    async with enter(check):
        stored = await call(check.scalar(select(Doc.name).where(Doc.id == 1)))
    afters = [c[2] for c in calls if c[0] == "after"]
    assert (result, stored, afters) == ("refused", "d1", [])


async def test_authorize_for_a_create_shaped_block_gets_nothing(env):
    """A create-shaped block (the SaaS clone and upload routes) passes a new
    object as ``obj`` and no ``data``. ``authorize("create")`` then gets
    ``obj=None`` and ``data=None``: neither the payload (as on ``POST /``)
    nor the object. By design: a route that wants a create rule to run
    passes the request as ``data=``."""
    Owner, Doc, DocSchema = _doc_models()
    seen = []

    def authorize(self, action, obj=None, data=None):
        seen.append((action, obj, data))

    async def async_authorize(self, action, obj=None, data=None):
        authorize(self, action, obj, data)

    session = await _seed(env, Owner, Doc)
    async with enter(session):
        view = _hooked(
            env, Doc, DocSchema, [], authorize=env.pick(authorize, async_authorize)
        )(request=None, session=session)
        async with enter(view.write_action(fr.Action.CREATE, obj=Doc(name="clone"))):
            pass
        await call(view.handle_create(view.schema_create(name="posted")))
    assert seen[0] == ("create", None, None)
    assert seen[1][2].name == "posted"


async def test_a_create_that_returns_an_existing_row(env):
    """An idempotent ``POST``: ``create`` returns the row that has the name
    already. The hooks get ``old=None``, so a hook that reads "``old`` is
    ``None``: created" (the SaaS outbox form) reports a create for a row that
    existed. By design: ``old`` for ``handle_create`` is fixed before
    ``create`` runs."""
    Owner, Doc, DocSchema = _doc_models()
    calls = []

    def create(self, schema_obj):
        found = self.session.scalar(select(Doc).where(Doc.name == schema_obj.name))
        return found or fr.RestView.create(self, schema_obj)

    async def async_create(self, schema_obj):
        found = await self.session.scalar(
            select(Doc).where(Doc.name == schema_obj.name)
        )
        return found or await fr.AsyncRestView.create(self, schema_obj)

    session = await _seed(env, Owner, Doc)
    async with enter(session):
        view = _hooked(
            env, Doc, DocSchema, calls, create=env.pick(create, async_create)
        )(request=None, session=session)
        doc = await call(view.handle_create(view.schema_create(name="d1")))
    after = [c for c in calls if c[0] == "after"][0]
    assert doc.id == 1 and after[2] is doc and after[3] is None


async def test_children_added_through_a_relationship_in_update(env):
    """``update`` appends children through the relationship; the save cascade
    inserts them under ``no_autoflush``, and the response names them."""

    class Box(fr.IDMixin, SharedBase):
        name: Mapped[str]
        items: Mapped[list["BoxItem"]] = relationship(default_factory=list)

    class BoxItem(fr.IDMixin, SharedBase):
        label: Mapped[str]
        box_id: Mapped[int] = mapped_column(ForeignKey("box.id"), init=False)

    class BoxItemSchema(fr.IDSchema):
        label: str

    class BoxSchema(fr.IDSchema):
        name: str
        items: fr.ReadOnly[list[BoxItemSchema]] = []

    def update(self, obj, schema_obj):
        obj = fr.RestView.update(self, obj, schema_obj)
        obj.items.append(BoxItem(label="new"))
        return obj

    async def async_update(self, obj, schema_obj):
        obj = await fr.AsyncRestView.update(self, obj, schema_obj)
        obj.items.append(BoxItem(label="new"))
        return obj

    view_type = registered(
        type(
            "BoxView",
            (env.base,),
            {
                "prefix": "/boxes",
                "model": Box,
                "schema": BoxSchema,
                "update": env.pick(update, async_update),
            },
        )
    )
    await env.create_all()
    session = env.make_session()
    async with enter(session):
        session.add(Box(name="b", items=[BoxItem(label="old")]))
        await call(session.commit())
    session = env.make_session()
    async with enter(session):
        view = view_type(request=None, session=session)
        box = await call(view.handle_update(1, view.schema_update(name="b2")))
        assert [i.label for i in view.to_single_response(box).items] == ["old", "new"]


# ---------------------------------------------------------------------------
# The shared commit
# ---------------------------------------------------------------------------


async def test_a_failing_row_in_a_savepoint_inside_a_shared_commit(env):
    """The bulk shape of the SaaS example: one handler per item in a
    savepoint. The failing row's INSERT happens in the bracket's save,
    inside its savepoint. The good rows commit, and only their after-hooks
    run."""

    class Code(fr.IDMixin, SharedBase):
        code: Mapped[str] = mapped_column(unique=True)

    class CodeSchema(fr.IDSchema):
        code: str

    calls = []

    def after(self, action, new, old=None):
        calls.append(new.code)

    async def async_after(self, action, new, old=None):
        after(self, action, new, old)

    view_type = registered(
        type(
            "CodeView",
            (env.base,),
            {
                "prefix": "/codes",
                "model": Code,
                "schema": CodeSchema,
                "after_action_commit": env.pick(after, async_after),
            },
        )
    )
    await env.create_all()
    session = env.make_session()
    failed = []
    async with enter(session):
        view = view_type(request=None, session=session)
        async with enter(view.shared_write_action_commit()):
            for code in ["a", "a", "b"]:
                try:
                    async with enter(session.begin_nested()):
                        await call(view.handle_create(view.schema_create(code=code)))
                except IntegrityError:
                    failed.append(code)
    assert failed == ["a"] and calls == ["a", "b"]
    check = env.make_session()
    async with enter(check):
        assert await call(check.scalar(select(func.count()).select_from(Code))) == 2


@pytest.mark.parametrize("savepoint", [False, True], ids=["plain", "savepoint"])
async def test_a_refused_create_block_in_a_shared_commit_is_not_written(env, savepoint):
    """A bulk route catches the refusal of one item. The block raised after
    Restly added its new object to the session. Without a savepoint, nothing
    can take the object out again, so the outer commit refuses to run, and
    nothing is written. With a savepoint around each item, its rollback
    undoes the refused item, and the good item commits."""
    Owner, Doc, DocSchema = _doc_models()
    calls = []
    session = await _seed(env, Owner, Doc)
    refused = []
    async with enter(session):
        view = _hooked(env, Doc, DocSchema, calls)(request=None, session=session)

        async def create(name):
            async with enter(view.write_action("create", obj=Doc(name=name))):
                if name == "bad":
                    raise fr.exc.Conflict("bad name")

        async def bulk():
            async with enter(view.shared_write_action_commit()):
                for name in ["good", "bad"]:
                    try:
                        if savepoint:
                            async with enter(session.begin_nested()):
                                await create(name)
                        else:
                            await create(name)
                    except fr.exc.Conflict:
                        refused.append(name)

        if savepoint:
            await bulk()
        else:
            with pytest.raises(RuntimeError, match="The write was not committed"):
                await bulk()
    assert refused == ["bad"]
    afters = [c[2].name for c in calls if c[0] == "after"]
    check = env.make_session()
    async with enter(check):
        names = sorted(await call(check.scalars(select(Doc.name))))
    if savepoint:
        assert (afters, names) == (["good"], ["d1", "d2", "good"])
    else:
        assert (afters, names) == ([], ["d1", "d2"])


@pytest.mark.parametrize("savepoint", [False, True], ids=["plain", "savepoint"])
async def test_a_refused_statement_in_a_shared_commit_is_not_written(env, savepoint):
    """A block runs ``session.execute(update(...))``, which writes at once,
    without a flush, and then raises. The route catches the error. Without a
    savepoint, the outer commit refuses to run, so the half-done statement is
    not committed. With one, its rollback undoes the statement."""
    Owner, Doc, DocSchema = _doc_models()
    calls = []
    session = await _seed(env, Owner, Doc)
    async with enter(session):
        view = _hooked(env, Doc, DocSchema, calls)(request=None, session=session)

        async def rename_all():
            async with enter(view.write_action("rename_all", obj=None)):
                await call(session.execute(update(Doc).values(name="renamed")))
                raise fr.exc.Conflict("stop")

        async def bulk():
            async with enter(view.shared_write_action_commit()):
                try:
                    if savepoint:
                        async with enter(session.begin_nested()):
                            await rename_all()
                    else:
                        await rename_all()
                except fr.exc.Conflict:
                    pass

        if savepoint:
            await bulk()
        else:
            with pytest.raises(RuntimeError, match="The write was not committed"):
                await bulk()
    check = env.make_session()
    async with enter(check):
        assert sorted(await call(check.scalars(select(Doc.name)))) == ["d1", "d2"]


@pytest.mark.parametrize("savepoint", [False, True], ids=["plain", "savepoint"])
async def test_a_refused_create_in_a_shared_commit_is_not_written(env, savepoint):
    """The same through ``handle_create``: ``create`` refuses after
    ``super().create()``, which added the object to the session, and the
    route catches the 409 and goes on. Without a savepoint the outer commit
    refuses to run; with one, only the good row is written."""
    Owner, Doc, DocSchema = _doc_models()

    def create(self, schema_obj):
        doc = fr.RestView.create(self, schema_obj)
        if doc.name == "bad":
            raise fr.exc.Conflict("bad name")
        return doc

    async def async_create(self, schema_obj):
        doc = await fr.AsyncRestView.create(self, schema_obj)
        if doc.name == "bad":
            raise fr.exc.Conflict("bad name")
        return doc

    calls = []
    session = await _seed(env, Owner, Doc)
    async with enter(session):
        view = _hooked(
            env, Doc, DocSchema, calls, create=env.pick(create, async_create)
        )(request=None, session=session)

        async def bulk():
            async with enter(view.shared_write_action_commit()):
                for name in ["good", "bad"]:
                    try:
                        if savepoint:
                            async with enter(session.begin_nested()):
                                await call(
                                    view.handle_create(view.schema_create(name=name))
                                )
                        else:
                            await call(
                                view.handle_create(view.schema_create(name=name))
                            )
                    except fr.exc.Conflict:
                        pass

        if savepoint:
            await bulk()
        else:
            with pytest.raises(RuntimeError, match="The write was not committed"):
                await bulk()
    check = env.make_session()
    async with enter(check):
        names = sorted(await call(check.scalars(select(Doc.name))))
    assert names == (["d1", "d2", "good"] if savepoint else ["d1", "d2"])


async def test_two_views_of_one_model_in_a_shared_commit(env):
    """View A (its response names ``owner``) updates a doc. View B (its
    response names nothing) then changes the same doc in a block. View B's
    save expires the doc's relationships and loads none. Before the shared commit,
    Restly refreshes what A's queued hook had loaded, so A's response still
    has the owner."""
    Owner, Doc, DocSchema = _doc_models()

    class BareDocSchema(fr.IDSchema):
        name: str

    a_type, b_type = registered(
        type(
            "DocView", (env.base,), {"prefix": "/a", "model": Doc, "schema": DocSchema}
        ),
        type(
            "BareDocView",
            (env.base,),
            {"prefix": "/b", "model": Doc, "schema": BareDocSchema},
        ),
    )
    session = await _seed(env, Owner, Doc)
    async with enter(session):
        a = a_type(request=None, session=session)
        b = b_type(request=None, session=session)
        async with enter(a.shared_write_action_commit()):
            doc = await call(a.handle_update(1, a.schema_update(name="x")))
            other = await call(session.get(Doc, 2))
            async with enter(b.write_action("move", obj=other)):
                other.name = "y"
                doc.owner_id = 2
        assert a.to_single_response(doc).owner.name == "o2"


async def test_two_updates_of_one_row_in_a_shared_commit(env):
    """Both queued after-hooks get the live row as ``new`` and their own
    snapshot as ``old`` (documented). A hook that compares ``new`` with
    ``old`` sees the final value twice."""
    Owner, Doc, DocSchema = _doc_models()
    calls = []
    session = await _seed(env, Owner, Doc)
    async with enter(session):
        view = _hooked(env, Doc, DocSchema, calls)(request=None, session=session)
        async with enter(view.shared_write_action_commit()):
            await call(view.handle_update(1, view.schema_update(name="x")))
            await call(view.handle_update(1, view.schema_update(name="y")))
    afters = [(c[2].name, c[3]["name"]) for c in calls if c[0] == "after"]
    assert afters == [("y", "d1"), ("y", "x")]


async def test_a_block_after_a_handler_in_a_shared_commit_reloads_its_row(env):
    """Handler 1 returns a doc; a later block in the same shared commit moves
    that doc to another owner. The block reloads it as another written row,
    so the route's response after the commit has the new owner."""
    Owner, Doc, DocSchema = _doc_models()
    calls = []
    session = await _seed(env, Owner, Doc)
    async with enter(session):
        view = _hooked(env, Doc, DocSchema, calls)(request=None, session=session)
        async with enter(view.shared_write_action_commit()):
            doc = await call(view.handle_update(1, view.schema_update(name="x")))
            async with enter(view.write_action("move", obj=None)):
                doc.owner_id = 2
        assert view.to_single_response(doc).owner.name == "o2"


async def test_a_refusal_before_the_change_keeps_the_outer_block(env):
    """A handler inside a block refuses before it changes anything, and the
    route catches the error. The outer block's own change was pending before
    the handler began, so it does not count as the handler's half-done
    change, and the commit goes on."""
    Owner, Doc, DocSchema = _doc_models()

    def create(self, data):
        raise fr.exc.Conflict("refused before any change")

    async def async_create(self, data):
        raise fr.exc.Conflict("refused before any change")

    session = await _seed(env, Owner, Doc)
    async with enter(session):
        view = _hooked(env, Doc, DocSchema, [], create=env.pick(create, async_create))(
            request=None, session=session
        )
        doc = await call(view.get_one(1))
        async with enter(view.write_action("rename", obj=doc)):
            doc.name = "renamed"
            with pytest.raises(fr.exc.Conflict):
                await call(view.handle_create(view.schema_create(name="refused")))
    check = env.make_session()
    async with enter(check):
        names = sorted(await call(check.scalars(select(Doc.name))))
    assert names == ["d2", "renamed"]


async def test_a_business_method_must_return_one_object(env):
    """A list from ``create`` is refused before the save, so nothing is
    committed and the response does not fail after the commit."""
    Owner, Doc, DocSchema = _doc_models()

    def create(self, data):
        return [fr.RestView.create(self, data)]

    async def async_create(self, data):
        return [await fr.AsyncRestView.create(self, data)]

    session = await _seed(env, Owner, Doc)
    async with enter(session):
        view = _hooked(env, Doc, DocSchema, [], create=env.pick(create, async_create))(
            request=None, session=session
        )
        with pytest.raises(TypeError, match=r"returned a list"):
            await call(view.handle_create(view.schema_create(name="listed")))
        await call(session.rollback())
    check = env.make_session()
    async with enter(check):
        names = sorted(await call(check.scalars(select(Doc.name))))
    assert names == ["d1", "d2"]


async def test_an_obj_that_this_session_cannot_write_is_refused(env):
    """A row that another open session holds, or a detached copy of a row
    that this session already has, cannot be the subject of this session's
    action. ``write_action`` says so before anything runs."""
    Owner, Doc, DocSchema = _doc_models()
    session = await _seed(env, Owner, Doc)
    other = env.make_session()
    async with enter(session), enter(other):
        view = _hooked(env, Doc, DocSchema, [])(request=None, session=session)
        foreign = await call(other.get(Doc, 1))
        with pytest.raises(TypeError, match="belongs to another session"):
            async with enter(view.write_action("rename", obj=foreign)):
                pass
        mine = await call(view.get_one(2))
        copy = await call(other.get(Doc, 2))
        other.expunge(copy)
        assert mine is not copy
        with pytest.raises(TypeError, match="already has the Doc with this key"):
            async with enter(view.write_action("rename", obj=copy)):
                pass
