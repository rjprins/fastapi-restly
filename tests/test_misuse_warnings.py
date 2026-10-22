"""Tests for the opt-in registration-time misuse warnings.

``fr.configure(warn_on_misuse=True)`` makes ``include_view`` lint the
registered class for the dominant misuse patterns (route-shell override,
manual ``session.commit()`` in a view method, hand-rolled CRUD on a bare
``View``, a reference type named after a scalar FK column, a writable field
on a column that the model sets, and a duplicate registration on the same
parent) and emit :class:`RestlyMisuseWarning` for each. Off by default.

The lint runs before parent endpoints are copied into the subclass, so a clean
view must not warn even though its ``__dict__`` gains the five shells during
registration -- pinned by ``test_clean_restview_does_not_warn``.
"""

import warnings
from typing import Any

import fastapi
from sqlalchemy import ForeignKey, orm

import fastapi_restly as fr
from fastapi_restly.db._globals import RestlyContext
from fastapi_restly.exc import RestlyMisuseWarning
from fastapi_restly.views import ViewCreate

from ._models import SharedBase


class GuardPost(fr.IDMixin, SharedBase):
    title: orm.Mapped[str]


class GuardComment(fr.IDMixin, SharedBase):
    body: orm.Mapped[str]
    post_id: orm.Mapped[int] = orm.mapped_column(ForeignKey(GuardPost.id))
    post: orm.Mapped[GuardPost] = orm.relationship(default=None, init=False)


def _register(view_cls: type, *, warn_on_misuse: bool | None = True) -> list[str]:
    """Register ``view_cls`` on a fresh app in an isolated context and return
    the ``RestlyMisuseWarning`` messages emitted."""
    app = fastapi.FastAPI()
    with RestlyContext():
        if warn_on_misuse is not None:
            fr.configure(warn_on_misuse=warn_on_misuse)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fr.include_view(app, view_cls)
    return [
        str(w.message) for w in caught if issubclass(w.category, RestlyMisuseWarning)
    ]


def test_shell_override_warns():
    class ShellArticle(fr.IDMixin, SharedBase):
        title: orm.Mapped[str]

    class ShellArticleView(fr.AsyncRestView):
        prefix = "/shell-articles"
        model = ShellArticle

        @fr.get("/")
        async def get_many_endpoint(self, page: int = 1) -> Any:
            return []

    messages = _register(ShellArticleView)
    assert len(messages) == 1
    assert "endpoint method 'get_many_endpoint'" in messages[0]
    # a read has no business method: the message names the seams of a read
    assert "business method" not in messages[0]
    assert "'scope'" in messages[0]
    assert "'apply_list_params'" in messages[0]
    assert "'authorize'" in messages[0]
    # the handler stays in the message as the thing a replacement calls
    assert "Call 'handle_get_many' from the replacement" in messages[0]


def test_an_override_that_keeps_the_steps_does_not_warn():
    """An endpoint method override that calls its handler, or the endpoint
    method of its base, changes only the HTTP contract. That is what the
    warning tells you to do, so it does not warn."""

    class KeptArticle(fr.IDMixin, SharedBase):
        title: orm.Mapped[str]

    class KeptArticleView(fr.AsyncRestView):
        prefix = "/kept-articles"
        model = KeptArticle

        @fr.get("/")
        async def get_many_endpoint(self, list_params: Any, archived: bool = False):
            result = await self.handle_get_many(list_params)
            return self.to_response(result, fr.ResponseShape.LIST)

        @fr.post("/", status_code=201)
        async def create_endpoint(self, data: ViewCreate) -> Any:
            return await super().create_endpoint(data)

    assert _register(KeptArticleView) == []


def test_manual_commit_warns():
    class CommitArticle(fr.IDMixin, SharedBase):
        title: orm.Mapped[str]

    class CommitArticleView(fr.AsyncRestView):
        prefix = "/commit-articles"
        model = CommitArticle

        @fr.post("/{id}/approve")
        async def approve(self, id: int) -> Any:
            obj: Any = await self.get_one(id)
            obj.title = "approved"
            await self.session.commit()
            return obj

    messages = _register(CommitArticleView)
    assert len(messages) == 1
    assert "approve calls session.commit() directly" in messages[0]
    assert "write_action" in messages[0]


