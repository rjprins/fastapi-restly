from collections.abc import Sequence
from typing import Any

import sqlalchemy
from fastapi import FastAPI, Response
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

app = FastAPI()


class Widget(fr.IDBase):
    name: Mapped[str]


class WidgetSchema(fr.IDSchema[Widget]):
    name: str


class WidgetInputSchema(fr.BaseSchema):
    name: str


@fr.include_view(app)
class WidgetView(
    fr.RestView[Widget, WidgetSchema, WidgetInputSchema, WidgetInputSchema, int]
):
    prefix = "/widgets"
    model = Widget
    schema = WidgetSchema
    creation_schema = WidgetInputSchema
    update_schema = WidgetInputSchema

    @fr.get("/ping")
    def ping(self) -> dict[str, bool]:
        return {"pong": True}

    @fr.route("/health", methods=["GET"])
    def health(self) -> dict[str, str]:
        return {"status": "ok"}

    def on_list(
        self,
        query_params: Any,
        query: sqlalchemy.Select[Any] | None = None,
    ) -> Sequence[Widget]:
        return super().on_list(query_params, query=query)

    def on_get(self, id: int) -> Widget:
        return super().on_get(id)

    def on_create(self, schema_obj: WidgetInputSchema) -> Widget:
        return super().on_create(schema_obj)

    def on_update(self, id: int, schema_obj: WidgetInputSchema) -> Widget:
        return super().on_update(id, schema_obj)

    def on_delete(self, id: int) -> Response:
        return super().on_delete(id)
