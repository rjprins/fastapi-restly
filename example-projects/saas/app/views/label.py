"""Label and TaskLabel views."""

import sqlalchemy as sa
from fastapi import HTTPException
from pydantic import BaseModel

import fastapi_restly as fr
from fastapi_restly.objects import async_make_new_object, async_save_object
from fastapi_restly.schemas import IDRef

from ..models import Label, Task, TaskLabel
from ..schemas import LabelSchema, TaskLabelSchema
from ._base import TenantBase
from ._mixins import TenantScopedMixin


class CreateAndAttachLabelRequest(BaseModel):
    """Request body for the sibling-creation custom endpoint."""

    task_id: int
    label_name: str
    color: str = "#808080"


class LabelView(TenantScopedMixin, TenantBase):
    """CRUD for labels (organization-scoped).

    ``TenantScopedMixin`` handles read filtering + write stamping of
    ``organization_id``; this class only adds the cascade-on-delete.
    """

    prefix = "/labels"
    model = Label
    schema = LabelSchema

    async def delete_object(self, obj):
        """Remove task-label associations before deleting the label."""
        await self.session.execute(
            sa.delete(TaskLabel).where(TaskLabel.label_id == obj.id)
        )
        await super().delete_object(obj)


class TaskLabelView(TenantBase):
    """CRUD for task-label associations.

    ``make_new_object`` stamps ``added_by_id`` from auth context.
    """

    prefix = "/task-labels"
    model = TaskLabel
    schema = TaskLabelSchema

    async def make_new_object(self, schema_obj):
        """Stamp added_by_id from auth context if the client did not provide it."""
        obj = await super().make_new_object(schema_obj)
        if obj.added_by_id is None:
            obj.added_by_id = self._current_user_id()
        return obj

    @fr.post("/create-and-attach", response_model=TaskLabelSchema, status_code=201)
    async def create_and_attach(
        self, request: CreateAndAttachLabelRequest
    ) -> TaskLabelSchema:
        """Sibling-creation: build a Label *and* a TaskLabel in one request.

        The Label is flushed first so its id can be used in the TaskLabel
        ``IDRef`` schema. Both rows commit through one ``write_action`` block.
        """
        # Tenant scope is enforced via TaskView.get_one-style checks here:
        # we don't go through TaskView, so we re-validate the task fits
        # the current org to avoid a cross-tenant attach.
        task = await self.session.get(Task, request.task_id)
        if task is None:
            raise HTTPException(404, "Task not found")
        org_id = self._current_org_id()
        if org_id is None:
            raise HTTPException(400, "Cannot create labels without an org context")

        # Commit the Label + TaskLabel pair atomically.
        async with self.write_action("create", data=request) as w:
            # 1) Build Label and flush so the resolver can see its PK.
            label = Label(
                name=request.label_name, color=request.color, organization_id=org_id
            )
            self.session.add(label)
            await self.session.flush()  # <-- the resolver path's hard requirement

            # 2) Build TaskLabel with IDRef instances so references are checked.
            link_schema = TaskLabelSchema.model_construct(
                task_id=IDRef[Task](id=request.task_id),
                label_id=IDRef[Label](id=label.id),
            )
            task_label = await async_make_new_object(
                self.session, TaskLabel, link_schema
            )
            # The free helper bypasses this view's make_new_object override.
            if task_label.added_by_id is None:
                task_label.added_by_id = self._current_user_id()
            w.obj = await async_save_object(self.session, task_label)
        return w.obj
