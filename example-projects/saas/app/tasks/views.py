"""Task view."""

from typing import Any

import fastapi
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import ColumnElement

import fastapi_restly as fr
from fastapi_restly.views import ViewResponse

from ..projects.models import Project
from ..users.models import User
from ..views import TrashView
from .models import Task, TaskClauses, TaskPriority, TaskStatus, TaskType
from .schemas import TaskSchema


class TaskCreate(BaseModel):
    """Schema for creating a task (no id/timestamps).

    It is TaskView's ``schema_create``, so the create route, the bulk route
    and the CSV import take the same body.
    """

    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    task_type: TaskType = TaskType.TASK
    project_id: fr.MustExist[int, Project]
    assignee_id: fr.MustExist[int, User] | None = None
    parent_id: fr.MustExist[int, Task] | None = None
    severity: int | None = None
    steps_to_reproduce: str | None = None
    story_points: int | None = None
    acceptance_criteria: str | None = None
    version: int = 1


class BulkCreateRequest(BaseModel):
    """Request body for bulk task creation."""

    items: list[TaskCreate]


class BulkDeleteRequest(BaseModel):
    """Request body for bulk task deletion."""

    ids: list[int]


class BulkResult(BaseModel):
    """Result of a bulk operation."""

    success: int
    failed: int
    errors: list[str] = []


# Valid state transitions for task workflow
VALID_TRANSITIONS = {
    TaskStatus.TODO: [TaskStatus.IN_PROGRESS],
    TaskStatus.IN_PROGRESS: [TaskStatus.TODO, TaskStatus.DONE],
    TaskStatus.DONE: [TaskStatus.IN_PROGRESS],  # Can reopen
}


