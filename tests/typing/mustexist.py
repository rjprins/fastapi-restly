"""Typing fixture: ``MustExist`` is statically the pk scalar, not a wrapper.

``MustExist[pk]`` and ``MustExist[pk, Model]`` both read as ``pk`` -- so a
reference field reads and writes as a plain scalar (``data.post_id`` is an
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


class Account(Base):
    __tablename__ = "typing_mustexist_account"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str]


class CommentRead(fr.BaseSchema):
    post_id: fr.MustExist[int]  # -> int (target model inferred from the FK)
    account_id: fr.MustExist[UUID, Account]  # -> UUID (explicit model)


if TYPE_CHECKING:
    # `assert_type` fails the type check if a field stops resolving to the scalar.
    _comment = cast(CommentRead, None)
    assert_type(_comment.post_id, int)
    assert_type(_comment.account_id, UUID)
