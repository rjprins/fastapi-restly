"""A view that registers before a schema that its schema names, as when a
module includes the view first and defines the other schema below it."""

from typing import Optional

import fastapi
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import fastapi_restly as fr


class Base(DeclarativeBase):
    pass


class Note(fr.IDMixin, Base):
    __tablename__ = "forward_ref_note"
    text: Mapped[str]


class Author(fr.IDMixin, Base):
    __tablename__ = "forward_ref_author"
    name: Mapped[str]
    note_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("forward_ref_note.id"), default=None
    )
    note: Mapped[Optional[Note]] = relationship(default=None)


class AuthorSchema(fr.IDSchema):
    name: str
    note: fr.ReadOnly[Optional["NoteSchema"]] = None


app = fastapi.FastAPI()


@fr.include_view(app)
class AuthorView(fr.AsyncRestView):
    prefix = "/authors"
    model = Author
    schema = AuthorSchema


class NoteSchema(fr.IDSchema):
    text: str
