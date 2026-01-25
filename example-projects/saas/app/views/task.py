"""Task view."""

import fastapi_restly as fr

from ..models import Task
from ..schemas import TaskSchema


class TaskView(fr.AsyncAlchemyView):
    """CRUD endpoints for tasks."""

    prefix = "/tasks"
    model = Task
    schema = TaskSchema
