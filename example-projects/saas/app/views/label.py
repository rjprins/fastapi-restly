"""Label and TaskLabel views."""

import fastapi_restly as fr

from ..models import Label, TaskLabel
from ..schemas import LabelSchema, TaskLabelSchema


class LabelView(fr.AsyncRestView):
    """CRUD for labels (organization-scoped)."""

    prefix = "/labels"
    model = Label
    schema = LabelSchema


class TaskLabelView(fr.AsyncRestView):
    """CRUD for task-label associations with metadata."""

    prefix = "/task-labels"
    model = TaskLabel
    schema = TaskLabelSchema
