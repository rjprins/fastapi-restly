"""Label and TaskLabel views."""

from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel

import fastapi_restly as fr

from ..tasks.models import Task, TaskClauses
from ..views import AuthenticatedView
from .models import Label, TaskLabel
from .schemas import LabelSchema, TaskLabelResponse, TaskLabelSchema


class CreateAndAttachLabelRequest(BaseModel):
    """Request body for the sibling-creation custom endpoint."""

    task_id: int
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
        if isinstance(data, TaskLabelSchema) and "task_id" in data.model_fields_set:
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

        Two creates, each through the handler of its own view, inside one
        ``shared_write_action_commit()``. So each runs its own steps:
        ``authorize``, the reference checks, the save and the hooks. The
        label's handler saves the label, so the link gets its id. The Label
        lands in the organization the request acts in: ``organization_id`` is
        the model's stamp, not an argument here. The link's ``authorize``
        refuses a task that the member is not assigned to (403), and its
        reference check refuses a foreign or deleted task (404). Then nothing
        is committed, the label included.
        """
        labels = LabelView(session=self.session, request=self.request)
        async with self.shared_write_action_commit():
            label = await labels.handle_create(
                LabelView.schema_create(name=request.label_name, color=request.color)
            )
            link = await self.handle_create(
                self.schema_create(task_id=request.task_id, label_id=label.id)
            )
        return link
