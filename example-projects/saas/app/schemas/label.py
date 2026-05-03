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

    ``task_id`` and ``label_id`` use ``fr.IDRef[T]``: the wire format is
    a plain integer (``"task_id": 5``) on both request and response, and
    the framework still validates the referenced row exists.
    """

    task_id: fr.IDRef[Task]
    label_id: fr.IDRef[Label]
    added_by_id: int | None = None  # stamped server-side by TaskLabelView.make_new_object
