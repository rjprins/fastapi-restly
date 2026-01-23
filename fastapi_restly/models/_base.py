import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Enum, func, select
from sqlalchemy.exc import NoResultFound
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
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampsMixin(MappedAsDataclass, kw_only=True):
    """
    Mixin to add created_at and updated_at timestamps (timezone naive).
    """

    created_at: Mapped[datetime] = mapped_column(
        default_factory=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        default_factory=utc_now, onupdate=utc_now, server_default=func.now()
    )


class IDMixin(MappedAsDataclass, kw_only=True):
    id: Mapped[int] = mapped_column(init=False, primary_key=True)


class TableNameMixin:
    @declared_attr
    @classmethod
    def __tablename__(cls) -> Any:
        return underscore(cls.__name__)


def underscore(name: str) -> str:
    result = []
    for i, c in enumerate(name):
        if c.isupper() and i > 0 and not name[i - 1].isupper():
            result.append("_")
        result.append(c.lower())
    return "".join(result)


class Base(TableNameMixin, MappedAsDataclass, DeclarativeBase, kw_only=True):
    type_annotation_map = {
        # native_enum=False so enums are persisted as strings in the
        # database, not as Postgres TYPE objects. This prevents
        # requiring database migrations for every enum change.
        enum.Enum: Enum(enum.Enum, native_enum=False, length=64)
    }

    @classmethod
    async def get_one_or_create(cls, session, **kwargs):
        """
        Do a database select for the given keyword arguments.
        The arguments must uniquely select a row.
        If no matching row/object is found, create a new object and
        return it.
        """
        select_query = select(cls).filter_by(**kwargs)
        results = await session.scalars(select_query)
        try:
            return results.one()
        except NoResultFound:
            new_instance = cls(**kwargs)
            session.add(new_instance)
            await session.flush()
            return new_instance


class IDBase(IDMixin, Base):
    __abstract__ = True


class IDStampsBase(TimestampsMixin, IDBase):
    __abstract__ = True
