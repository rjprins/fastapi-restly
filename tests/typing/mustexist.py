"""Typing fixture: ``MustExist`` is statically the pk scalar, not a wrapper.

``MustExist[Model]`` is ``int`` by default; ``MustExist[Model, PK]`` is ``PK``. So
a reference field reads and writes as a plain scalar (``data.post_id`` is an
``int``), unlike the ``IDRef``/``IDSchema`` wrappers. ``assert_type`` makes Pyright
fail if either stops resolving to the scalar.
"""

from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

from sqlalchemy import Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from typing_extensions import assert_type

import fastapi_restly as fr


class Base(DeclarativeBase):
    pass


class Post(Base):
    __tablename__ = "typing_mustexist_post"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]


class Account(Base):
    __tablename__ = "typing_mustexist_account"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str]


class CommentRead(fr.BaseSchema):
    post_id: fr.MustExist[Post]  # -> int (default)
    account_id: fr.MustExist[Account, UUID]  # -> UUID (explicit second arg)


if TYPE_CHECKING:
    # `assert_type` fails the type check if a field stops resolving to the scalar.
    _comment = cast(CommentRead, None)
    assert_type(_comment.post_id, int)
    assert_type(_comment.account_id, UUID)
