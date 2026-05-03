from typing import Any

from fastapi import FastAPI
from sqlalchemy.orm import Mapped

import fastapi_restly as fr


class Widget(fr.IDBase):
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
