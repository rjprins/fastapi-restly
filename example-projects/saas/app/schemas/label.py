"""Label and TaskLabel schemas."""

import fastapi_restly as fr

from ..models import Label, Task


class LabelSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    """Schema for Label model."""

    name: str
    color: str = "#808080"
    organization_id: int


class TaskLabelSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    """Schema for TaskLabel association with metadata.

    ``task_id`` and ``label_id`` use ``fr.IDSchema[T]`` — the wire format is
    ``{"id": N}`` rather than a plain integer. The framework validates that the
    referenced row exists (returning 422 if not) and resolves it to the FK
    column value automatically.
    """

    task_id: fr.IDSchema[Task]
    label_id: fr.IDSchema[Label]
    added_by_id: int | None = None  # stamped server-side by TaskLabelView.make_new_object
