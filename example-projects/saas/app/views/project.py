"""Project view."""

import re

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
    - ``TenantBase`` — auth dep, audit ``save_object`` override point, ``_emit``
      outbox helper. The ``build_query`` override point consumed by the mixins
      above lives on ``AsyncRestView`` itself.

    Each mixin's ``build_query`` calls ``super().build_query()``, so the
    tenant + soft-delete WHERE clauses compose without either mixin
    knowing the other exists. The same chain feeds ``get_many``,
    ``count``, AND ``get_one`` — pagination totals stay aligned with list
    results, and a row hidden from listing returns 404 from ``GET /{id}``
    as well. ``handle_update`` and ``handle_delete`` inherit this
    visibility check because they load the row through ``get_one`` first.

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

    async def get_many(self, query_params) -> fr.ListingResult[Project]:
        # The mixins enforce tenant scope + soft-delete filtering already
        # (via build_query). Here we only do project-specific response
        # decoration on each row in the page.
        result = await super().get_many(query_params)
        decorated = [
            await self._decorate_project_response(project)
            for project in result.objects
        ]
        return fr.ListingResult(
            objects=decorated,
            total_count=result.total_count,
            query_params=result.query_params,
        )

    async def get_one(self, id: int):
        # The mixins enforce tenant scope + soft-delete filtering already.
        # ``get_one`` is the auth-free load+scope+404 override point; we layer only
        # project-specific response decoration on top. ``handle_get_one``
        # (and therefore every read path) routes through here.
        project = await super().get_one(id)
        return await self._decorate_project_response(project)

    def _can_edit(self, project: Project) -> bool:
        """Whether the current user may edit this project.

        Stand-in policy: only members of the same org. In production this
        would consult the user's role from request.state.
        """
        org_id = self._current_org_id()
        return org_id is None or project.organization_id == org_id

    async def create(self, schema_obj):
        """Slug derivation + outbox emit on top of the mixin chain.

        Overrides the *bare* business ``create`` verb: auth-free and
        commit-free. ``self.make_new_object`` runs through
        ``AuditStampedMixin`` (stamps created_by/updated_by) and
        ``TenantScopedMixin`` (stamps organization_id) — neither lives in this
        method. We only do the project-specific bits: slug uniqueness probe and
        the outbox event. The outbox row is added to the session here and the
        ``handle_create`` commit bracket persists it atomically with the
        project write.
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

    async def update(self, obj: Project, schema_obj):
        """Slug regen if name changed + status-transition outbox event.

        Overrides the *bare* business ``update`` verb, which receives the
        already-loaded ``obj``: ``handle_update`` loads it through ``get_one``
        (tenant-scope + soft-delete + 404), runs ``authorize``, then calls this
        method, and finally commits via the bracket. Audit-stamping
        (``updated_by_id``) is provided by ``AuditStampedMixin.update_object``.
        This method only contains the project-specific slug + transition-event
        logic. The status-changed outbox row is added to the session and the
        ``handle_update`` commit bracket persists it atomically.
        """
        old_name, old_status = obj.name, obj.status
        project = await self.update_object(obj, schema_obj)
        if project.name != old_name and not getattr(schema_obj, "slug", None):
            project.slug = await self._unique_slug(
                _slugify(project.name), project.organization_id, exclude_id=project.id
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

        Brackets the soft-delete with ``write_action`` so it runs the same
        sequence as ``handle_delete`` (authorize / snapshot / before_commit /
        commit / after_commit). ``obj=project`` means the hooks see
        ``new=project`` (the soft-deleted row) rather than ``None``.
        """
        project = await self.handle_get_one(id)

        async with self.write_action("delete", obj=project):
            await self.delete_object(project)
        return await self._decorate_project_response(project)

    @fr.post("/{id}/restore", response_model=ProjectSchema)
    async def restore(self, id: int) -> Project:
        """Restore a soft-deleted project.

        Genuinely-custom action: it deliberately bypasses the mixin's
        ``deleted_at IS NULL`` filter (we *want* to find a deleted row), so it
        cannot reuse ``handle_get_one``. Tenant scope is re-checked by hand,
        then the clear runs in a ``write_action("restore", ...)`` block for the
        full bracket.
        """
        project = await self.session.get(Project, id)
        if project is None:
            raise HTTPException(404)
        org_id = self._current_org_id()
        if org_id is not None and project.organization_id != org_id:
            raise HTTPException(404, detail="Project not found")
        if project.deleted_at is None:
            raise HTTPException(status_code=400, detail="Project is not deleted")

        async with self.write_action("restore", obj=project):
            project.deleted_at = None
            await self.save_object(project)
        return await self._decorate_project_response(project)

    @fr.post("/{id}/archive", response_model=ProjectSchema)
    async def archive_project(self, id: int) -> Project:
        """Archive a project (prevents new task creation).

        Update-shaped custom action: load via ``handle_get_one`` (scope + 404 +
        read-auth), then bracket the ``status = ARCHIVED`` flip with
        ``write_action("archive", ...)`` so authorize / snapshot / before_commit
        / commit / after_commit all fire.
        """
        project = await self.handle_get_one(id)
        if project.status == ProjectStatus.ARCHIVED:
            raise HTTPException(status_code=400, detail="Project is already archived")

        async with self.write_action("archive", obj=project):
            project.status = ProjectStatus.ARCHIVED
            await self.save_object(project)
        return await self._decorate_project_response(project)

    @fr.post("/{id}/clone", response_model=ProjectSchema)
    async def clone_project(self, id: int, request: CloneRequest) -> Project:
        """Clone a project with all its tasks.

        Calls ``handle_get_one`` first to enforce tenant scope + 404 +
        read-auth, then re-queries with ``selectinload`` to eager-load the
        tasks. Cleaner as a single ``handle_get_one`` if/when it grows a
        ``loader_options`` argument; for now, two queries is the honest cost.
        """
        # Tenant + 404 + read-auth check via the canonical read handler.
        await self.handle_get_one(id)

        query = (
            select(Project).where(Project.id == id).options(selectinload(Project.tasks))
        )
        result = await self.session.execute(query)
        original = result.scalar_one()

        # Build the cloned project via handle_create so slug/audit/outbox-emit
        # plus the authorize + commit bracket all happen exactly as for a normal
        # POST. We synthesize a ProjectSchema as the input — anything unset
        # there falls back to schema defaults. ``handle_create`` commits the
        # project row itself.
        new_schema = ProjectSchema.model_construct(
            name=request.new_name or f"{original.name} (Copy)",
            description=original.description,
            status=ProjectStatus.ACTIVE,
            organization_id=original.organization_id,
        )
        new_project = await self.handle_create(new_schema)

        if request.include_tasks:
            from fastapi_restly.objects import async_save_object

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
            # The sibling-task inserts are a SECOND write on top of the
            # handle_create commit; ``async_save_object`` only flushes, so the
            # route owns this commit. Without it the cloned tasks (and the
            # bumped story-point rollup) would be silently rolled back.
            await async_save_object(self.session, new_project)
            await self.session.commit()

        return await self._decorate_project_response(new_project)

    @fr.get("/{id}/stats", response_model=ProjectStats)
    async def get_project_stats(self, id: int) -> ProjectStats:
        """Get task statistics for a project."""
        # handle_get_one enforces tenant scope + 404 + read-auth — get the
        # access check for free.
        await self.handle_get_one(id)

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
        Tenant scoping is implicit — ``self.handle_get_one(id)`` already
        verified the project is visible to the caller, and tasks are
        project-bound.
        """
        await self.handle_get_one(id)
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

        Sibling/different-model create: the task insert + the project's
        story-point rollup run inside a ``write_action("create", ...)`` block so
        they commit atomically through the one bracket.
        """
        from fastapi_restly.objects import async_save_object

        project = await self.handle_get_one(id)
        if project.status == ProjectStatus.ARCHIVED:
            raise HTTPException(
                status_code=400, detail="Cannot create tasks in an archived project"
            )

        async with self.write_action("create", data=request) as w:
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
            w.obj = await async_save_object(self.session, task)
        return w.obj


class TaskCreateRequest(BaseModel):
    """Request body for creating a task via the nested /projects/{id}/tasks route."""

    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    task_type: TaskType = TaskType.TASK
    assignee_id: int | None = None
