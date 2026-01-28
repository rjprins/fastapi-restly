"""Task view."""

from pydantic import BaseModel
from sqlalchemy import select

import fastapi_restly as fr

from ..models import Task, TaskStatus, TaskPriority, TaskType
from ..schemas import TaskSchema


# Simulated current user from auth context - in real apps, get from JWT/session
CURRENT_USER_ID: int | None = None  # Set to enable row-level permissions


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


class TaskView(fr.AsyncAlchemyView):
    """CRUD endpoints for tasks.

    Demonstrates row-level permissions: users can only see tasks
    assigned to them OR tasks in projects they have access to.
    Set CURRENT_USER_ID to enable row-level filtering.
    """

    prefix = "/tasks"
    model = Task
    schema = TaskSchema

    def _get_current_user_id(self) -> int | None:
        """Get current user ID from auth context (placeholder for real auth)."""
        return CURRENT_USER_ID

    async def process_index(self, query_params, query=None):
        """Override to filter tasks by row-level permissions."""
        if query is None:
            query = select(self.model)

        # Row-level permissions: filter to tasks assigned to user
        current_user = self._get_current_user_id()
        if current_user is not None:
            # User can see tasks assigned to them
            query = query.where(Task.assignee_id == current_user)

        return await super().process_index(query_params, query)

    async def process_get(self, id: int):
        """Override to verify row-level access for single resource."""
        from fastapi import HTTPException

        task = await self.session.get(Task, id)
        if task is None:
            raise HTTPException(404)

        # Row-level permissions: verify user can access this task
        current_user = self._get_current_user_id()
        if current_user is not None:
            # Only allow if assigned to user
            if task.assignee_id != current_user:
                # Return 404 to avoid leaking existence
                raise HTTPException(404, detail="Task not found")

        return task

    async def _validate_cross_resource(self, data: dict) -> None:
        """Validate cross-resource constraints (assignee must be in same org as project)."""
        from fastapi import HTTPException

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
                        detail="Assignee must be from the same organization as the project"
                    )

    def _validate_conditional_fields(self, data: dict) -> None:
        """Validate conditional required fields based on task_type."""
        from fastapi import HTTPException

        task_type = data.get("task_type", TaskType.TASK)
        severity = data.get("severity")

        # Bugs require severity
        if task_type == TaskType.BUG and severity is None:
            raise HTTPException(
                status_code=422,
                detail="severity is required for bug tasks"
            )

    async def process_post(self, schema_obj):
        """Override to check if project is archived before creating task."""
        from fastapi import HTTPException

        from ..models import Project, ProjectStatus

        data = schema_obj.model_dump()
        project_id = data.get("project_id")

        if project_id:
            project = await self.session.get(Project, project_id)
            if project and project.status == ProjectStatus.ARCHIVED:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot create tasks in an archived project"
                )

        # Validate conditional required fields
        self._validate_conditional_fields(data)

        # Validate cross-resource constraints
        await self._validate_cross_resource(data)

        return await super().process_post(schema_obj)

    async def process_patch(self, id: int, schema_obj):
        """Override to implement optimistic locking via version field."""
        from fastapi import HTTPException

        task = await self.session.get(Task, id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        # Check version for optimistic locking
        data = schema_obj.model_dump(exclude_unset=True)
        if "version" in data:
            if data["version"] != task.version:
                raise HTTPException(
                    status_code=409,
                    detail=f"Conflict: expected version {data['version']}, but current version is {task.version}"
                )

        # Cross-resource validation for assignee change
        # Use existing task's project_id if not being changed
        validation_data = {
            "project_id": data.get("project_id", task.project_id),
            "assignee_id": data.get("assignee_id"),
        }
        if validation_data["assignee_id"] is not None:
            await self._validate_cross_resource(validation_data)

        # Update fields
        for key, value in data.items():
            if key not in ("id", "created_at", "updated_at", "version"):
                setattr(task, key, value)

        # Increment version
        task.version += 1

        return task

    @fr.post("/bulk", response_model=BulkResult)
    async def bulk_create(self, request: BulkCreateRequest) -> BulkResult:
        """Create multiple tasks at once."""
        success = 0
        failed = 0
        errors: list[str] = []

        for item in request.items:
            try:
                task = Task(**item.model_dump())
                self.session.add(task)
                await self.session.flush()
                success += 1
            except Exception as e:
                failed += 1
                errors.append(f"Failed to create task '{item.title}': {e!s}")

        return BulkResult(success=success, failed=failed, errors=errors)

    @fr.post("/bulk-delete", response_model=BulkResult)
    async def bulk_delete(self, request: BulkDeleteRequest) -> BulkResult:
        """Delete multiple tasks by IDs."""
        success = 0
        failed = 0
        errors: list[str] = []

        for task_id in request.ids:
            try:
                task = await self.session.get(Task, task_id)
                if task:
                    await self.session.delete(task)
                    success += 1
                else:
                    failed += 1
                    errors.append(f"Task {task_id} not found")
            except Exception as e:
                failed += 1
                errors.append(f"Failed to delete task {task_id}: {e!s}")

        return BulkResult(success=success, failed=failed, errors=errors)

    @fr.post("/{id}/start", response_model=TaskSchema)
    async def start_task(self, id: int) -> Task:
        """Move task from TODO to IN_PROGRESS."""
        from fastapi import HTTPException

        task = await self.session.get(Task, id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task.status != TaskStatus.TODO:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot start task with status '{task.status.value}'. Must be 'todo'."
            )

        task.status = TaskStatus.IN_PROGRESS
        task.version += 1
        return task

    @fr.post("/{id}/complete", response_model=TaskSchema)
    async def complete_task(self, id: int) -> Task:
        """Move task from IN_PROGRESS to DONE."""
        from fastapi import HTTPException

        task = await self.session.get(Task, id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task.status != TaskStatus.IN_PROGRESS:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot complete task with status '{task.status.value}'. Must be 'in_progress'."
            )

        task.status = TaskStatus.DONE
        task.version += 1
        return task

    @fr.post("/{id}/reopen", response_model=TaskSchema)
    async def reopen_task(self, id: int) -> Task:
        """Reopen a completed task back to IN_PROGRESS."""
        from fastapi import HTTPException

        task = await self.session.get(Task, id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task.status != TaskStatus.DONE:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reopen task with status '{task.status.value}'. Must be 'done'."
            )

        task.status = TaskStatus.IN_PROGRESS
        task.version += 1
        return task
