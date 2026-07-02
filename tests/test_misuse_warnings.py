"""Tests for the opt-in registration-time misuse warnings.

``fr.configure(warn_on_misuse=True)`` makes ``include_view`` lint the
registered class for the dominant misuse patterns (route-shell override,
manual ``session.commit()`` in a view method, hand-rolled CRUD on a bare
``View``, and a reference type named after a scalar FK column) and emit
:class:`RestlyMisuseWarning` for each. Off by default.

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


class GuardPost(fr.IDBase):
    title: orm.Mapped[str]


class GuardComment(fr.IDBase):
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
    class ShellArticle(fr.IDBase):
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
    assert "'get_many'" in messages[0]
    assert "'handle_get_many'" in messages[0]


def test_manual_commit_warns():
    class CommitArticle(fr.IDBase):
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
    class PublishArticle(fr.IDBase):
        title: orm.Mapped[str]

    class PublishArticleView(fr.AsyncRestView):
        prefix = "/publish-articles"
        model = PublishArticle

        @fr.post("/{id}/publish")
        async def publish(self, id: int) -> Any:
            obj: Any = await self.get_one(id)
            async with self.write_action("publish", obj=obj):
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

    class CleanArticle(fr.IDBase):
        title: orm.Mapped[str]

    class CleanArticleView(fr.AsyncRestView):
        prefix = "/clean-articles"
        model = CleanArticle

        def build_query(self):
            return super().build_query()

        async def create(self, schema_obj: Any) -> Any:
            return await super().create(schema_obj)

    assert _register(CleanArticleView) == []
    # The copy machinery has since populated the shells on the subclass.
    assert "create_endpoint" in CleanArticleView.__dict__


def test_disabled_by_default():
    class QuietArticle(fr.IDBase):
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


def test_scalar_named_reference_off_by_default():
    class QuietRefSchema(fr.IDSchema):
        body: str
        post_id: fr.IDRef[GuardPost]

    class QuietRefView(fr.AsyncRestView):
        prefix = "/quiet-ref"
        model = GuardComment
        schema = QuietRefSchema

    assert _register(QuietRefView, warn_on_misuse=None) == []
