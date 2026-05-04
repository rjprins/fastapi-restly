import enum
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Enum, func
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


class DataclassBase(TableNameMixin, MappedAsDataclass, DeclarativeBase, kw_only=True):
    """SQLAlchemy declarative base with dataclass semantics."""

    type_annotation_map = {
        # native_enum=False so enums are persisted as strings in the
        # database, not as Postgres TYPE objects. This prevents
        # requiring database migrations for every enum change.
        enum.Enum: Enum(enum.Enum, native_enum=False, length=64)
    }


class IDBase(IDMixin, DataclassBase):
    """Convenience base: DataclassBase + integer `id` primary key."""

    __abstract__ = True
