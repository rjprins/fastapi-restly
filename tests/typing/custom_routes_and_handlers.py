from collections.abc import Sequence
from typing import Any

import sqlalchemy
from fastapi import FastAPI, Response
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

app = FastAPI()


class Widget(fr.IDBase):
    name: Mapped[str]


class WidgetRead(fr.IDSchema[Widget]):
    name: str


class WidgetInput(fr.BaseSchema):
    name: str


@fr.include_view(app)
class WidgetView(
    fr.RestView[Widget, WidgetRead, WidgetInput, WidgetInput, int]
):
    prefix = "/widgets"
    model = Widget
    schema = WidgetRead
    creation_schema = WidgetInput
    update_schema = WidgetInput

    @fr.get("/ping")
    def ping(self) -> dict[str, bool]:
        return {"pong": True}

    @fr.route("/health", methods=["GET"])
    def health(self) -> dict[str, str]:
        return {"status": "ok"}

    def handle_list(
        self, query_params: Any, query: sqlalchemy.Select[Any] | None = None
    ) -> Sequence[Widget]:
        return super().handle_list(query_params, query=query)

    def handle_get(self, id: int) -> Widget:
        return super().handle_get(id)

    def handle_create(self, schema_obj: WidgetInput) -> Widget:
        return super().handle_create(schema_obj)

    def handle_update(self, id: int, schema_obj: WidgetInput) -> Widget:
        return super().handle_update(id, schema_obj)

    def handle_delete(self, id: int) -> Response:
        return super().handle_delete(id)
