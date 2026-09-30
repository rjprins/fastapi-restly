"""Building a view by hand, outside a request, with its dependencies by name."""

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

import fastapi_restly as fr


class Task(fr.IDBase):
    title: Mapped[str]


class TaskRead(fr.IDSchema):
    title: str


class TaskView(fr.AsyncRestView[Task, TaskRead, TaskRead, TaskRead, int]):
    prefix = "/tasks"
    model = Task
    schema = TaskRead


async def create_task(session: AsyncSession, request: Request) -> Task:
    view = TaskView(session=session, request=request)
    return await view.handle_create(TaskRead(id=0, title="a"))
