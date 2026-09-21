"""Label and TaskLabel views."""

from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel

import fastapi_restly as fr
from fastapi_restly.objects import async_make_new_object, async_save_object

from ..tasks.models import Task, TaskClauses
from ..views import AuthenticatedView
from .models import Label, TaskLabel
from .schemas import LabelSchema, TaskLabelSchema


class CreateAndAttachLabelRequest(BaseModel):
    """Request body for the sibling-creation custom endpoint."""

    task_id: int
    label_name: str
    color: str = "#808080"


class LabelView(AuthenticatedView):
    """CRUD for labels (organization-scoped).

    The session listener in ``app.models`` filters reads to the organization.
    ``Label`` stamps ``organization_id`` through ``TenantOwned``.
    This class only adds the cascade-on-delete.
    """

    prefix = "/labels"
    model = Label
    schema = LabelSchema

    async def delete(self, obj):
        """Remove task-label associations before deleting the label.

        A bulk DELETE carries no tenant criteria, so this also removes a
        link an admin made from another organization's task to this label.
        The label's foreign key requires that.
        """
        await self.session.execute(
            sa.delete(TaskLabel).where(TaskLabel.label_id == obj.id)
        )
        await super().delete(obj)


class TaskLabelView(AuthenticatedView):
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
        """A member changes or removes a link only on a task assigned to them.

        The schema's reference scope covers the task a request names. This
        covers the task an existing link already points at. Reads stay open
        inside the organization: a member lists the labels of a task they
        cannot open.
        """
        if action not in (fr.Action.UPDATE, fr.Action.DELETE) or obj is None:
            return
        task_id = await self.session.scalar(
            fr.apply_clauses(
                sa.select(Task.id).where(Task.id == obj.task_id),
                TaskClauses.assigned_to_current_user,
            )
        )
        if task_id is None:
            raise fr.exc.Forbidden("Changing a task's labels requires the task")

    @fr.post("/create-and-attach", response_model=TaskLabelSchema, status_code=201)
    async def create_and_attach(
        self, request: CreateAndAttachLabelRequest
    ) -> TaskLabelSchema:
        """Sibling-creation: build a Label *and* a TaskLabel in one request.

        The Label lands in the organization the request acts in:
        ``organization_id`` is the model's stamp, not an argument here.
        The Label is flushed first so its id can pass the TaskLabel schema's
        reference checks. The tenant criteria restrict those checks to the
        caller's organization, and ``task_id`` is checked in
        ``TaskClauses.visible``. A foreign task, a deleted task, or a task
        the member is not assigned to returns 404, rolling back the flushed
        Label with the request.
        """
        # Commit the Label + TaskLabel pair atomically.
        async with self.write_action("create", data=request) as w:
            # 1) Build Label and flush so its PK exists for the existence check.
            label = Label(name=request.label_name, color=request.color)
            self.session.add(label)
            await self.session.flush()  # <-- existence check needs the PK to exist

            # 2) Build TaskLabel with plain ids so references are checked,
            #    each inside its target's default_scope.
            link_schema = TaskLabelSchema.model_construct(
                task_id=request.task_id, label_id=label.id
            )
            task_label = await async_make_new_object(
                self.session, TaskLabel, link_schema
            )
            w.obj = await async_save_object(self.session, task_label)
        return w.obj
