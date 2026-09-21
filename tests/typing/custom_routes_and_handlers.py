from typing import Any

from fastapi import FastAPI
from sqlalchemy import ColumnElement
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
class WidgetView(fr.RestView[Widget, WidgetRead, WidgetInput, WidgetInput, int]):
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
    # The handlers always forward ``scope=``, so an override declares it.
    def get_many(
        self, query_params: Any, *, scope: fr.views.ReadScope = None
    ) -> fr.ListingResult[Widget]:
        return super().get_many(query_params, scope=scope)

    # ``id`` is the primary key or a predicate that replaces it, so an
    # override widens the parameter the same way.
    def get_one(
        self, id: int | ColumnElement[bool], *, scope: fr.views.ReadScope = None
    ) -> Widget:
        return super().get_one(id, scope=scope)

    def create(self, schema_obj: WidgetInput) -> Widget:
        return super().create(schema_obj)

    def update(self, obj: Widget, schema_obj: WidgetInput) -> Widget:
        return super().update(obj, schema_obj)

    def delete(self, obj: Widget) -> None:
        super().delete(obj)

    # Request handlers (authorize + commit bracket) -- final, and typed at
    # the call site: a custom action reuses the full op and its bracket.
    @fr.post("/{id}/rename")
    def rename(self, id: int, schema_obj: WidgetInput) -> WidgetRead:
        widget: Widget = self.handle_update(id, schema_obj)
        return self.to_response_schema(widget)

    @fr.post("/{id}/retire")
    def retire(self, id: int) -> dict[str, int]:
        self.handle_delete(id)
        return {"retired": id}

    # A natural-key route: the predicate form keeps the model's type.
    @fr.get("/by-name/{name}")
    def get_by_name(self, name: str) -> WidgetRead:
        widget: Widget = self.handle_get_one(Widget.name == name)
        return self.to_response_schema(widget)

    # A narrowed listing: ``where=`` takes a raw expression or a clause, and
    # the ``scope``-only ``get_many`` override above stays compatible.
    @fr.get("/named/{name}")
    def list_named(self, name: str, query_params: Any) -> Any:
        by_name = self.handle_get_many(query_params, where=Widget.name == name)
        by_clause: fr.ListingResult[Widget] = self.handle_get_many(
            query_params,
            scope=fr.clauses.UNSCOPED,
            where=fr.where_clause(Widget.name == name),
        )
        return self.to_response(by_name or by_clause, fr.ResponseShape.LISTING)


def create_widgets_together(view: WidgetView, items: list[WidgetInput]) -> list[Widget]:
    with view.shared_write_action_commit():
        widgets = [view.handle_create(item) for item in items]
    return widgets


async def async_create_widgets_together(
    view: fr.AsyncRestView[Widget, WidgetRead, WidgetInput, WidgetInput, int],
    items: list[WidgetInput],
) -> list[Widget]:
    async with view.shared_write_action_commit():
        widgets = [await view.handle_create(item) for item in items]
    return widgets
