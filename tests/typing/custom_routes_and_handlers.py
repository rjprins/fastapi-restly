from typing import Any

from fastapi import FastAPI
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
    schema_create = WidgetInput
    schema_update = WidgetInput

    @fr.get("/ping")
    def ping(self) -> dict[str, bool]:
        return {"pong": True}

    @fr.route("/health", methods=["GET"])
    def health(self) -> dict[str, str]:
        return {"status": "ok"}

    # Domain operations (auth-free, commit-free) -- the common override point.
    def get_many(self, query_params: Any) -> fr.ListingResult[Widget]:
        return super().get_many(query_params)

    def get_one(self, id: int) -> Widget:
        return super().get_one(id)

    def create(self, schema_obj: WidgetInput) -> Widget:
        return super().create(schema_obj)

    def update(self, obj: Widget, schema_obj: WidgetInput) -> Widget:
        return super().update(obj, schema_obj)

    def delete(self, obj: Widget) -> None:
        super().delete(obj)

    # Request handlers (authorize + commit bracket) -- full ops returning the
    # domain object, reusable from custom actions.
    def handle_update(self, id: int, schema_obj: WidgetInput) -> Widget:
        return super().handle_update(id, schema_obj)

    def handle_delete(self, id: int) -> None:
        super().handle_delete(id)
