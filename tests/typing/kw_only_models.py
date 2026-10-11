"""Models on a base with fr.ModelDefaults are keyword-only, so field order is free."""

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import fastapi_restly as fr


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class Task(fr.IDMixin, Base):
    version: Mapped[int] = mapped_column(default=1)
    title: Mapped[str]


class Tag(Base):
    id: Mapped[int] = mapped_column(primary_key=True, default=0)
    name: Mapped[str]


class Note(fr.TimestampsMixin, fr.IDMixin, Base):
    pinned: Mapped[bool] = mapped_column(default=False)
    body: Mapped[str]


task = Task(title="a")
tag = Tag(name="b")
note = Note(body="c", pinned=True)
