import enum
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Enum, func
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    declared_attr,
    mapped_column,
)

# Provide an alternative settings for relationship cascade "all" and
# "all, delete-orphan". The "refresh-expire" cascade will cause
# issues in an async context. See also:
# https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#preventing-implicit-io-when-using-asyncsession
# `CASCADE_ALL_ASYNC` should be used instead.
CASCADE_ALL_ASYNC = "save-update, merge, delete, expunge"
CASCADE_ALL_DELETE_ORPHAN_ASYNC = CASCADE_ALL_ASYNC + ", delete-orphan"


def utc_now() -> datetime:
    """Replacement for the deprecated datetime.utcnow()"""
    return datetime.now(timezone.utc)


class TimestampsMixin(MappedAsDataclass, kw_only=True):
    """
    Dataclass mixin adding UTC-aware created_at and updated_at timestamps.
    """

    created_at: Mapped[datetime] = mapped_column(
        default_factory=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        default_factory=utc_now, onupdate=utc_now, server_default=func.now()
    )


class IDMixin(MappedAsDataclass, kw_only=True):
    """Dataclass mixin adding an auto-incrementing integer `id` primary key."""

    id: Mapped[int] = mapped_column(init=False, primary_key=True)


class TableNameMixin:
    """Mixin that auto-generates snake_case table names from class names."""

    @declared_attr
    @classmethod
    def __tablename__(cls) -> Any:
        return underscore(cls.__name__)


def underscore(name: str) -> str:
    """Convert CamelCase class name to snake_case table name.

    Handles acronyms correctly: HTTPServer -> http_server, XMLParser -> xml_parser.
    """
    # Insert underscore before an uppercase letter that follows a lowercase letter
    s1 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    # Insert underscore before an uppercase letter that is followed by a lowercase letter
    # (handles the end of an acronym: "HTTPServer" -> "HTTP_Server")
    s2 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s1)
    return s2.lower()


class DataclassBase(
    AsyncAttrs, TableNameMixin, MappedAsDataclass, DeclarativeBase, kw_only=True
):
    """Convenience SQLAlchemy declarative base.

    * ``MappedAsDataclass`` provides keyword-only dataclass constructors.
    * ``AsyncAttrs`` adds ``awaitable_attrs`` for loading unloaded attributes
      from async code, for example ``await obj.awaitable_attrs.items``.
    * ``__tablename__`` is generated automatically from the model class name
      using snake_case: ``BlogPost`` becomes ``"blog_post"``. Set
      ``__tablename__`` explicitly on a model to override the generated name.
    * Python ``enum.Enum`` annotations use a non-native SQLAlchemy ``Enum``
      backed by ``VARCHAR(64)``. Enum member names are stored as strings, so
      databases such as PostgreSQL do not create native enum types that require
      migrations when members change.

    Views eager-load the attributes named by the response schema.
    ``awaitable_attrs`` is primarily useful in code outside that path, such as
    an ``after_commit`` hook or custom business method.
    """

    type_annotation_map = {
        # native_enum=False so enums are persisted as strings in the
        # database, not as Postgres TYPE objects. This prevents
        # requiring database migrations for every enum change.
        enum.Enum: Enum(enum.Enum, native_enum=False, length=64)
    }


class IDBase(IDMixin, DataclassBase):
    """Dataclass base with an integer ``id`` primary key.

    It inherits dataclass semantics, automatic snake_case table naming, and
    ``awaitable_attrs`` from ``DataclassBase``.

    This shorthand is convenient for typical Restly models:

    .. code-block:: python

       import fastapi_restly as fr
       from sqlalchemy.orm import Mapped

       class User(fr.IDBase):
           name: Mapped[str]

    It is equivalent to declaring the ``id`` column on ``DataclassBase``:

    .. code-block:: python

       import fastapi_restly as fr
       from sqlalchemy.orm import Mapped, mapped_column

       class User(fr.DataclassBase):
           id: Mapped[int] = mapped_column(init=False, primary_key=True)
           name: Mapped[str]

    Both forms infer ``__tablename__ = "user"``, expose ``awaitable_attrs``,
    and exclude ``id`` from the generated constructor.
    """

    __abstract__ = True