def test_write_action_method_does_not_warn():
    class PublishArticle(fr.IDMixin, SharedBase):
        title: orm.Mapped[str]

    class PublishArticleView(fr.AsyncRestView):
        prefix = "/publish-articles"
        model = PublishArticle

        class Action(fr.Action):
            PUBLISH = "publish"

        @fr.post("/{id}/publish")
        async def publish(self, id: int) -> Any:
            obj: Any = await self.get_one(id)
            async with self.write_action(self.Action.PUBLISH, obj=obj):
                obj.title = "published"
            return obj

    assert _register(PublishArticleView) == []


def test_bare_view_crud_set_warns():
    class HandRolledView(fr.View):
        prefix = "/hand-rolled"

        @fr.get("/")
        async def list_items(self) -> Any:
            return []

        @fr.post("/")
        async def create_item(self) -> Any:
            return {}

        @fr.delete("/{id}")
        async def delete_item(self, id: int) -> None:
            return None

    messages = _register(HandRolledView)
    assert len(messages) == 1
    assert "hand-rolls a CRUD route set on a bare View" in messages[0]
    assert "RestView" in messages[0]


def test_bare_view_few_routes_does_not_warn():
    class PingView(fr.View):
        prefix = "/ping"

        @fr.get("/")
        async def ping(self) -> Any:
            return {"pong": True}

        @fr.post("/")
        async def echo(self) -> Any:
            return {}

    assert _register(PingView) == []


def test_clean_restview_does_not_warn():
    """A view using the intended seams stays silent -- and because the framework
    copies the parent endpoint methods into the subclass during registration, this
    also pins that the lint runs *before* that copy (otherwise every clean view
    would appear to override all five shells)."""

    class CleanArticle(fr.IDMixin, SharedBase):
        title: orm.Mapped[str]

    class CleanArticleView(fr.AsyncRestView):
        prefix = "/clean-articles"
        model = CleanArticle

        async def create(self, schema_obj: Any) -> Any:
            return await super().create(schema_obj)

    assert _register(CleanArticleView) == []
    # The copy machinery has since populated the shells on the subclass.
    assert "create_endpoint" in CleanArticleView.__dict__


def test_disabled_by_default():
    class QuietArticle(fr.IDMixin, SharedBase):
        title: orm.Mapped[str]

    class QuietArticleView(fr.AsyncRestView):
        prefix = "/quiet-articles"
        model = QuietArticle

        @fr.get("/")
        async def get_many_endpoint(self, page: int = 1) -> Any:
            return []

        @fr.post("/{id}/approve")
        async def approve(self, id: int) -> Any:
            obj = await self.get_one(id)
            await self.session.commit()
            return obj

    assert _register(QuietArticleView, warn_on_misuse=None) == []


def test_scalar_named_idref_warns():
    class ScalarIDRefSchema(fr.IDSchema):
        body: str
        post_id: fr.IDRef[GuardPost]

    class ScalarIDRefView(fr.AsyncRestView):
        prefix = "/scalar-idref"
        model = GuardComment
        schema = ScalarIDRefSchema

    messages = _register(ScalarIDRefView)
    assert len(messages) == 1
    assert "ScalarIDRefSchema.post_id is typed `IDRef[GuardPost]`" in messages[0]
    assert "scalar foreign-key column `GuardComment.post_id`" in messages[0]
    assert "fr.MustExist[int, GuardPost]" in messages[0]
    # A partner relationship exists, so the hint offers the rename too.
    assert "name the field `post`" in messages[0]


def test_scalar_named_idschema_warns():
    class ScalarIDSchemaSchema(fr.IDSchema):
        body: str
        post_id: fr.IDSchema[GuardPost]

    class ScalarIDSchemaView(fr.AsyncRestView):
        prefix = "/scalar-idschema"
        model = GuardComment
        schema = ScalarIDSchemaSchema

    messages = _register(ScalarIDSchemaView)
    assert len(messages) == 1
    assert "is typed `IDSchema[GuardPost]`" in messages[0]
    assert "fr.MustExist[int, GuardPost]" in messages[0]


