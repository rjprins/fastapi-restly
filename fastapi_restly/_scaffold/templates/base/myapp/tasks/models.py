"""The Task model.

One resource lives in one package: the model here, its API contract in
``schemas.py``, and the view that serves it in ``views.py``. Adding a field
touches all three, and they sit together.

Replace this resource with your own, or add more packages beside it.
"""

from datetime import datetime

import fastapi_restly as fr
from sqlalchemy import orm


class Task(fr.TimestampsMixin, fr.IDBase):
    """A single task.

    ``fr.IDBase`` supplies an auto-increment ``id`` primary key.
    ``fr.TimestampsMixin`` adds ``created_at`` and ``updated_at``. Both share
    one ``MetaData``, so ``fr.DataclassBase.metadata`` is the whole schema.
    """

    title: orm.Mapped[str]
    done: orm.Mapped[bool] = orm.mapped_column(default=False)
    completed_at: orm.Mapped[datetime | None] = orm.mapped_column(default=None)
