"""Label and TaskLabel schemas."""

import fastapi_restly as fr

from ..tasks.models import Task
from .models import Label


class LabelSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    """Schema for Label model."""

    name: str
    color: str = "#808080"
    # The tenant stamp: from the request context, never from the body.
    organization_id: fr.ReadOnly[int]


class TaskLabelSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    """Schema for TaskLabel association with metadata.

    ``task_id`` and ``label_id`` are ``fr.MustExist[int, Model]`` foreign keys:
    the wire format is a plain integer (``"task_id": 5``) on both request and
    response, and the framework validates the referenced row exists.
    """

    task_id: fr.MustExist[int, Task]
    label_id: fr.MustExist[int, Label]
    added_by_id: fr.ReadOnly[int | None] = None  # stamped from Current.user_id
