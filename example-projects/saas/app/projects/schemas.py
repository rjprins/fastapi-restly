"""Project schema."""

from datetime import datetime

from pydantic import computed_field

import fastapi_restly as fr

from .models import ProjectStatus


class ProjectSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    """Schema for Project model.

    Audit stamps (``created_by_id`` / ``updated_by_id``) and the
    denormalized roll-up (``total_story_points``) are marked ``ReadOnly``:
    they're populated server-side and the framework will refuse to copy
    them off a request body.
    """

    name: str
    slug: str = ""  # from the name if blank: see app.projects.models
    description: str = ""
    status: ProjectStatus = ProjectStatus.ACTIVE
    deleted_at: datetime | None = None
    # The tenant stamp: from the request context, never from the body.
    organization_id: fr.ReadOnly[int]

    # Server-stamped — readonly so PATCH bodies can't spoof them.
    created_by_id: fr.ReadOnly[int | None] = None
    updated_by_id: fr.ReadOnly[int | None] = None

    # Kept by the session event in app.tasks.models, not by the client.
    total_story_points: fr.ReadOnly[int] = 0

    # Whether the caller may edit the project: set by
    # ProjectView.to_single_response, not stored.
    can_edit: fr.ReadOnly[bool | None] = None

    # SQL expressions on the model (app.tasks.models), not columns.
    # ReadOnly keeps them out of the create and update bodies.
    task_count: fr.ReadOnly[int | None] = None
    completed_task_count: fr.ReadOnly[int | None] = None

    # mypy does not support a decorator on a property; pydantic documents this ignore
    @computed_field  # type: ignore[prop-decorator]
    @property
    def completion_percent(self) -> float | None:
        """Calculate completion percentage from task counts."""
        if self.task_count is None or self.task_count == 0:
            return None
        if self.completed_task_count is None:
            return 0.0
        return round((self.completed_task_count / self.task_count) * 100, 1)
