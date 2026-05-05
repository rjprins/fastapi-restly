"""Project view."""

import re
from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

import fastapi_restly as fr

from ..models import Project, ProjectStatus, Task, TaskPriority, TaskStatus, TaskType
from ..schemas import ProjectSchema, TaskSchema
from ._base import TenantBase
from ._mixins import AuditStampedMixin, SoftDeleteMixin, TenantScopedMixin


def _slugify(text: str) -> str:
    """Lowercase, hyphenated slug — adequate for the example."""
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "project"


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


class ProjectView(SoftDeleteMixin, AuditStampedMixin, TenantScopedMixin, TenantBase):
    """CRUD endpoints for projects.

    Mixin composition (left → right via MRO):
    - ``SoftDeleteMixin`` — hides ``deleted_at`` rows; ``delete_object``
      sets the timestamp instead of removing the row.
    - ``AuditStampedMixin`` — stamps ``created_by_id`` / ``updated_by_id``
      via ``make_new_object`` / ``update_object`` (pre-flush, no trap).
    - ``TenantScopedMixin`` — adds ``organization_id`` filter to reads,
      stamps it on writes from auth context.
    - ``TenantBase`` — auth dep, audit ``save_object`` seam, ``_emit``
      outbox helper. The ``build_query`` seam consumed by the mixins
      above lives on ``AsyncRestView`` itself.

    Each mixin's ``build_query`` calls ``super().build_query()``, so the
    tenant + soft-delete WHERE clauses compose without either mixin
    knowing the other exists. The same chain feeds ``perform_list``,
    ``count_listing``, AND ``perform_get`` — pagination totals stay
    aligned with list results, and a row hidden from listing returns 404
    from ``GET /{id}`` as well. ``perform_update`` and ``perform_delete``
    inherit this visibility check via ``perform_get``.

    The view itself only contains *project-specific* logic: slug
    derivation, the response-only ``can_edit`` decoration, the
    immutability check on update, and the project-level outbox events.
    """

    prefix = "/projects"
    model = Project
    schema = ProjectSchema
    include_pagination_metadata = True
    exclude_routes = [fr.ViewRoute.DELETE]  # replaced by soft_delete below

    async def _decorate_project_response(self, project: Project) -> Project:
        """Populate transient response fields that are not stored on Project."""
        project.can_edit = self._can_edit(project)
        project.task_count = (
            await self.session.scalar(
                select(func.count()).where(
                    Task.project_id == project.id, Task.deleted_at.is_(None)
                )
            )
            or 0
        )
        project.completed_task_count = (
            await self.session.scalar(
                select(func.count()).where(
                    Task.project_id == project.id,
                    Task.status == TaskStatus.DONE,
                    Task.deleted_at.is_(None),
                )
            )
            or 0
        )
        return project

    async def perform_list(
        self, query_params, query: sa.Select | None = None
    ) -> Sequence[Project]:
        projects = await super().perform_list(query_params, query)
        return [await self._decorate_project_response(project) for project in projects]

    async def perform_get(self, id: int):
        # The mixins enforce tenant scope + soft-delete filtering already.
        # Here we only do project-specific response decoration.
        project = await super().perform_get(id)
        return await self._decorate_project_response(project)

    def _can_edit(self, project: Project) -> bool:
        """Whether the current user may edit this project.

        Stand-in policy: only members of the same org. In production this
        would consult the user's role from request.state.
        """
        org_id = self._current_org_id()
        return org_id is None or project.organization_id == org_id

    async def perform_create(self, schema_obj):
        """Slug derivation + outbox emit on top of the mixin chain.

        ``self.make_new_object`` runs through ``AuditStampedMixin`` (stamps
        created_by/updated_by) and ``TenantScopedMixin`` (stamps
        organization_id) — neither lives in this method. We only do the
        project-specific bits: slug uniqueness probe and the outbox event.
        """
        project = await self.make_new_object(schema_obj)
        project.slug = await self._unique_slug(
            project.slug or _slugify(project.name), project.organization_id
        )
        project = await self.save_object(project)
        self._emit(
            "project.created", project, {"name": project.name, "slug": project.slug}
        )
        return await self._decorate_project_response(project)

    async def perform_update(self, id: int, schema_obj):
        """Slug regen if name changed + status-transition outbox event.

        Audit-stamping (``updated_by_id``) is provided by
        ``AuditStampedMixin.update_object``; tenant-scope enforcement comes
        from ``TenantScopedMixin.build_query`` (applied at the SQL level via
        the framework's ``perform_get``). This method only contains the
        project-specific slug + transition-event logic.
        """
        project = await self.perform_get(id)
        old_name, old_status = project.name, project.status
        project = await self.update_object(project, schema_obj)
        if project.name != old_name and not getattr(schema_obj, "slug", None):
            project.slug = await self._unique_slug(
                _slugify(project.name), project.organization_id, exclude_id=id
            )
        project = await self.save_object(project)
        if project.status != old_status:
            self._emit(
                "project.status_changed",
                project,
                {"from": old_status.value, "to": project.status.value},
            )
        return await self._decorate_project_response(project)

    async def _unique_slug(
        self, base: str, org_id: int, exclude_id: int | None = None
    ) -> str:
        """Find a slug that doesn't collide with an existing project in this org."""
        candidate = base
        n = 1
        while True:
            q = sa.select(Project.id).where(
                Project.slug == candidate, Project.organization_id == org_id
            )
            if exclude_id is not None:
                q = q.where(Project.id != exclude_id)
            existing = await self.session.scalar(q)
            if existing is None:
                return candidate
            n += 1
            candidate = f"{base}-{n}"

    async def update_object(self, obj, schema_obj):
        """Reject any attempt to move a project to a different organization.

        Overriding update_object is the right place to guard fields that should
        be immutable after creation — here we raise 400 if the PATCH body
        contains an organization_id that differs from the current one.
        """
        new_org = getattr(schema_obj, "organization_id", None)
        if new_org is not None and new_org != obj.organization_id:
            raise HTTPException(
                400, "Cannot move a project to a different organization"
            )
        return await super().update_object(obj, schema_obj)

    @fr.delete("/{id}", status_code=200, response_model=ProjectSchema)
    async def soft_delete(self, id: int) -> Project:
        """Soft delete: sets deleted_at instead of removing the row.

        The actual ``deleted_at`` flip lives in
        ``SoftDeleteMixin.delete_object``. This route exists only because
        the matrix's "Return deleted record instead of 204" use-case
        requires a 200 + body contract — that's a route-level HTTP
        decision, not a behavior change.
        """
        project = await self.perform_get(id)
        await self.delete_object(project)
        return await self._decorate_project_response(project)

    @fr.post("/{id}/restore", response_model=ProjectSchema)
    async def restore(self, id: int) -> Project:
        """Restore a soft-deleted project.

        Bypasses the mixin's ``deleted_at IS NULL`` filter explicitly —
        we *want* to find a deleted row here. Tenant scope still applies.
        """
        project = await self.session.get(Project, id)
        if project is None:
            raise HTTPException(404)
        org_id = self._current_org_id()
        if org_id is not None and project.organization_id != org_id:
            raise HTTPException(404, detail="Project not found")
        if project.deleted_at is None:
            raise HTTPException(status_code=400, detail="Project is not deleted")
        project.deleted_at = None
        return await self._decorate_project_response(project)

    @fr.post("/{id}/archive", response_model=ProjectSchema)
    async def archive_project(self, id: int) -> Project:
        """Archive a project (prevents new task creation)."""
        project = await self.perform_get(id)
        if project.status == ProjectStatus.ARCHIVED:
            raise HTTPException(status_code=400, detail="Project is already archived")
        project.status = ProjectStatus.ARCHIVED
        return await self._decorate_project_response(project)

    @fr.post("/{id}/clone", response_model=ProjectSchema)
    async def clone_project(self, id: int, request: CloneRequest) -> Project:
        """Clone a project with all its tasks.

        Calls ``perform_get`` first to enforce tenant scope and 404, then
        re-queries with ``selectinload`` to eager-load the tasks. Cleaner
        as a single ``perform_get`` if/when it grows a ``loader_options``
        argument; for now, two queries is the honest cost.
        """
        # Tenant + 404 check via the canonical handler.
        await self.perform_get(id)

        query = (
            select(Project).where(Project.id == id).options(selectinload(Project.tasks))
        )
        result = await self.session.execute(query)
        original = result.scalar_one()

        # Build the cloned project via perform_create so slug/audit/outbox-emit
        # all happen exactly as for a normal POST. We synthesize a
        # ProjectSchema as the input — anything unset there falls back to
        # schema defaults.
        new_schema = ProjectSchema.model_construct(
            name=request.new_name or f"{original.name} (Copy)",
            description=original.description,
            status=ProjectStatus.ACTIVE,
            organization_id=original.organization_id,
        )
        new_project = await self.perform_create(new_schema)

        if request.include_tasks:
            from fastapi_restly.views import async_save_object

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
                if new_task.story_points:
                    new_project.total_story_points += new_task.story_points
            await async_save_object(self.session, new_project)

        return await self._decorate_project_response(new_project)

    @fr.get("/{id}/stats", response_model=ProjectStats)
    async def get_project_stats(self, id: int) -> ProjectStats:
        """Get task statistics for a project."""
        # perform_get enforces tenant scope and 404s — get the access check for free.
        await self.perform_get(id)

        todo = (
            await self.session.scalar(
                select(func.count()).where(
                    Task.project_id == id, Task.status == TaskStatus.TODO
                )
            )
            or 0
        )
        in_progress = (
            await self.session.scalar(
                select(func.count()).where(
                    Task.project_id == id, Task.status == TaskStatus.IN_PROGRESS
                )
            )
            or 0
        )
        done = (
            await self.session.scalar(
                select(func.count()).where(
                    Task.project_id == id, Task.status == TaskStatus.DONE
                )
            )
            or 0
        )
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
        """List tasks for a specific project, honouring task visibility rules.

        Mirrors the predicates ``TaskView`` applies to ``GET /tasks/``:
        soft-deleted tasks are hidden (unless ``?include_deleted=true``),
        and non-admin callers see only tasks assigned to themselves.
        Tenant scoping is implicit — ``self.perform_get(id)`` already verified
        the project is visible to the caller, and tasks are project-bound.
        """
        await self.perform_get(id)
        q = select(Task).where(Task.project_id == id)
        include_deleted = (
            self.request.query_params.get("include_deleted", "false").lower() == "true"
        )
        if not include_deleted:
            q = q.where(Task.deleted_at.is_(None))
        if not self._is_admin():
            user_id = self._current_user_id()
            if user_id is not None:
                q = q.where(Task.assignee_id == user_id)
        result = await self.session.scalars(q)
        return list(result.all())

    @fr.post("/{id}/tasks", response_model=TaskSchema, status_code=201)
    async def create_project_task(self, id: int, request: "TaskCreateRequest") -> Task:
        """Create a task within a project (auto-sets project_id).

        Builds the Task directly because ``self.make_new_object`` is bound
        to ``self.model`` (Project, not Task) — it would build the wrong
        type. ``async_save_object`` is the framework's flush+refresh helper
        and keeps the row's autogenerated columns populated for the
        response.
        """
        from fastapi_restly.views import async_save_object

        project = await self.perform_get(id)
        if project.status == ProjectStatus.ARCHIVED:
            raise HTTPException(
                status_code=400, detail="Cannot create tasks in an archived project"
            )

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
        if task.story_points:
            project.total_story_points += task.story_points
        return await async_save_object(self.session, task)


class TaskCreateRequest(BaseModel):
    """Request body for creating a task via the nested /projects/{id}/tasks route."""

    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    task_type: TaskType = TaskType.TASK
    assignee_id: int | None = None
