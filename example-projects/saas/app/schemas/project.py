"""Project schema."""

from datetime import datetime

from pydantic import computed_field

import fastapi_restly as fr

from ..models import Project, ProjectStatus


class ProjectSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    """Schema for Project model."""

    name: str
    description: str = ""
    status: ProjectStatus = ProjectStatus.ACTIVE
    deleted_at: datetime | None = None
    organization_id: int

    # Computed fields - populated by the view, not stored in DB
    # Marked as ReadOnly so they're excluded from create/update
    task_count: fr.ReadOnly[int | None] = None
    completed_task_count: fr.ReadOnly[int | None] = None

    @computed_field
    @property
    def completion_percent(self) -> float | None:
        """Calculate completion percentage from task counts."""
        if self.task_count is None or self.task_count == 0:
            return None
        if self.completed_task_count is None:
            return 0.0
        return round((self.completed_task_count / self.task_count) * 100, 1)
