"""Label and TaskLabel views."""

import sqlalchemy as sa
from fastapi import HTTPException
from pydantic import BaseModel

import fastapi_restly as fr
from fastapi_restly.objects import async_make_new_object, async_save_object

from ..context import Current
from ..views import TenantBase, TenantScopedMixin
from .models import Label, TaskLabel
from .schemas import LabelSchema, TaskLabelSchema


class CreateAndAttachLabelRequest(BaseModel):
    """Request body for the sibling-creation custom endpoint."""

    task_id: int
    label_name: str
    color: str = "#808080"


class LabelView(TenantScopedMixin, TenantBase):
    """CRUD for labels (organization-scoped).

    ``LabelClauses.default_scope`` filters reads to the organization;
    ``TenantScopedMixin`` stamps ``organization_id`` on writes. This class
    only adds the cascade-on-delete.
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
            obj.added_by_id = Current.user_id()
        return obj

    @fr.post("/create-and-attach", response_model=TaskLabelSchema, status_code=201)
    async def create_and_attach(
        self, request: CreateAndAttachLabelRequest
    ) -> TaskLabelSchema:
        """Sibling-creation: build a Label *and* a TaskLabel in one request.

        The Label is flushed first so its id can pass the TaskLabel schema's
        ``MustExist`` checks. Those checks run inside each target model's
        ``default_scope``, so a ``task_id`` from another organization reads
        as "does not exist" (404): the tenant EXISTS in
        ``TaskClauses.default_scope`` replaces the org-join this route used
        to spell out by hand, and the aborted request rolls the flushed
        Label back with it.
        """
        org_id = Current.org_id()
        if org_id is None:
            raise HTTPException(400, "Cannot create labels without an org context")

        # Commit the Label + TaskLabel pair atomically.
        async with self.write_action("create", data=request) as w:
            # 1) Build Label and flush so its PK exists for the existence check.
            label = Label(
                name=request.label_name, color=request.color, organization_id=org_id
            )
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
            # The free helper bypasses this view's make_new_object override.
            if task_label.added_by_id is None:
                task_label.added_by_id = Current.user_id()
            w.obj = await async_save_object(self.session, task_label)
        return w.obj
