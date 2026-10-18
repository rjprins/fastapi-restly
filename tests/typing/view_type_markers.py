from typing import Any

from fastapi import FastAPI
from sqlalchemy.orm import DeclarativeBase, Mapped

import fastapi_restly as fr
from fastapi_restly.views import (
    ViewCreate,
    ViewID,
    ViewListResponse,
    ViewResponse,
    ViewUpdate,
)

app = FastAPI()


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class Task(fr.IDMixin, Base):
    title: Mapped[str]


class TaskSchema(fr.IDSchema):
    title: str


class RenameView(fr.AsyncRestView[Any]):
    """A base view: each view that subclasses it gets its own types."""

    @fr.post("/{id}/rename", status_code=200)
    async def rename(self, id: ViewID, schema_obj: ViewUpdate) -> ViewResponse:
        obj = await self.handle_update(id, schema_obj)
        return self.to_response(obj)

    @fr.get("/recent")
    async def recent(self, list_params: Any) -> ViewListResponse:
        result = await self.handle_get_many(list_params)
        return self.to_response(result, fr.ResponseShape.LIST)


@fr.include_view(app)
class TaskView(RenameView):
    prefix = "/tasks"
    model = Task
    schema = TaskSchema


@fr.include_view(app)
class SyncTaskView(fr.RestView[Task, TaskSchema]):
    """The markers in a concrete view, also inside another type."""

    prefix = "/sync-tasks"
    model = Task
    schema = TaskSchema

    @fr.post("/import", response_model=list[ViewResponse])
    def import_tasks(self, schema_obj: ViewCreate) -> Any:
        return [self.to_response(self.handle_create(schema_obj))]

    @fr.get("/maybe/{id}")
    def maybe(self, id: ViewID) -> ViewResponse | None:
        return self.to_response(self.handle_get_one(id))
