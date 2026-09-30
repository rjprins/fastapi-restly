"""Restly's dataclass bases are keyword-only, so field order is free."""

from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr


class Task(fr.IDBase):
    version: Mapped[int] = mapped_column(default=1)
    title: Mapped[str]


class Tag(fr.DataclassBase):
    id: Mapped[int] = mapped_column(primary_key=True, default=0)
    name: Mapped[str]


class Note(fr.IDBase, fr.TimestampsMixin):
    pinned: Mapped[bool] = mapped_column(default=False)
    body: Mapped[str]


task = Task(title="a")
tag = Tag(name="b")
note = Note(body="c", pinned=True)
