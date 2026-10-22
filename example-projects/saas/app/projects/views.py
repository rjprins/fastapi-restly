"""Project view."""

from typing import Any

import sqlalchemy as sa
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

import fastapi_restly as fr
from fastapi_restly.views import ViewResponse

from ..current import Current
from ..tasks.models import Task, TaskClauses, TaskPriority, TaskStatus, TaskType
from ..tasks.schemas import TaskResponse
from ..tasks.views import TaskCreate, TaskView
from ..views import TrashView
from .models import Project, ProjectClauses, ProjectStatus
from .schemas import ProjectSchema


class CloneRequest(BaseModel):
    """Request body for cloning a project."""

    new_name: str | None = None
    include_tasks: bool = True


class ProjectPatchRequest(BaseModel):
    """Request body for the by-slug update: the fields a PATCH may write.

    Only the fields the client sends are applied, so an omitted field
    keeps its stored value.
    """

    name: str | None = None
    description: str | None = None
    status: ProjectStatus | None = None


class ProjectStats(BaseModel):
    """Statistics for a project."""

    total_tasks: int
    todo_count: int
    in_progress_count: int
    done_count: int
    completion_percent: float


class ProjectView(TrashView[Project]):
    """CRUD endpoints for projects.

    Read visibility is the model's ``default_scope`` (``ProjectClauses``):
    live projects under the tenant floor, for the list, the count,
    ``get_one`` and every ``project_id`` reference alike. The trash and
    restore routes of ``TrashView`` read through ``trash_scope``,
    ``is_deleted``.
    The write side is structural too: ``Project`` mixes in
    ``TenantOwned``, ``AuditStamped``, and ``SoftDeletable`` (see
    ``app.models``), so ``organization_id`` and the audit ids are stamped
    from ``Current`` on every write path, and ``TrashView`` turns
    ``delete`` into a ``deleted_at`` flip. ``AuthenticatedView`` adds the auth
    and context-bind deps and the ``_emit`` outbox helper; the tenant
    floor on reads is the session listener in ``app.models``.

    The slug, the story-point total and the task counts are the model's
    business too (see ``app.projects.models`` and ``app.tasks.models``). This
    view keeps project-specific logic: ``can_edit`` in the response, and
    project-level events. The events are outbox rows in
    ``before_action_commit``, which runs after the save, so the id and the
    slug are set there. They follow from ``old``
    and ``new``, so every route that creates a project or changes its status
    emits them.
    """

    prefix = "/projects"
    model = Project
    schema = ProjectSchema
    # No scope declared: reads apply ProjectClauses.default_scope.
    trash_scope = ProjectClauses.is_deleted
    exclude_routes = [fr.ViewRoute.DELETE]  # replaced by soft_delete below

    class Action(TrashView.Action):
        ARCHIVE = "archive"

    def to_single_response(self, obj: Any) -> Any:
        """The response, with ``can_edit`` for the caller.

        Every response of the view passes here: ``GET``, the list items, the
        writes and the custom routes below. The task counts need no code
        here: they load with the project.
        """
        response = super().to_single_response(obj)
        return response.model_copy(update={"can_edit": self._can_edit(obj)})

    def _can_edit(self, project: Project) -> bool:
        """Whether the current user may edit this project.

        Stand-in policy: the project's own organization, and admins. In
        production this would consult ``Current.role`` as well.
        """
        return Current.is_admin() or project.organization_id == Current.org_id()

    async def update(self, obj: Project, data: BaseModel) -> Project:
        """Keep a slug that the client sent.

        Audit-stamping (``updated_by_id``) is the column's ``onupdate``
        (``AuditStamped`` in ``app.models``), and a new name gives a new slug
        through the mapper event in ``app.projects.models``.
        """
        project = await super().update(obj, data)
        if getattr(data, "slug", None):
            # A slug that the client sent is kept, also when it is the
            # current one, which SQLAlchemy does not count as a change.
            flag_modified(project, "slug")
        return project

    async def before_action_commit(
        self, action: str, new: Any, old: Any = None
    ) -> None:
        """Write the project's outbox events, in the transaction of the write.

        The bracket has saved ``new``, so its id and its slug are set. ``old``
        is the project before the action: ``None`` for a new project, from
        ``POST /projects`` or from a clone. So the events follow from ``old``
        and ``new``, whichever route made the change. A project is never
        removed here (``DELETE`` is a soft delete), so ``new`` is set.
        """
        await super().before_action_commit(action, new, old)
        if old is None:
            self._emit("project.created", new, {"name": new.name, "slug": new.slug})
        elif new.status != old["status"]:
            self._emit(
                "project.status_changed",
                new,
                {"from": old["status"].value, "to": new.status.value},
            )

    @fr.delete("/{id}", status_code=200)
    async def soft_delete(self, id: int) -> ViewResponse:
        """Soft delete: sets deleted_at instead of removing the row.

        ``SoftDeleteMixin.delete`` performs the mutation. This route
        replaces the default 204 contract with ``200 + body``.
        """
        project = await self.get_one(id)

        async with self.write_action(self.Action.DELETE, obj=project):
            await self.delete(project)
        return project

    @fr.post("/{id}/archive")
    async def archive_project(self, id: int) -> ViewResponse:
        """Archive a project (prevents new task creation).

        Loads through ``get_one``; ``write_action(self.Action.ARCHIVE)`` gates the
        action and commits the status change. The status changes, so
        ``before_action_commit`` writes a ``project.status_changed`` event.
        """
        project = await self.get_one(id)
        if project.status == ProjectStatus.ARCHIVED:
            raise HTTPException(status_code=400, detail="Project is already archived")

        async with self.write_action(self.Action.ARCHIVE, obj=project):
            project.status = ProjectStatus.ARCHIVED
        return project

    @fr.post("/{id}/clone")
    async def clone_project(self, id: int, request: CloneRequest) -> ViewResponse:
        """Clone a project with all its tasks.

        One write action: the new project is its subject, under the name
        ``create``, so ``authorize`` and the outbox event see a new project,
        as on ``POST /projects``. The project, its outbox event and the copied
        tasks share one commit. The save refreshes the clone, so the response
        has the story-point total and the task count that the tasks add.
        """
        original = await self.get_one(id)  # the scope and the 404
        tasks = await original.awaitable_attrs.tasks if request.include_tasks else []
        clone = Project(
            name=request.new_name or f"{original.name} (Copy)",
            description=original.description,
            status=ProjectStatus.ACTIVE,
        )
        async with self.write_action(self.Action.CREATE, obj=clone):
            if tasks:
                await self.session.flush()  # clone.id, for the copied tasks
                self.session.add_all(
                    Task(
                        title=task.title,
                        description=task.description,
                        status=task.status,
                        priority=task.priority,
                        task_type=task.task_type,
                        project_id=clone.id,
                        severity=task.severity,
                        steps_to_reproduce=task.steps_to_reproduce,
                        story_points=task.story_points,
                        acceptance_criteria=task.acceptance_criteria,
                    )
                    for task in tasks
                )
        return clone

    @fr.get("/by-slug/{slug}")
    async def get_by_slug(self, slug: str) -> ViewResponse:
        """Retrieve by the natural key instead of the id.

        ``handle_get_one`` takes a predicate in place of the id, so this
        route is ``GET /{id}`` under another key: the same scope, the same
        404, the same read-auth, and the same response.
        The key is (organization, slug), not the slug alone: slugs are
        unique inside an organization, and an admin reads across tenants,
        so a bare slug could match a row per tenant.
        """
        return await self.handle_get_one(self._by_slug(slug))

    @fr.patch("/by-slug/{slug}")
    async def update_by_slug(
        self, slug: str, request: ProjectPatchRequest
    ) -> ViewResponse:
        """Update by the natural key, through the standard commit bracket.

        ``handle_update`` takes the same predicate, so this loads,
        authorizes, snapshots, runs the ``update`` verb (slug regeneration
        and the status event included) and commits, exactly as
        ``PATCH /{id}`` does.
        """
        return await self.handle_update(self._by_slug(slug), request)

    def _by_slug(self, slug: str) -> sa.ColumnElement[bool]:
        """The natural key: a slug is unique inside one organization."""
        return sa.and_(
            Project.slug == slug, Project.organization_id == Current.org_id()
        )

    @fr.get("/{id}/stats", response_model=ProjectStats)
    async def get_project_stats(self, id: int) -> ProjectStats:
        """Get task statistics for a project."""
        # Reuse the standard scoped read check.
        await self.handle_get_one(id)

        # Live tasks only: a task in the trash is not counted, as in the
        # task counts of the project itself.
        stmt = fr.apply_clauses(
            select(Task.status, func.count())
            .where(Task.project_id == id)
            .group_by(Task.status),
            TaskClauses.default_scope,
        )
        counts = dict((await self.session.execute(stmt)).tuples().all())
        todo = counts.get(TaskStatus.TODO, 0)
        in_progress = counts.get(TaskStatus.IN_PROGRESS, 0)
        done = counts.get(TaskStatus.DONE, 0)
        total = todo + in_progress + done
        completion = round(done / total * 100, 1) if total > 0 else 0.0

        return ProjectStats(
            total_tasks=total,
            todo_count=todo,
            in_progress_count=in_progress,
            done_count=done,
            completion_percent=completion,
        )

    # TaskResponse is a class that a function builds, so mypy does not accept
    # it inside a type such as list[...]. At runtime it is a normal class.
    @fr.get("/{id}/tasks", response_model=list[TaskResponse])  # type: ignore[valid-type]
    async def list_project_tasks(self, id: int) -> list[Task]:
        """List one project's tasks, through TaskView's own visibility.

        ``fr.resolve_scope(TaskView)`` returns the scope that view's reads
        apply, so ``GET /projects/{id}/tasks`` and ``GET /tasks`` cannot
        disagree about which tasks exist for the caller: a member sees the
        tasks assigned to them on both. Naming ``TaskClauses.visible``
        here instead would copy the rule, and the copy would drift the day
        ``TaskView`` declares a different one. The tenant floor is the
        criterion in ``tasks/models``, and ``handle_get_one`` has already
        checked the project itself.
        """
        await self.handle_get_one(id)
        stmt = fr.apply_clauses(
            select(Task).where(Task.project_id == id), fr.resolve_scope(TaskView)
        )
        return list((await self.session.scalars(stmt)).all())

    @fr.post("/{id}/tasks", response_model=TaskResponse, status_code=201)
    async def create_project_task(self, id: int, request: "TaskCreateRequest") -> Task:
        """Create through the task handler using the current request and session.

        The path supplies the project. The task handler checks references,
        applies task validation, and owns the write hooks and commit.
        """
        await self.get_one(id)
        task_view = TaskView(session=self.session, request=self.request)
        data = TaskCreate(project_id=id, **request.model_dump())
        return await task_view.handle_create(data)


class TaskCreateRequest(BaseModel):
    """Request body for creating a task via the nested /projects/{id}/tasks route."""

    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    task_type: TaskType = TaskType.TASK
    assignee_id: int | None = None
    severity: int | None = None