def test_relationship_named_idref_does_not_warn():
    class RelIDRefSchema(fr.IDSchema):
        body: str
        post: fr.IDRef[GuardPost]

    class RelIDRefView(fr.AsyncRestView):
        prefix = "/rel-idref"
        model = GuardComment
        schema = RelIDRefSchema

    # ``post`` is a relationship, not a column -- the legitimate reference case.
    assert _register(RelIDRefView) == []


def test_mustexist_on_scalar_fk_does_not_warn():
    class CheckedFKSchema(fr.IDSchema):
        body: str
        post_id: fr.MustExist[int, GuardPost]

    class CheckedFKView(fr.AsyncRestView):
        prefix = "/checked-fk"
        model = GuardComment
        schema = CheckedFKSchema

    # MustExist is the recommended form -- a plain scalar, not a reference type.
    assert _register(CheckedFKView) == []


def test_duplicate_registration_warns():
    class DupPost(fr.IDMixin, SharedBase):
        title: orm.Mapped[str]

    class DupPostView(fr.AsyncRestView):
        prefix = "/dup-posts"
        model = DupPost

    app = fastapi.FastAPI()
    with RestlyContext():
        fr.configure(warn_on_misuse=True)
        fr.include_view(app, DupPostView)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fr.include_view(app, DupPostView)
    messages = [
        str(w.message) for w in caught if issubclass(w.category, RestlyMisuseWarning)
    ]
    assert len(messages) == 1
    assert "already registered" in messages[0]
    assert "no-op" in messages[0]


def test_duplicate_registration_silent_by_default():
    class QuietDupPost(fr.IDMixin, SharedBase):
        title: orm.Mapped[str]

    class QuietDupPostView(fr.AsyncRestView):
        prefix = "/quiet-dup-posts"
        model = QuietDupPost

    app = fastapi.FastAPI()
    with RestlyContext():
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fr.include_view(app, QuietDupPostView)
            fr.include_view(app, QuietDupPostView)
    assert [
        str(w.message) for w in caught if issubclass(w.category, RestlyMisuseWarning)
    ] == []


def test_scalar_named_reference_off_by_default():
    class QuietRefSchema(fr.IDSchema):
        body: str
        post_id: fr.IDRef[GuardPost]

    class QuietRefView(fr.AsyncRestView):
        prefix = "/quiet-ref"
        model = GuardComment
        schema = QuietRefSchema

    assert _register(QuietRefView, warn_on_misuse=None) == []


class StampOrg(fr.IDMixin, SharedBase):
    name: orm.Mapped[str]


class StampedNote(fr.IDMixin, SharedBase):
    body: orm.Mapped[str]
    # The model sets each of these: from a column default, a mapper event, a
    # default factory, and a column default on a foreign key.
    tenant_id: orm.Mapped[int] = orm.mapped_column(init=False, insert_default=lambda: 1)
    slug: orm.Mapped[str] = orm.mapped_column(init=False)
    token: orm.Mapped[str] = orm.mapped_column(init=False, default_factory=lambda: "t")
    org_id: orm.Mapped[int] = orm.mapped_column(
        ForeignKey(StampOrg.id), init=False, insert_default=lambda: 1
    )
    org: orm.Mapped[StampOrg] = orm.relationship(init=False)


def test_writable_field_on_a_model_set_column_warns():
    class StampedNoteSchema(fr.IDSchema):
        body: str
        tenant_id: int | None = None

    class StampedNoteView(fr.AsyncRestView):
        prefix = "/stamped-notes"
        model = StampedNote
        schema = StampedNoteSchema

    messages = _register(StampedNoteView)
    # The create and update classes come from one schema: one warning.
    assert len(messages) == 1
    assert "StampedNoteSchema.tenant_id is writable" in messages[0]
    assert "`StampedNote.tenant_id` has `init=False` and a default" in messages[0]
    assert "fr.ReadOnly" in messages[0]


