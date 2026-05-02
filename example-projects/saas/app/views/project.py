"""Project view."""

from datetime import datetime, timezone

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

import fastapi_restly as fr
from fastapi_restly.query import apply_query_modifiers, use_query_modifier_version

from ..models import Project, ProjectStatus, Task, TaskPriority, TaskStatus, TaskType
from ..schemas import ProjectSchema, TaskSchema
from ._base import TenantBase


class CloneRequest(BaseModel):
    """Request body for cloning a project."""

    new_name: str | None = None
    include_tasks: bool = True


class ProjectStats(BaseModel):
    """Statistics for a project."""

    total_tasks: int
    todo_count: int
    in_progress_count: int
    done_count: int
    completion_percent: float


class ProjectView(TenantBase):
    """CRUD endpoints for projects.

    Demonstrates:
    - Inheriting TenantBase (class-level auth dependency + audit save_object)
    - include_pagination_metadata with a custom count_index
    - update_object override to make organization_id immutable after creation
    - Soft delete via a custom @fr.delete route (exclude_routes removes the
      generated one, giving full control over the HTTP contract)
    """

    prefix = "/projects"
    model = Project
    schema = ProjectSchema
    include_pagination_metadata = True
    exclude_routes = ["delete"]  # replaced by soft_delete below

    def _base_query(self, include_deleted: bool = False) -> sa.Select:
        """Build the base query with tenant and soft-delete filters applied.

        Shared by on_list and count_index so both see the same row set.
        """
        query = sa.select(Project)
        org_id = self._current_org_id()
        if org_id is not None:
            query = query.where(Project.organization_id == org_id)
        if not include_deleted:
            query = query.where(Project.deleted_at.is_(None))
        return query

    async def on_list(self, query_params, query=None):
        include_deleted = (
            self.request.query_params.get("include_deleted", "false").lower() == "true"
        )
        return await super().on_list(query_params, query=self._base_query(include_deleted))

    async def count_index(self, query_params):
        """Count with the same tenant and soft-delete filters as on_list.

        Must be overridden whenever on_list restricts the base query, so that
        pagination totals stay accurate when include_pagination_metadata = True.
        """
        include_deleted = (
            self.request.query_params.get("include_deleted", "false").lower() == "true"
        )
        base = self._base_query(include_deleted)
        query_params_obj = self._to_query_params(query_params)
        with use_query_modifier_version(self.get_query_modifier_version()):
            filtered = apply_query_modifiers(
                query_params_obj, base, self.model, self.schema
            )
        filtered = filtered.order_by(None).limit(None).offset(None)
        count_q = select(func.count()).select_from(filtered.subquery())
        return int(await self.session.scalar(count_q) or 0)

    async def on_get(self, id: int):
        from fastapi import HTTPException

        project = await self.session.get(Project, id)
        if project is None:
            raise HTTPException(404)
        org_id = self._current_org_id()
        if org_id is not None and project.organization_id != org_id:
            raise HTTPException(404, detail="Project not found")
        return project

    async def update_object(self, obj, schema_obj):
        """Reject any attempt to move a project to a different organization.

        Overriding update_object is the right place to guard fields that should
        be immutable after creation — here we raise 400 if the PATCH body
        contains an organization_id that differs from the current one.
        """
        import fastapi
        new_org = getattr(schema_obj, "organization_id", None)
        if new_org is not None and new_org != obj.organization_id:
            raise fastapi.HTTPException(400, "Cannot move a project to a different organization")
        return await super().update_object(obj, schema_obj)

    @fr.delete("/{id}", status_code=200, response_model=ProjectSchema)
    async def soft_delete(self, id: int) -> Project:
        """Soft delete: sets deleted_at instead of removing the row.

        Uses a custom @fr.delete route (with exclude_routes removing the
        generated one) to return 200 + the updated object rather than 204.
        """
        from fastapi import HTTPException

        project = await self.session.get(Project, id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        project.deleted_at = datetime.now(timezone.utc)
        return project

    @fr.post("/{id}/restore", response_model=ProjectSchema)
    async def restore(self, id: int) -> Project:
        """Restore a soft-deleted project."""
        from fastapi import HTTPException

        project = await self.session.get(Project, id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if project.deleted_at is None:
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
        from fastapi import HTTPException

        query = (
            select(Project)
            .where(Project.id == id)
            .options(selectinload(Project.tasks))
        )
        result = await self.session.execute(query)
        original = result.scalar_one_or_none()
        if not original:
            raise HTTPException(status_code=404, detail="Project not found")

        new_name = request.new_name or f"{original.name} (Copy)"
        new_project = Project(
            name=new_name,
            description=original.description,
            status=ProjectStatus.ACTIVE,
            organization_id=original.organization_id,
        )
        self.session.add(new_project)
        await self.session.flush()

        if request.include_tasks:
            for task in original.tasks:
                new_task = Task(
                    title=task.title,
                    description=task.description,
                    status=task.status,
                    priority=task.priority,
                    task_type=task.task_type,
                    project_id=new_project.id,
                    severity=task.severity,
                    steps_to_reproduce=task.steps_to_reproduce,
                    story_points=task.story_points,
                    acceptance_criteria=task.acceptance_criteria,
                )
                self.session.add(new_task)

        return new_project

    @fr.get("/{id}/stats", response_model=ProjectStats)
    async def get_project_stats(self, id: int) -> ProjectStats:
        """Get task statistics for a project."""
        from fastapi import HTTPException

        project = await self.session.get(Project, id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        todo = await self.session.scalar(
            select(func.count()).where(Task.project_id == id, Task.status == TaskStatus.TODO)
        ) or 0
        in_progress = await self.session.scalar(
            select(func.count()).where(Task.project_id == id, Task.status == TaskStatus.IN_PROGRESS)
        ) or 0
        done = await self.session.scalar(
            select(func.count()).where(Task.project_id == id, Task.status == TaskStatus.DONE)
        ) or 0
        total = todo + in_progress + done
        completion = round(done / total * 100, 1) if total > 0 else 0.0

        return ProjectStats(
            total_tasks=total,
            todo_count=todo,
            in_progress_count=in_progress,
            done_count=done,
            completion_percent=completion,
        )

    @fr.get("/{id}/tasks", response_model=list[TaskSchema])
    async def list_project_tasks(self, id: int) -> list[Task]:
        """List all tasks for a specific project."""
        from fastapi import HTTPException

        project = await self.session.get(Project, id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        result = await self.session.scalars(select(Task).where(Task.project_id == id))
        return list(result.all())

    @fr.post("/{id}/tasks", response_model=TaskSchema, status_code=201)
    async def create_project_task(self, id: int, request: "TaskCreateRequest") -> Task:
        """Create a task within a project (auto-sets project_id)."""
        from fastapi import HTTPException

        project = await self.session.get(Project, id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
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
    """Request body for creating a task via the nested /projects/{id}/tasks route."""

    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    task_type: TaskType = TaskType.TASK
    assignee_id: int | None = None
