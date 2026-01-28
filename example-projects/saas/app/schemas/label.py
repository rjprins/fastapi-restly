"""Label and TaskLabel schemas."""

import fastapi_restly as fr

from ..models import Label, TaskLabel


class LabelSchema(fr.TimestampsSchemaMixin, fr.IDSchema[Label]):
    """Schema for Label model."""

    name: str
    color: str = "#808080"
    organization_id: int


class TaskLabelSchema(fr.TimestampsSchemaMixin, fr.IDSchema[TaskLabel]):
    """Schema for TaskLabel association with metadata."""

    task_id: int
    label_id: int
    added_by_id: int | None = None
