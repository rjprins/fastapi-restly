"""Project view."""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

import fastapi_restly as fr

from ..models import Project, Task, TaskStatus, TaskPriority, TaskType, ProjectStatus
from ..schemas import ProjectSchema, TaskSchema


# Simulated current org from auth context - in real apps, get from JWT/session
CURRENT_ORG_ID: int | None = None  # Set to enable tenant isolation


def filter_fields(data: dict[str, Any], fields: list[str] | None) -> dict[str, Any]:
    """Filter a dict to only include specified fields.

    Used for sparse fieldsets (?fields=id,name).
    """
    if fields is None:
        return data
    return {k: v for k, v in data.items() if k in fields}


class CloneRequest(BaseModel):
    """Request body for cloning a project."""

    new_name: str | None = None  # If not provided, append " (Copy)"
    include_tasks: bool = True


class ProjectStats(BaseModel):
    """Statistics for a project."""

    total_tasks: int
    todo_count: int
    in_progress_count: int
    done_count: int
    completion_percent: float
    overdue_count: int = 0  # Would need due_date field to implement


class ProjectView(fr.AsyncAlchemyView):
    """CRUD endpoints for projects.

    Demonstrates tenant isolation by filtering to current org.
    Set CURRENT_ORG_ID to enable org scoping.
    """

    prefix = "/projects"
    model = Project
    schema = ProjectSchema
    exclude_routes = ["delete"]  # Use soft delete instead

    def _get_current_org_id(self) -> int | None:
        """Get current org ID from auth context (placeholder for real auth)."""
        return CURRENT_ORG_ID

    async def process_index(self, query_params, query=None):
        """Override to filter by org and soft-delete status."""
        # Check if include_deleted is requested via raw query params
        include_deleted = self.request.query_params.get("include_deleted", "false").lower() == "true"

        if query is None:
            query = select(self.model)

        # Tenant isolation: filter by current org if set
        current_org = self._get_current_org_id()
        if current_org is not None:
            query = query.where(Project.organization_id == current_org)

        if not include_deleted:
            query = query.where(Project.deleted_at.is_(None))

        return await super().process_index(query_params, query)

    async def process_get(self, id: int):
        """Override to verify org access for single resource."""
        from fastapi import HTTPException

        project = await self.session.get(Project, id)
        if project is None:
            raise HTTPException(404)

        # Tenant isolation: verify org access
        current_org = self._get_current_org_id()
        if current_org is not None and project.organization_id != current_org:
            # Return 404 (not 403) to avoid leaking existence of other org's data
            raise HTTPException(404, detail="Project not found")

        return project

    @fr.get("/sparse", response_model=list[dict])
    async def list_sparse(self) -> list[dict]:
        """List projects with sparse fieldsets support.

        Query params:
        - fields: Comma-separated list of fields to include (e.g., ?fields=id,name)

        Example: GET /projects/sparse?fields=id,name,status
        """
        # Get requested fields from query params
        fields_param = self.request.query_params.get("fields")
        requested_fields: list[str] | None = None
        if fields_param:
            requested_fields = [f.strip() for f in fields_param.split(",")]

        # Build query with tenant isolation and soft delete filtering
        query = select(Project)
        current_org = self._get_current_org_id()
        if current_org is not None:
            query = query.where(Project.organization_id == current_org)

        include_deleted = self.request.query_params.get("include_deleted", "false").lower() == "true"
        if not include_deleted:
            query = query.where(Project.deleted_at.is_(None))

        result = await self.session.scalars(query)
        projects = list(result.all())

        # Convert to dicts and filter fields
        output = []
        for project in projects:
            # Use from_attributes=True for SQLAlchemy model conversion
            project_dict = ProjectSchema.model_validate(
                project, from_attributes=True
            ).model_dump()
            output.append(filter_fields(project_dict, requested_fields))

        return output

    @fr.delete("/{id}", status_code=200, response_model=ProjectSchema)
    async def soft_delete(self, id: int) -> Project:
        """Soft delete a project (sets deleted_at instead of actually deleting)."""
        project = await self.session.get(Project, id)
        if not project:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Project not found")

        project.deleted_at = datetime.now(timezone.utc)
        return project

    @fr.post("/{id}/restore", response_model=ProjectSchema)
    async def restore(self, id: int) -> Project:
        """Restore a soft-deleted project."""
        project = await self.session.get(Project, id)
        if not project:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Project not found")

        if project.deleted_at is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Project is not deleted")

        project.deleted_at = None
        return project

    @fr.post("/{id}/archive", response_model=ProjectSchema)
    async def archive_project(self, id: int) -> Project:
        """Archive a project (prevents new task creation)."""
        from fastapi import HTTPException

        project = await self.session.get(Project, id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        if project.status == ProjectStatus.ARCHIVED:
            raise HTTPException(status_code=400, detail="Project is already archived")

        project.status = ProjectStatus.ARCHIVED
        return project

    @fr.post("/{id}/clone", response_model=ProjectSchema)
    async def clone_project(self, id: int, request: CloneRequest) -> Project:
        """Clone a project with all its tasks."""
        from sqlalchemy import select

        # Load original project with tasks
        query = (
            select(Project)
            .where(Project.id == id)
            .options(selectinload(Project.tasks))
        )
        result = await self.session.execute(query)
        original = result.scalar_one_or_none()

        if not original:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Project not found")

        # Create new project
        new_name = request.new_name or f"{original.name} (Copy)"
        new_project = Project(
            name=new_name,
            description=original.description,
            status=ProjectStatus.ACTIVE,  # Always start as active
            organization_id=original.organization_id,
        )
        self.session.add(new_project)
        await self.session.flush()  # Get new project ID

        # Clone tasks if requested
        if request.include_tasks:
            for task in original.tasks:
                new_task = Task(
                    title=task.title,
                    description=task.description,
                    status=task.status,
                    priority=task.priority,
                    task_type=task.task_type,
                    project_id=new_project.id,
                    # Don't copy: assignee_id, parent_id (subtask hierarchy)
                    severity=task.severity,
                    steps_to_reproduce=task.steps_to_reproduce,
                    story_points=task.story_points,
                    acceptance_criteria=task.acceptance_criteria,
                )
                self.session.add(new_task)

        return new_project

    @fr.get("/{id}/stats", response_model=ProjectStats)
    async def get_project_stats(self, id: int) -> ProjectStats:
        """Get statistics for a project."""
        from fastapi import HTTPException

        project = await self.session.get(Project, id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        # Count tasks by status
        todo_query = select(func.count()).where(
            Task.project_id == id, Task.status == TaskStatus.TODO
        )
        in_progress_query = select(func.count()).where(
            Task.project_id == id, Task.status == TaskStatus.IN_PROGRESS
        )
        done_query = select(func.count()).where(
            Task.project_id == id, Task.status == TaskStatus.DONE
        )

        todo = await self.session.scalar(todo_query) or 0
        in_progress = await self.session.scalar(in_progress_query) or 0
        done = await self.session.scalar(done_query) or 0
        total = todo + in_progress + done

        completion = (done / total * 100) if total > 0 else 0.0

        return ProjectStats(
            total_tasks=total,
            todo_count=todo,
            in_progress_count=in_progress,
            done_count=done,
            completion_percent=round(completion, 1),
        )

    # Nested routes for tasks within a project
    @fr.get("/{id}/tasks", response_model=list[TaskSchema])
    async def list_project_tasks(self, id: int) -> list[Task]:
        """List all tasks for a specific project."""
        from fastapi import HTTPException

        # Verify project exists
        project = await self.session.get(Project, id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        query = select(Task).where(Task.project_id == id)
        result = await self.session.scalars(query)
        return list(result.all())

    @fr.post("/{id}/tasks", response_model=TaskSchema, status_code=201)
    async def create_project_task(
        self,
        id: int,
        request: "TaskCreateRequest",
    ) -> Task:
        """Create a task within a project (auto-sets project_id)."""
        from fastapi import HTTPException

        # Verify project exists
        project = await self.session.get(Project, id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        # Check if project is archived
        if project.status == ProjectStatus.ARCHIVED:
            raise HTTPException(status_code=400, detail="Cannot create tasks in an archived project")

        task = Task(
            title=request.title,
            description=request.description,
            status=request.status,
            priority=request.priority,
            task_type=request.task_type,
            project_id=id,
            assignee_id=request.assignee_id,
        )
        self.session.add(task)
        await self.session.flush()
        return task


class TaskCreateRequest(BaseModel):
    """Request body for creating a task via nested route."""

    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    task_type: TaskType = TaskType.TASK
    assignee_id: int | None = None