def test_readonly_field_on_a_model_set_column_does_not_warn():
    class ReadOnlyStampSchema(fr.IDSchema):
        body: str
        tenant_id: fr.ReadOnly[int | None] = None
        # No column default: a mapper event or the view's create sets it.
        slug: str | None = None

    class ReadOnlyStampView(fr.AsyncRestView):
        prefix = "/readonly-stamps"
        model = StampedNote
        schema = ReadOnlyStampSchema

    assert _register(ReadOnlyStampView) == []


def test_declared_write_class_with_a_model_set_column_warns():
    class StampCreate(fr.BaseSchema):
        body: str
        tenant_id: int | None = None

    class DeclaredCreateView(fr.AsyncRestView):
        prefix = "/declared-create-stamps"
        model = StampedNote
        schema_create = StampCreate

    messages = _register(DeclaredCreateView)
    # The generated schema marks the column ReadOnly, so only the declared
    # create class warns.
    assert len(messages) == 1
    assert "StampCreate.tenant_id is writable" in messages[0]
    assert "Leave the field out of StampCreate" in messages[0]
    assert "fr.ReadOnly" not in messages[0]


def test_writable_model_set_field_off_by_default():
    class QuietStampSchema(fr.IDSchema):
        body: str
        tenant_id: int | None = None

    class QuietStampView(fr.AsyncRestView):
        prefix = "/quiet-stamps"
        model = StampedNote
        schema = QuietStampSchema

    assert _register(QuietStampView, warn_on_misuse=None) == []


def test_writes_through_a_relationship_or_over_a_factory_warn():
    class OrgTokenSchema(fr.IDSchema):
        body: str
        org: fr.IDRef[StampOrg] | None = None
        token: str | None = None

    class OrgTokenView(fr.AsyncRestView):
        prefix = "/org-token-stamps"
        model = StampedNote
        schema = OrgTokenSchema

    messages = _register(OrgTokenView)
    assert len(messages) == 2
    assert (
        "OrgTokenSchema.org is writable and sets `StampedNote.org_id` "
        "through the relationship" in messages[0]
    )
    assert "OrgTokenSchema.token is writable" in messages[1]


def test_only_the_write_routes_of_the_view_are_linted():
    class RouteStampSchema(fr.IDSchema):
        body: str
        tenant_id: int | None = None

    class ReadOnlyStampView(fr.AsyncRestView):
        prefix = "/read-only-stamps"
        model = StampedNote
        schema = RouteStampSchema
        exclude_routes = (fr.ViewRoute.CREATE, fr.ViewRoute.UPDATE)

    assert _register(ReadOnlyStampView) == []

    class UpdateOnlyStampView(fr.AsyncRestView):
        prefix = "/update-only-stamps"
        model = StampedNote
        schema = RouteStampSchema
        exclude_routes = (fr.ViewRoute.CREATE,)

    assert len(_register(UpdateOnlyStampView)) == 1


def test_an_inherited_schema_and_a_declared_update_class_are_linted():
    class BaseStampSchema(fr.IDSchema):
        body: str
        tenant_id: int | None = None

    class StampUpdate(fr.BaseSchema):
        body: str | None = None
        tenant_id: int | None = None

    class BaseStampView(fr.AsyncRestView):
        model = StampedNote
        schema = BaseStampSchema

    class ChildStampView(BaseStampView):
        prefix = "/child-stamps"
        schema_update = StampUpdate

    app = fastapi.FastAPI()
    with RestlyContext():
        fr.configure(warn_on_misuse=True)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fr.include_view(app, ChildStampView)
    caught = [w for w in caught if issubclass(w.category, RestlyMisuseWarning)]
    # The create class comes from the inherited schema.
    assert [str(w.message).split(" is ")[0] for w in caught] == [
        "BaseStampSchema.tenant_id",
        "StampUpdate.tenant_id",
    ]
    # The warning points at the include_view call.
    assert {w.filename for w in caught} == {__file__}
