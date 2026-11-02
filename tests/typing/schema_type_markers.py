from typing import Annotated, Any

from fastapi import FastAPI
from sqlalchemy.orm import DeclarativeBase, Mapped

import fastapi_restly as fr
from fastapi_restly.views import (
    CreateSchema,
    IDType,
    ListResponseSchema,
    ResponseSchema,
    SchemaType,
    UpdateSchema,
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
    async def rename(self, id: IDType, data: UpdateSchema) -> ResponseSchema:
        obj = await self.handle_update(id, data)
        return obj

    @fr.get("/recent")
    async def recent(self, list_params: Any) -> ListResponseSchema:
        result = await self.handle_get_many(list_params)
        return result


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

    @fr.post("/import", response_model=list[ResponseSchema])
    def import_tasks(self, data: CreateSchema) -> Any:
        return [self.to_single_response(self.handle_create(data))]

    @fr.get("/maybe/{id}")
    def maybe(self, id: IDType) -> ResponseSchema | None:
        return self.to_single_response(self.handle_get_one(id))


# SchemaType: a function of the view, on a setting and in a route.
Wrapped = Annotated[Any, SchemaType(lambda view: view.response_schema)]


class FunctionView(fr.AsyncRestView[Any]):
    create_schema = SchemaType(lambda view: view.schema)

    @SchemaType
    def update_schema(view: Any) -> Any:
        return view.schema

    @fr.get("/wrapped")
    async def wrapped(self) -> Wrapped: ...
