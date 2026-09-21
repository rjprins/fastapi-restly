"""Label and TaskLabel schemas."""

from typing import Annotated

import fastapi_restly as fr

from ..tasks.models import Task, TaskClauses
from .models import Label


class LabelSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    """Schema for Label model."""

    name: str
    color: str = "#808080"
    # The tenant stamp: from the request context, never from the body.
    organization_id: fr.ReadOnly[int]


class TaskLabelSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    """Schema for TaskLabel association with metadata.

    ``task_id`` and ``label_id`` are existence-checked foreign keys: the wire
    format is a plain integer (``"task_id": 5``) on both request and
    response, and the framework validates the referenced row exists.
    ``label_id`` is checked in Label's default scope. ``task_id`` names its
    own scope, ``TaskClauses.visible``, so a member attaches labels only to
    a task assigned to them. Another task answers 404, as it does on
    ``GET /tasks``.
    """

    task_id: Annotated[int, fr.RefExists(Task, scope=TaskClauses.visible)]
    label_id: fr.MustExist[int, Label]
    added_by_id: fr.ReadOnly[int | None] = None  # stamped from Current.user_id
