"""The User model.

One resource lives in one package: the model here, its API contract in
``schemas.py``, and the view that serves it in ``views.py``. Adding a field
touches all three, and they sit together.

Add more packages beside this one, each laid out the same way.
"""

import fastapi_restly as fr
from sqlalchemy import orm


class User(fr.TimestampsMixin, fr.IDBase):
    """A user.

    ``fr.IDBase`` supplies an auto-increment ``id`` primary key.
    ``fr.TimestampsMixin`` adds ``created_at`` and ``updated_at``. Both share
    one ``MetaData``, so ``fr.DataclassBase.metadata`` is the whole schema.
    """

    email: orm.Mapped[str] = orm.mapped_column(unique=True)
    name: orm.Mapped[str]
