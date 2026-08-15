"""The public API contract for a task.

Restly derives the create and update bodies from this one schema, dropping the
read-only fields, so there is a single place to change when the contract does.
"""

from datetime import datetime

import fastapi_restly as fr


class TaskSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    """Request and response shape for ``/tasks``.

    ``fr.IDSchema`` adds a read-only ``id``, and ``fr.TimestampsSchemaMixin``
    adds read-only timestamps. ``fr.ReadOnly[...]`` marks a field the server
    owns: it appears in responses and is refused in request bodies.
    """

    title: str
    done: bool = False
    completed_at: fr.ReadOnly[datetime | None] = None
