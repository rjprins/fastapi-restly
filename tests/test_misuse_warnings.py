"""Tests for the opt-in registration-time misuse warnings.

``fr.configure(warn_on_misuse=True)`` makes ``include_view`` lint the
registered class for the three dominant misuse patterns (route-shell override,
manual ``session.commit()`` in a view method, hand-rolled CRUD on a bare
``View``) and emit :class:`RestlyMisuseWarning` for each. Off by default.

The lint runs before parent endpoints are copied into the subclass, so a clean
view must not warn even though its ``__dict__`` gains the five shells during
registration -- pinned by ``test_clean_restview_does_not_warn``.
"""

import warnings
from typing import Any

import fastapi
from sqlalchemy import orm

import fastapi_restly as fr
from fastapi_restly.db._globals import RestlyContext
from fastapi_restly.exc import RestlyMisuseWarning


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
    assert "route shell 'get_many_endpoint'" in messages[0]
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
    copies the parent route shells into the subclass during registration, this
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
