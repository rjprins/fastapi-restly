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

    Uses ``fr.IDSchema[T]`` (the JSON-API-style nested ``{"id": N}``
    wire format) rather than ``fr.FlatIDSchema[T]`` (scalar wire,
    server-side validation). FlatIDSchema would be the right choice
    for "validate but stay scalar," BUT the framework currently has a
    Pydantic-interaction bug where ``to_response_schema``'s
    ``FlatIDSchema.model_construct(id=N)`` output doesn't survive
    FastAPI's response serialization — the ``model_serializer`` runs
    against the underlying int instead of the constructed instance.
    See ``rut-notes/hooks_design_findings_for_docs_update.md`` (Finding
    #4) for the full diagnosis. Until that's fixed, this schema sticks
    with ``IDSchema[T]``.
    """

    task_id: fr.IDSchema[Task]
    label_id: fr.IDSchema[Label]
    added_by_id: int | None = None  # stamped server-side by TaskLabelView.make_new_object
