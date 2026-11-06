from typing import Any

from fastapi import APIRouter, FastAPI
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

from ._models import SharedBase


class Widget(fr.IDMixin, SharedBase):
    name: Mapped[str]


class WidgetSchema(fr.IDSchema):
    name: str


def _first_operation_tags(app: FastAPI, path: str, method: str = "get") -> list[str]:
    return app.openapi()["paths"][path][method]["tags"]


def test_default_openapi_tag_is_derived_from_prefix():
    app = FastAPI()

    @fr.include_view(app)
    class WidgetView(fr.AsyncRestView):
        prefix = "/widgets"
        model = Widget
        schema = WidgetSchema

    assert _first_operation_tags(app, "/widgets") == ["Widgets"]


def test_explicit_openapi_tags_are_not_prefixed_with_class_name():
    app = FastAPI()

    @fr.include_view(app)
    class TaggedWidgetView(fr.AsyncRestView):
        prefix = "/tagged-widgets"
        tags = ["Inventory"]
        model = Widget
        schema = WidgetSchema

    assert _first_operation_tags(app, "/tagged-widgets") == ["Inventory"]


def test_default_openapi_tag_falls_back_to_class_name_without_prefix():
    app = FastAPI()

    @fr.include_view(app)
    class PingView(fr.View):
        @fr.get("/ping")
        async def ping(self) -> dict[str, Any]:
            return {"ok": True}

    assert _first_operation_tags(app, "/ping") == ["PingView"]


def test_a_view_on_a_tagged_router_gets_only_the_tags_of_the_router():
    """FastAPI adds the tags of a router to the tags of its routes. A tag from
    the prefix would put each route of the view in a second group, next to the
    plain routes of the router."""
    router = APIRouter(prefix="/shop", tags=["shop"])

    @router.get("/plain")
    def plain() -> dict[str, Any]:
        return {}

    @fr.include_view(router)
    class WidgetView(fr.AsyncRestView):
        prefix = "/widgets"
        model = Widget
        schema = WidgetSchema

    app = FastAPI()
    app.include_router(router)
    assert _first_operation_tags(app, "/shop/plain") == ["shop"]
    assert _first_operation_tags(app, "/shop/widgets") == ["shop"]


def test_the_tags_of_a_view_on_a_tagged_router_come_after_those_of_the_router():
    router = APIRouter(prefix="/shop", tags=["shop"])

    @fr.include_view(router)
    class WidgetView(fr.AsyncRestView):
        prefix = "/widgets"
        tags = ["Inventory"]
        model = Widget
        schema = WidgetSchema

    app = FastAPI()
    app.include_router(router)
    assert _first_operation_tags(app, "/shop/widgets") == ["shop", "Inventory"]


def test_a_view_on_two_routers_gets_the_tag_from_the_prefix_only_without_tags():
    tagged = APIRouter(prefix="/tagged", tags=["shop"])
    plain = APIRouter(prefix="/plain")

    class WidgetView(fr.AsyncRestView):
        prefix = "/widgets"
        model = Widget
        schema = WidgetSchema

    fr.include_view(tagged, WidgetView)
    fr.include_view(plain, WidgetView)
    app = FastAPI()
    app.include_router(tagged)
    app.include_router(plain)
    assert _first_operation_tags(app, "/tagged/widgets") == ["shop"]
    assert _first_operation_tags(app, "/plain/widgets") == ["Widgets"]
