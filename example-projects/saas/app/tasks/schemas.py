"""Task schema."""

from datetime import datetime

import fastapi_restly as fr

from ..projects.models import Project
from ..users.models import User
from .models import Task, TaskPriority, TaskStatus, TaskType


class TaskSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    """Schema for Task model.

    Conditional validation lives in TaskView.create/update: on update the
    rule needs the stored row, which a schema validator cannot see.
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

    # Stamped server-side by the model mixins (app.models).
    deleted_at: fr.ReadOnly[datetime | None] = None
    created_by_id: fr.ReadOnly[int | None] = None
    updated_by_id: fr.ReadOnly[int | None] = None
