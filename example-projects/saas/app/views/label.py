"""Label and TaskLabel views."""

import sqlalchemy as sa
from fastapi import HTTPException
from pydantic import BaseModel

import fastapi_restly as fr
from fastapi_restly.schemas import IDRef
from fastapi_restly.views import async_make_new_object, async_save_object

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
        """Remove all task-label associations before deleting the label.

        Overriding ``delete_object`` is the right place for cascading cleanup
        that should happen on every delete — here we detach any tasks that
        reference this label before removing the label row itself.
        """
        await self.session.execute(
            sa.delete(TaskLabel).where(TaskLabel.label_id == obj.id)
        )
        await super().delete_object(obj)


class TaskLabelView(TenantBase):
    """CRUD for task-label associations.

    Demonstrates ``make_new_object`` to stamp the ``added_by_id`` field from
    the injected auth context rather than requiring the client to provide it.
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

        The brenntag pattern from ``permissions.py:188-201`` — create a
        sibling row if needed, then create another row that references it.

        Two design choices visible in this implementation, both deliberate:

        1) **Build the Label and flush before constructing the TaskLabel.**
           This is required because the next step uses
           ``async_make_new_object`` with a ``TaskLabelSchema`` that
           carries ``label_id: IDRef[Label]`` — and the schema
           resolver runs ``select(Label).where(id=...)`` to verify the
           reference. A not-yet-flushed Label has no PK yet, so we
           wouldn't have anything to put in ``label_id``. The flush is
           the cost of going through the resolver path.

        2) **Direct ORM construction after flush.** We use
           ``async_make_new_object`` rather than the model constructor
           directly so the framework's writable-field filtering + readonly
           handling apply. The ``TaskLabelSchema`` has ``label_id`` typed
           as ``IDRef[Label]`` — the resolver replaces that with the
           Label instance, which the framework then converts back to
           ``label_id = label.id`` via the resolver path.
        """
        # Tenant scope is enforced via TaskView.handle_retrieve-style checks here:
        # we don't go through TaskView, so we re-validate the task fits
        # the current org to avoid a cross-tenant attach.
        task = await self.session.get(Task, request.task_id)
        if task is None:
            raise HTTPException(404, "Task not found")
        org_id = self._current_org_id()
        if org_id is None:
            raise HTTPException(400, "Cannot create labels without an org context")

        # 1) Build sibling #1 (Label) and flush — needed because the
        #    next step's IDRef resolver requires an existing PK.
        label = Label(
            name=request.label_name, color=request.color, organization_id=org_id
        )
        self.session.add(label)
        await self.session.flush()  # <-- the resolver path's hard requirement

        # 2) Build sibling #2 (TaskLabel) referencing #1 via IDRef.
        #    The fields on TaskLabelSchema are typed IDRef[T] so the
        #    wire format is scalar (``"task_id": 5``) but the framework
        #    resolver still verifies the row exists. ``model_construct``
        #    skips Pydantic validation, so we pass IDRef instances
        #    directly rather than scalars — that keeps the resolver path
        #    happy. (Passing a scalar would leave a plain int that the
        #    resolver doesn't recognize, falling through to assignment
        #    against the ORM column, which happens to work because the
        #    column is also an int. But that path skips the existence
        #    check, which is the whole point of the type.)
        link_schema = TaskLabelSchema.model_construct(
            task_id=IDRef[Task](id=request.task_id), label_id=IDRef[Label](id=label.id)
        )
        task_label = await async_make_new_object(self.session, TaskLabel, link_schema)
        # added_by_id stamping isn't auto-applied here because
        # async_make_new_object is the *free function*, not the bound
        # ``self.make_new_object`` method that this view overrides for
        # the stamp. Worth flagging for the helper-design discussion.
        if task_label.added_by_id is None:
            task_label.added_by_id = self._current_user_id()

        task_label = await async_save_object(self.session, task_label)
        # IDRef serializes as a bare scalar both ways. FastAPI's
        # response_model coercion handles the ORM int directly.
        return task_label
