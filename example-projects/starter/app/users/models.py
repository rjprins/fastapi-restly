import fastapi_restly as fr
from sqlalchemy import orm


class User(fr.TimestampsMixin, fr.IDBase):
    email: orm.Mapped[str] = orm.mapped_column(unique=True)
    name: orm.Mapped[str]
