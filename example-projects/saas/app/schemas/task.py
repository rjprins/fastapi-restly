"""Task schema."""

from datetime import datetime

import fastapi_restly as fr

from ..models import Project, Task, TaskPriority, TaskStatus, TaskType, User


class TaskSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    """Schema for Task model.

    Conditional validation lives in TaskView.create/update because schema
    validators also run for query filtering.
    """

    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    task_type: TaskType = TaskType.TASK
    project_id: fr.MustExist[int, Project]
    assignee_id: fr.MustExist[int, User] | None = None
    parent_id: fr.MustExist[int, Task] | None = None

    # Type-specific fields (polymorphic)
    # Bug-specific
    severity: int | None = None
    steps_to_reproduce: str | None = None
    # Feature-specific
    story_points: int | None = None
    acceptance_criteria: str | None = None

    # Optimistic locking
    version: int = 1

    # Stamped server-side by SoftDeleteMixin / AuditStampedMixin on TaskView.
    deleted_at: fr.ReadOnly[datetime | None] = None
    created_by_id: fr.ReadOnly[int | None] = None
    updated_by_id: fr.ReadOnly[int | None] = None
