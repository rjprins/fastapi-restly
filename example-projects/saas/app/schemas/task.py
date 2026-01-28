"""Task schema."""

import fastapi_restly as fr

from ..models import Task, TaskStatus, TaskPriority, TaskType


class TaskSchema(fr.TimestampsSchemaMixin, fr.IDSchema[Task]):
    """Schema for Task model.

    Note: Conditional validation (bugs require severity) is implemented
    in TaskView.process_post and process_patch rather than here, because
    schema validators also run during query filtering which causes issues.
    """

    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    task_type: TaskType = TaskType.TASK
    project_id: int
    assignee_id: int | None = None
    parent_id: int | None = None

    # Type-specific fields (polymorphic)
    # Bug-specific
    severity: int | None = None
    steps_to_reproduce: str | None = None
    # Feature-specific
    story_points: int | None = None
    acceptance_criteria: str | None = None

    # Optimistic locking
    version: int = 1
