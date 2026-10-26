from typing import Any

from sqlalchemy import ColumnElement, func
from sqlalchemy.orm import DeclarativeBase, Mapped

import fastapi_restly as fr


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class Chore(fr.IDMixin, Base):
    title: Mapped[str]
    done: Mapped[bool]


class ChoreSchema(fr.IDSchema[Chore]):
    title: str
    done: bool


class ChoreView(fr.AsyncRestView):
    model = Chore
    schema = ChoreSchema

    @fr.list_filter("open")
    def filter_open(self, value: bool) -> ColumnElement[bool]:
        """Chores that are not done, or with false the done chores."""
        return Chore.done.is_(not value)

    @fr.list_filter("title__length")
    def filter_title_length(self, value: int) -> ColumnElement[bool]:
        return func.length(Chore.title) == value


class DoneChoreView(ChoreView):
    # as the docs say: a subclass drops a key with a typed None
    filter_open: Any = None


def open_chores(view: ChoreView) -> ColumnElement[bool]:
    # mypy's warn_unused_ignores fails here if the decorator loses the type
    # of the method
    view.filter_open("yes")  # type: ignore[arg-type]
    return view.filter_open(True)
