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

        Overrides the *bare* business ``create`` verb (auth-free, commit-free).
        The denormalized ``Project.total_story_points`` field is kept in
        sync here rather than in a SQLAlchemy event because (a) the math
        depends on the schema's optional ``story_points`` field and (b)
        we want the rollup to live in the same transaction as the task
        write. Both the task insert and the project update flush together
        when ``save_object`` is called; ``handle_create`` then commits the
        pair atomically.
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

        Overrides the *bare* business ``update`` verb, which receives the
        already-loaded ``obj``: ``handle_update`` loads it through ``get_one``
        (which enforces row-level access — assignee match — and 404s), runs
        ``authorize``, then calls this method, and commits via the bracket.

        Pattern from the matrix's "update related object based on updated
        object" row: capture old value before mutation, apply the update,
        then propagate the delta to the related row inside the same
        transaction. ``save_object`` at the end flushes both rows together.

        Also illustrates the NOT_SET-style sentinel pattern: this view uses
        ``model_dump(exclude_unset=True)`` so a ``None`` *that the client
        explicitly sent* clears the field, while a missing key leaves it
        alone. (The Brenntag ``NOT_SET`` sentinel exists for the rarer
        case where ``None`` itself is a meaningful explicit value, distinct
        from "not provided" — that requires a custom marker, which we don't
        need with the explicit-vs-omitted distinction Pydantic gives us.)
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

        # Apply writable fields via the framework helper (skips ReadOnly,
        # respects exclude_unset semantics from the wire). Then bump version
        # explicitly — if the client sent a version it was already validated
        # above, and we always want server-side increment.
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
        """Run a status transition as a genuinely-custom write action.

        A transition is *update-shaped* but not a plain PATCH (the input is
        the route itself, the allowed moves are gated by a state machine), so
        instead of reusing ``handle_update`` we run the explicit custom-action
        bracket. Crucially the route now OWNS its commit: ``handle_<verb>``
        owns the commit for the CRUD verbs, but the request-session dependency
        no longer commits on response, so any custom write that mutates the DB
        must call ``self._commit()`` itself or the change is silently lost.

        The bracket below mirrors ``handle_update`` so the transition still gets
        authorize / snapshot / before_commit / after_commit — skipping those is
        exactly what the handle design is built to prevent.
        """
        if target_status not in VALID_TRANSITIONS.get(source_status, []):
            raise RuntimeError(
                f"Invalid task transition definition: {source_status.value} -> {target_status.value}"
            )

        # Load with scope + 404 + read-auth via the canonical read handler.
        task = await self.handle_get_one(id)
        if task.status != source_status:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot {action} task with status '{task.status.value}'. "
                    f"Must be '{source_status.value}'."
                ),
            )

        # Action-specific policy gate + pre-mutation snapshot for the hooks.
        await self.authorize(action, obj=task)
        old = self.snapshot(task)
        task.status = target_status
        task.version += 1
        await self.save_object(task)

        # The route owns the bracket: before_commit (in-transaction side
        # effects) -> commit -> after_commit (post-commit side effects).
        await self.before_commit(action, new=task, old=old)
        await self._commit()
        await self.after_commit(action, new=task, old=old)
        return task

    @fr.post("/import-csv", response_model=BulkResult)
    async def import_csv(
        self,
        project_id: int = fastapi.Form(...),  # noqa: B008
        file: fastapi.UploadFile = fastapi.File(...),  # noqa: B008
    ) -> BulkResult:
        """Parse a CSV of tasks and bulk-create them.

        Demonstrates "Bulk import/update from spreadsheet" from the matrix —
        a custom route because (a) the input is multipart not JSON, and
        (b) the response is a per-row success/failure report rather than
        a list of created objects.

        Each row is fed through the business ``create`` verb via
        ``TaskCreateSchema``, so every row inherits the same validation chain
        and rollup as ``POST /tasks/``. ``create`` flushes but does NOT commit
        -- and the request-session dependency no longer commits on response --
        so this custom route owns the commit: a single ``self._commit()`` at
        the end persists all successful rows together. Pydantic surfaces field
        errors per row; each row also runs inside its own ``begin_nested()``
        SAVEPOINT, so a row that fails at flush (e.g. a DB constraint) rolls
        back only itself and the rest of the batch still persists.
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
        await self._commit()
        return BulkResult(success=success, failed=failed, errors=errors)

    @fr.post("/bulk", response_model=BulkResult)
    async def bulk_create(self, request: BulkCreateRequest) -> BulkResult:
        """Create multiple tasks at once.

        Delegates each item to the business ``create`` verb so the same
        archived-project guard, conditional-field validation, cross-resource
        validation, and story-point rollup that apply to ``POST /tasks/`` apply
        per row. ``create`` flushes without committing, so this custom route
        owns the commit -- the request-session dependency no longer commits on
        response, so a single ``self._commit()`` at the end persists every
        successful row together. Each row runs in its own ``begin_nested()``
        SAVEPOINT so one row failing at flush can't abort the whole batch.
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
        await self._commit()
        return BulkResult(success=success, failed=failed, errors=errors)

    @fr.post("/bulk-delete", response_model=BulkResult)
    async def bulk_delete(self, request: BulkDeleteRequest) -> BulkResult:
        """Delete multiple tasks by IDs.

        Uses ``handle_get_one`` per id so each delete inherits the row-level
        access check (only the assignee may see/touch the task).
        ``handle_get_one`` raises 404 for both "missing" and "not yours", which
        we catch and report.

        This is a genuinely-custom write (a multi-row report, not a single
        verb), so it owns its commit. ``delete_object`` flushes but does NOT
        commit -- the request-session dependency no longer commits on response,
        so without the trailing ``self._commit()`` every soft-delete here would
        be silently rolled back. Each id runs in its own ``begin_nested()``
        SAVEPOINT (so one failure can't poison the rest), and we commit ONCE at
        the end so all successful rows persist together.
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
        await self._commit()
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
