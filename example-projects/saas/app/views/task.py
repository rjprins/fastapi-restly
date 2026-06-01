"""Task view."""

import fastapi
from fastapi import HTTPException
from pydantic import BaseModel

import fastapi_restly as fr

from ..models import Task, TaskPriority, TaskStatus, TaskType
from ..schemas import TaskSchema
from ._base import TenantBase
from ._mixins import AuditStampedMixin, SoftDeleteMixin


class TaskCreateSchema(BaseModel):
    """Schema for creating a task (no id/timestamps)."""

    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    task_type: TaskType = TaskType.TASK
    project_id: int
    assignee_id: int | None = None
    parent_id: int | None = None
    severity: int | None = None
    steps_to_reproduce: str | None = None
    story_points: int | None = None
    acceptance_criteria: str | None = None
    version: int = 1


class BulkCreateRequest(BaseModel):
    """Request body for bulk task creation."""

    items: list[TaskCreateSchema]


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


class TaskView(SoftDeleteMixin, AuditStampedMixin, TenantBase):
    """CRUD endpoints for tasks.

    Mixin-composed: soft delete + audit stamps. Tenant scope is *not*
    applied here — task scoping is by ``assignee_id`` (a row-level
    permission, not a tenant filter), implemented in ``build_query`` below.
    Because retrieve also routes through ``build_query``, the same predicate
    that filters listing also returns 404 from ``GET /tasks/{id}`` for tasks
    not assigned to the current user — and cascades through ``handle_update``
    and ``handle_delete`` (both load the row through ``get_one`` first).
    Demonstrates that views with non-tenant-aligned access models still
    benefit from the soft-delete + audit mixins, and that mixin composition
    is a la carte.
    """

    prefix = "/tasks"
    model = Task
    schema = TaskSchema

    async def delete_object(self, obj):
        """Decrement the parent project's story-point rollup before delete."""
        from ..models import Project

        if obj.story_points:
            project = await self.session.get(Project, obj.project_id)
            if project is not None:
                project.total_story_points -= obj.story_points
        await super().delete_object(obj)

    def build_query(self):
        """Filter tasks to those assigned to the current user.

        Admin requests bypass this filter — they see every task regardless
        of assignee. Applied at the ``build_query`` override point so the same scope
        feeds listing, count, AND retrieve — the row-level permission is
        enforced at the SQL level on every read path, and cascades through
        ``handle_update`` / ``handle_delete`` (which load via ``get_one``).
        Composes with ``SoftDeleteMixin.build_query`` via ``super()``.
        """
        q = super().build_query()
        if self._is_admin():
            return q
        current_user = self._current_user_id()
        if current_user is not None:
            q = q.where(Task.assignee_id == current_user)
        return q

    async def _validate_cross_resource(self, data: dict) -> None:
        """Validate cross-resource constraints (assignee must be in same org as project)."""
        from ..models import Project, User

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

    def _validate_conditional_fields(self, data: dict) -> None:
        """Validate conditional required fields based on task_type."""
        task_type = data.get("task_type", TaskType.TASK)
        severity = data.get("severity")

        # Bugs require severity
        if task_type == TaskType.BUG and severity is None:
            raise HTTPException(
                status_code=422, detail="severity is required for bug tasks"
            )

    async def create(self, schema_obj):
        """Validate, build, save, and bump the parent's story-point rollup.

        The rollup depends on request data, so it lives in the business verb and
        commits with the task.
        """
        from ..models import Project, ProjectStatus

        data = schema_obj.model_dump()
        project_id = data.get("project_id")

        if project_id:
            project = await self.session.get(Project, project_id)
            if project and project.status == ProjectStatus.ARCHIVED:
                raise HTTPException(
                    status_code=400, detail="Cannot create tasks in an archived project"
                )

        # Validate conditional required fields
        self._validate_conditional_fields(data)

        # Validate cross-resource constraints
        await self._validate_cross_resource(data)

        task = await self.make_new_object(schema_obj)
        if task.story_points and project:
            project.total_story_points += task.story_points
        return await self.save_object(task)

    async def update(self, obj, schema_obj):
        """Optimistic locking + reroll the parent's story-point rollup.

        Capture old values, apply the update, then propagate the story-point
        delta to the related project in the same transaction.
        """
        from ..models import Project

        task = obj

        # Check version for optimistic locking before applying the update.
        client_version = getattr(schema_obj, "version", None)
        if (
            client_version is not None
            and "version" in schema_obj.model_fields_set
            and client_version != task.version
        ):
            raise HTTPException(
                status_code=409,
                detail=f"Conflict: expected version {client_version}, but current version is {task.version}",
            )

        # Cross-resource validation for assignee change.
        sent = schema_obj.model_dump(exclude_unset=True)
        if sent.get("assignee_id") is not None:
            await self._validate_cross_resource(
                {
                    "project_id": sent.get("project_id", task.project_id),
                    "assignee_id": sent["assignee_id"],
                }
            )

        old_points = task.story_points or 0
        old_project_id = task.project_id

        # Apply writable fields, then bump version server-side.
        task = await self.update_object(task, schema_obj)
        task.version = (
            client_version if client_version is not None else task.version
        ) + 1

        # Propagate story-point delta to the (possibly new) parent project.
        new_points = task.story_points or 0
        if old_points or new_points:
            if old_project_id == task.project_id:
                project = await self.session.get(Project, task.project_id)
                if project is not None:
                    project.total_story_points += new_points - old_points
            else:
                old_project = await self.session.get(Project, old_project_id)
                new_project = await self.session.get(Project, task.project_id)
                if old_project is not None:
                    old_project.total_story_points -= old_points
                if new_project is not None:
                    new_project.total_story_points += new_points

        return await self.save_object(task)

    async def _transition_task(
        self, id: int, source_status: TaskStatus, target_status: TaskStatus, action: str
    ) -> Task:
        """Run a status transition through a named write action."""
        if target_status not in VALID_TRANSITIONS.get(source_status, []):
            raise RuntimeError(
                f"Invalid task transition definition: {source_status.value} -> {target_status.value}"
            )

        # Load with scope + 404 + read-auth.
        task = await self.handle_get_one(id)
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
            await self.save_object(task)
        return task

    @fr.post("/import-csv", response_model=BulkResult)
    async def import_csv(
        self,
        project_id: int = fastapi.Form(...),  # noqa: B008
        file: fastapi.UploadFile = fastapi.File(...),  # noqa: B008
    ) -> BulkResult:
        """Parse a CSV of tasks and bulk-create them.

        Each row uses the business ``create`` verb, with per-row savepoints and
        one final commit for successful rows.
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
        for row_no, row in enumerate(reader, start=2):  # row 1 is the header
            try:
                schema_obj = TaskCreateSchema(
                    title=(row.get("title") or "").strip(),
                    description=row.get("description") or "",
                    project_id=project_id,
                )
                if not schema_obj.title:
                    raise ValueError("title is required")
                # The business ``create`` verb is auth-free by design, so a
                # custom route that calls it directly must gate the action
                # itself (mirrors ``handle_create``).
                await self.authorize("create", data=schema_obj)
                # Per-row SAVEPOINT: a row that fails at flush rolls back just
                # its savepoint, keeping the outer transaction usable so the
                # remaining good rows still persist.
                async with self.session.begin_nested():
                    await self.create(schema_obj)
                success += 1
            except Exception as exc:  # noqa: BLE001 — surface per-row error
                failed += 1
                errors.append(f"row {row_no}: {exc}")
        # The route owns the commit now: persist every successfully-built row.
        await self.session.commit()
        return BulkResult(success=success, failed=failed, errors=errors)

    @fr.post("/bulk", response_model=BulkResult)
    async def bulk_create(self, request: BulkCreateRequest) -> BulkResult:
        """Create multiple tasks at once.

        Each item uses the business ``create`` verb. Per-row savepoints allow
        partial success; one final commit persists successful rows.
        """
        success = 0
        failed = 0
        errors: list[str] = []

        for item in request.items:
            try:
                # ``create`` is auth-free; gate each row like ``handle_create``.
                await self.authorize("create", data=item)
                async with self.session.begin_nested():
                    await self.create(item)
                success += 1
            except Exception as e:  # noqa: BLE001 — surface per-row error
                failed += 1
                errors.append(f"Failed to create task '{item.title}': {e!s}")

        # The route owns the commit now: persist every successfully-built row.
        await self.session.commit()
        return BulkResult(success=success, failed=failed, errors=errors)

    @fr.post("/bulk-delete", response_model=BulkResult)
    async def bulk_delete(self, request: BulkDeleteRequest) -> BulkResult:
        """Delete multiple tasks by IDs.

        Each id loads through ``handle_get_one`` for row visibility, then runs in
        a savepoint. One final commit persists successful deletes.
        """
        success = 0
        failed = 0
        errors: list[str] = []

        for task_id in request.ids:
            try:
                async with self.session.begin_nested():
                    task = await self.handle_get_one(task_id)
                    # handle_get_one gates "get_one"; the delete action needs
                    # its own gate (the business delete is auth-free).
                    await self.authorize("delete", obj=task)
                    await self.delete_object(task)
                success += 1
            except HTTPException as exc:
                failed += 1
                if exc.status_code == 404:
                    errors.append(f"Task {task_id} not found")
                else:
                    errors.append(f"Failed to delete task {task_id}: {exc.detail}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                errors.append(f"Failed to delete task {task_id}: {e!s}")

        # The route owns the commit now: persist all successful deletes.
        await self.session.commit()
        return BulkResult(success=success, failed=failed, errors=errors)

    @fr.post("/{id}/start", response_model=TaskSchema)
    async def start_task(self, id: int) -> Task:
        """Move task from TODO to IN_PROGRESS."""
        return await self._transition_task(
            id, TaskStatus.TODO, TaskStatus.IN_PROGRESS, "start"
        )

    @fr.post("/{id}/complete", response_model=TaskSchema)
    async def complete_task(self, id: int) -> Task:
        """Move task from IN_PROGRESS to DONE."""
        return await self._transition_task(
            id, TaskStatus.IN_PROGRESS, TaskStatus.DONE, "complete"
        )

    @fr.post("/{id}/reopen", response_model=TaskSchema)
    async def reopen_task(self, id: int) -> Task:
        """Reopen a completed task back to IN_PROGRESS."""
        return await self._transition_task(
            id, TaskStatus.DONE, TaskStatus.IN_PROGRESS, "reopen"
        )
