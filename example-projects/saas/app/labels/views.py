"""Label and TaskLabel views."""

from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel

import fastapi_restly as fr
from fastapi_restly.objects import async_make_new_object, async_save_object

from ..tasks.models import Task, TaskClauses
from ..views import AuthenticatedView
from .models import Label, TaskLabel
from .schemas import LabelSchema, TaskLabelResponse, TaskLabelSchema


class CreateAndAttachLabelRequest(BaseModel):
    """Request body for the sibling-creation custom endpoint."""

    task_id: fr.MustExist[int, Task]
    label_name: str
    color: str = "#808080"


class LabelView(AuthenticatedView[Label]):
    """CRUD for labels (organization-scoped).

    The session listener in ``app.models`` filters reads to the organization.
    ``Label`` stamps ``organization_id`` through ``TenantOwned``. Deleting a
    label removes its task links in the database (``ON DELETE CASCADE`` on
    ``TaskLabel``), so the view adds nothing.
    """

    prefix = "/labels"
    model = Label
    schema = LabelSchema


class TaskLabelView(AuthenticatedView[TaskLabel]):
    """CRUD for task-label associations.

    The tenant criterion in ``labels.models`` requires both the task and label
    to belong to the caller's organization, unless the caller is an admin.
    ``added_by_id`` is stamped by its column's insert default from
    ``Current.user_id`` on every write path; the schema marks it read-only.
    """

    prefix = "/task-labels"
    model = TaskLabel
    schema = TaskLabelSchema

    async def authorize(
        self, action: str, obj: TaskLabel | None = None, data: Any = None
    ) -> None:
        """A member changes labels only on a task that is assigned to them.

        This covers the task that a request names and the task that an
        existing link points at. Reads stay open inside the organization: a
        member lists the labels of a task they cannot open. A task that does
        not exist is left to the reference check, which answers 404.
        """
        task_ids = set()
        if obj is not None and action in (fr.Action.UPDATE, fr.Action.DELETE):
            task_ids.add(obj.task_id)
        bodies = (TaskLabelSchema, CreateAndAttachLabelRequest)
        if isinstance(data, bodies) and "task_id" in data.model_fields_set:
            task_ids.add(data.task_id)
        if not task_ids:
            return
        # each named task, and whether the caller may change it
        stmt = sa.select(Task.id, TaskClauses.assigned_to_current_user()).where(
            Task.id.in_(task_ids)
        )
        if not all(allowed for _, allowed in await self.session.execute(stmt)):
            raise fr.exc.Forbidden("Changing a task's labels requires the task")

    @fr.post("/create-and-attach", response_model=TaskLabelResponse, status_code=201)
    async def create_and_attach(
        self, request: CreateAndAttachLabelRequest
    ) -> TaskLabel:
        """Sibling-creation: build a Label *and* a TaskLabel in one request.

        The Label lands in the organization the request acts in:
        ``organization_id`` is the model's stamp, not an argument here.
        Before the block runs, ``authorize`` refuses a task that the member
        is not assigned to (403), and the reference check refuses a foreign
        or deleted task (404). The Label is flushed first so its id can pass
        the TaskLabel schema's reference checks.
        """
        # Commit the Label + TaskLabel pair atomically.
        async with self.write_action("create", data=request) as w:
            # 1) Build Label and flush so its PK exists for the existence check.
            label = Label(name=request.label_name, color=request.color)
            self.session.add(label)
            await self.session.flush()  # <-- existence check needs the PK to exist

            # 2) Build TaskLabel with plain ids so references are checked.
            link_schema = TaskLabelSchema.model_construct(
                task_id=request.task_id, label_id=label.id
            )
            task_label = await async_make_new_object(
                self.session, TaskLabel, link_schema
            )
            task_label = await async_save_object(self.session, task_label)
            w.obj = task_label
        return task_label