class TaskView(TrashView[Task]):
    """CRUD endpoints for tasks.

    Task visibility is a row-level permission (a member sees their own
    assignments, other roles the organization's tasks):
    ``scope = TaskClauses.visible`` replaces the model's ``default_scope``
    on this view's reads, while references to Task keep the default. The
    tenant rule is not repeated here: the ``with_loader_criteria``
    criterion in ``tasks/models.py`` applies to ORM reads of Task, this
    scope and the trash route's alike. Retrieve
    applies the same scope, so the predicate that filters the list also
    returns 404 from ``GET /tasks/{id}`` for a task a member is not
    assigned to, and cascades through ``handle_update`` and
    ``handle_delete`` (both load the row through ``get_one`` first).
    """

    prefix = "/tasks"
    model = Task
    schema = TaskSchema
    schema_create = TaskCreate
    scope = TaskClauses.visible
    trash_scope = TaskClauses.trashed

    class Action(TrashView.Action):
        START = "start"
        COMPLETE = "complete"
        REOPEN = "reopen"

    # No enum gets the generated range keys, because SQLAlchemy's Enum
    # stores the names. This column stores the number, so the view writes
    # the range filter itself.
    @fr.list_filter("priority__gte")
    def filter_priority_gte(self, value: TaskPriority) -> ColumnElement[bool]:
        """Tasks with this priority number or a larger one. 1 is critical and
        4 is low, so ``priority__gte=3`` gives the medium and low tasks."""
        return Task.priority >= value

    async def _validate_cross_resource(self, data: dict[str, Any]) -> None:
        """Validate cross-resource constraints (assignee must be in same org as project)."""
        from ..projects.models import Project
        from ..users.models import User

        project_id = data.get("project_id")
        assignee_id = data.get("assignee_id")

        if project_id and assignee_id:
            project = await self.session.get(Project, project_id)
            assignee = await self.session.get(User, assignee_id)

            if project and assignee:
                if assignee.organization_id != project.organization_id:
                    raise HTTPException(
                        status_code=422,
                        detail="Assignee must be from the same organization as the project",
                    )

    def _validate_conditional_fields(self, data: dict[str, Any]) -> None:
        """Validate conditional required fields based on task_type."""
        task_type = data.get("task_type", TaskType.TASK)
        severity = data.get("severity")

        # Bugs require severity
        if task_type == TaskType.BUG and severity is None:
            raise HTTPException(
                status_code=422, detail="severity is required for bug tasks"
            )

    async def create(self, data: BaseModel) -> Task:
        """Validate against the project and the assignee, then build the task.

        The handler has already checked that ``project_id`` and
        ``assignee_id`` name visible rows. The project's story-point total
        follows by itself: the session event in ``app.tasks.models`` keeps it
        on every write path.
        """
        from ..projects.models import Project, ProjectStatus

        values = data.model_dump()
        project_id = values.get("project_id")

        project = None
        if project_id:
            project = await self.session.get(Project, project_id)
            if project and project.status == ProjectStatus.ARCHIVED:
                raise HTTPException(
                    status_code=400, detail="Cannot create tasks in an archived project"
                )

        # Validate conditional required fields
        self._validate_conditional_fields(values)

        # Validate cross-resource constraints
        await self._validate_cross_resource(values)

        return await super().create(data)

    async def update(self, obj: Task, data: BaseModel) -> Task:
        """Optimistic locking, then apply the update.

        A change of the story points or the project updates the projects'
        totals through the session event in ``app.tasks.models``.
        """
        task = obj

        # Check version for optimistic locking before applying the update.
        client_version = getattr(data, "version", None)
        if (
            client_version is not None
            and "version" in data.model_fields_set
            and client_version != task.version
        ):
            raise fr.exc.Conflict(
                f"Conflict: expected version {client_version}, but current version is {task.version}"
            )

        # Cross-resource validation for assignee change.
        sent = data.model_dump(exclude_unset=True)
        if sent.get("assignee_id") is not None:
            await self._validate_cross_resource(
                {
                    "project_id": sent.get("project_id", task.project_id),
                    "assignee_id": sent["assignee_id"],
                }
            )

        # Apply writable fields, then bump version server-side.
        task = await super().update(task, data)
        task.version = (
            client_version if client_version is not None else task.version
        ) + 1
        return task

    async def _transition_task(
        self, id: int, source_status: TaskStatus, target_status: TaskStatus, action: str
    ) -> Task:
        """Run a status transition through a named write action."""
        if target_status not in VALID_TRANSITIONS.get(source_status, []):
            raise RuntimeError(
                f"Invalid task transition definition: {source_status.value} -> {target_status.value}"
            )

        # Scoped load + 404; ``write_action`` below gates the action.
        task = await self.get_one(id)
        if task.status != source_status:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot {action} task with status '{task.status.value}'. "
                    f"Must be '{source_status.value}'."
                ),
            )

        async with self.write_action(action, obj=task):
            task.status = target_status
            task.version += 1
        return task

    @fr.post("/import-csv", response_model=BulkResult)
    async def import_csv(
        self,
        project_id: int = fastapi.Form(...),  # noqa: B008
        file: fastapi.UploadFile = fastapi.File(...),  # noqa: B008
    ) -> BulkResult:
        """Parse a CSV of tasks and bulk-create them.

        Each row uses the create handler inside a savepoint. The outer shared
        block commits successful rows once and then runs their after-hooks.
        """
        import csv
        import io

        if not file.filename:
            raise fastapi.HTTPException(422, "filename is required")
        raw = await file.read()
        try:
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
        except UnicodeDecodeError as exc:
            raise fastapi.HTTPException(422, str(exc)) from exc

        success = 0
        failed = 0
        errors: list[str] = []
        async with self.shared_write_action_commit():
            for row_no, row in enumerate(reader, start=2):  # row 1 is the header
                try:
                    data = TaskCreate(
                        title=(row.get("title") or "").strip(),
                        description=row.get("description") or "",
                        project_id=project_id,
                    )
                    if not data.title:
                        raise ValueError("title is required")
                    # The savepoint contains the handler's mutation and
                    # before-hook writes. A rejected row rolls both back.
                    async with self.session.begin_nested():
                        await self.handle_create(data)
                    success += 1
                except Exception as exc:  # noqa: BLE001, surface per-row error
                    failed += 1
                    errors.append(f"row {row_no}: {exc}")
        return BulkResult(success=success, failed=failed, errors=errors)

    @fr.post("/bulk", response_model=BulkResult)
    async def bulk_create(self, request: BulkCreateRequest) -> BulkResult:
        """Create multiple tasks at once.

        Each item uses the create handler inside a savepoint. The outer shared
        block commits successful rows once and then runs their after-hooks.
        """
        success = 0
        failed = 0
        errors: list[str] = []

        async with self.shared_write_action_commit():
            for item in request.items:
                try:
                    async with self.session.begin_nested():
                        await self.handle_create(item)
                    success += 1
                except Exception as e:  # noqa: BLE001, surface per-row error
                    failed += 1
                    errors.append(f"Failed to create task '{item.title}': {e!s}")

        return BulkResult(success=success, failed=failed, errors=errors)

    @fr.post("/bulk-delete", response_model=BulkResult)
    async def bulk_delete(self, request: BulkDeleteRequest) -> BulkResult:
        """Delete multiple tasks by IDs.

        Each id uses the delete handler inside a savepoint. The outer shared
        block commits successful deletes once and then runs their after-hooks.
        """
        success = 0
        failed = 0
        errors: list[str] = []

        async with self.shared_write_action_commit():
            for task_id in request.ids:
                try:
                    async with self.session.begin_nested():
                        await self.handle_delete(task_id)
                    success += 1
                except fr.exc.NotFound:
                    failed += 1
                    errors.append(f"Task {task_id} not found")
                except HTTPException as exc:
                    failed += 1
                    errors.append(f"Failed to delete task {task_id}: {exc.detail}")
                except Exception as e:  # noqa: BLE001, surface per-row error
                    failed += 1
                    errors.append(f"Failed to delete task {task_id}: {e!s}")

        return BulkResult(success=success, failed=failed, errors=errors)

    @fr.post("/{id}/start")
    async def start_task(self, id: int) -> ViewResponse:
        """Move task from TODO to IN_PROGRESS."""
        return await self._transition_task(
            id, TaskStatus.TODO, TaskStatus.IN_PROGRESS, self.Action.START
        )

    @fr.post("/{id}/complete")
    async def complete_task(self, id: int) -> ViewResponse:
        """Move task from IN_PROGRESS to DONE."""
        return await self._transition_task(
            id, TaskStatus.IN_PROGRESS, TaskStatus.DONE, self.Action.COMPLETE
        )

    @fr.post("/{id}/reopen")
    async def reopen_task(self, id: int) -> ViewResponse:
        """Reopen a completed task back to IN_PROGRESS."""
        return await self._transition_task(
            id, TaskStatus.DONE, TaskStatus.IN_PROGRESS, self.Action.REOPEN
        )
