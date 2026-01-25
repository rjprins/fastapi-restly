"""Task schema."""

import fastapi_restly as fr

from ..models import Task, TaskStatus, TaskPriority


class TaskSchema(fr.TimestampsSchemaMixin, fr.IDSchema[Task]):
    """Schema for Task model."""

    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    project_id: int
    assignee_id: int | None = None
