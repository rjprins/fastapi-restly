import fastapi_restly as fr
from sqlalchemy import orm

from ..models import Base


class User(fr.TimestampsMixin, fr.IDMixin, Base):
    email: orm.Mapped[str] = orm.mapped_column(unique=True)
    name: orm.Mapped[str]
