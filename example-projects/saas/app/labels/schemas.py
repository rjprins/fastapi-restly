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

    ``task_id`` and ``label_id`` are existence-checked foreign keys: the JSON
    value is a plain integer (``"task_id": 5``) on both request and
    response. Restly checks that the row exists among the rows of its
    model's default scope, in the caller's organization. Which tasks a
    member may label is a permission, so it is in ``TaskLabelView.authorize``.
    """

    task_id: fr.MustExist[int, Task]
    label_id: fr.MustExist[int, Label]
    added_by_id: fr.ReadOnly[int | None] = None  # stamped from Current.user_id


# The response class that the view builds from TaskLabelSchema. A custom route
# names it, so OpenAPI shows TaskLabelResponse there, as on the CRUD routes.
TaskLabelResponse = fr.schemas.derive_schema_response(TaskLabelSchema)
