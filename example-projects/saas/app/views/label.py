"""Label and TaskLabel views."""

import fastapi_restly as fr

from ..models import Label, TaskLabel
from ..schemas import LabelSchema, TaskLabelSchema


class LabelView(fr.AsyncAlchemyView):
    """CRUD for labels (organization-scoped)."""

    prefix = "/labels"
    model = Label
    schema = LabelSchema


class TaskLabelView(fr.AsyncAlchemyView):
    """CRUD for task-label associations with metadata."""

    prefix = "/task-labels"
    model = TaskLabel
    schema = TaskLabelSchema
