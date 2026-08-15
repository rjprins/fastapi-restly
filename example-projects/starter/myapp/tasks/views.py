"""The Task API.

Restly generates list, retrieve, create, update, and delete from ``model`` and
``schema``. The three overrides below are the seams you will reach for most.
Delete any you do not need: the generated routes work without them.

Add your own view classes beside this one, then list them in ``myapp/main.py``.
"""

from datetime import datetime, timezone

import fastapi_restly as fr
import sqlalchemy as sa

from .models import Task
from .schemas import TaskSchema


class TaskView(fr.AsyncRestView[Task, TaskSchema]):
    """CRUD for ``/tasks``, plus one custom route.

    The type parameters are optional: a bare ``fr.AsyncRestView`` works. They
    earn their place as soon as you override a method, because they tell your
    type checker that ``create`` returns a ``Task``.
    """

    prefix = "/tasks"
    model = Task
    schema = TaskSchema

    session: fr.AsyncSessionDep

    def build_query(self) -> sa.Select:
        """Shape every read.

        List, count, and retrieve all run through this query, so a row hidden
        here returns 404 from ``GET /tasks/{id}`` as well. Tenant scoping,
        soft-delete filtering, and row-level visibility belong here.
        """
        return super().build_query().order_by(Task.id)

    async def create(self, schema_obj) -> Task:
        """Business logic for one create.

        Business methods are where your own logic goes. Never commit here: the
        framework brackets the write and commits once the request succeeds.
        """
        task = await super().create(schema_obj)
        if task.done:
            task.completed_at = datetime.now(timezone.utc)
        return task

    @fr.post("/{id}/complete", response_model=TaskSchema)
    async def complete(self, id: int) -> Task:
        """A custom route beside the generated ones.

        ``write_action`` brackets a hand-written write so it commits on the same
        terms as a generated one.
        """
        task = await self.handle_get_one(id)

        async with self.write_action("complete", obj=task):
            task.done = True
            task.completed_at = datetime.now(timezone.utc)
            await self.save_object(task)

        return task
