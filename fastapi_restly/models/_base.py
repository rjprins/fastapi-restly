import enum
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Enum, func
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import (
    Composite,
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    MappedColumn,
    RelationshipProperty,
    Synonym,
    composite,
    declared_attr,
    deferred,
    mapped_column,
    relationship,
    synonym,
)
from sqlalchemy.orm.decl_api import DCTransformDeclarative
from typing_extensions import dataclass_transform

from ..exc import RestlyConfigurationError

# Alternatives to cascade "all" and "all, delete-orphan" without
# "refresh-expire": a plain session.refresh(obj) on an async session would
# expire related objects that then lazy-load on access. Restly's own
# save_object refreshes by attribute name and does not cascade. See:
# https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#preventing-implicit-io-when-using-asyncsession
CASCADE_ALL_ASYNC = "save-update, merge, delete, expunge"
CASCADE_ALL_DELETE_ORPHAN_ASYNC = CASCADE_ALL_ASYNC + ", delete-orphan"


def utc_now() -> datetime:
    """Replacement for the deprecated datetime.utcnow()"""
    return datetime.now(timezone.utc)


def _require_dataclass_base(cls: type, mixin: str) -> None:
    """Refuse a dataclass mixin on a model whose declarative base is not one.

    SQLAlchemy accepts it, but makes that one model a dataclass. Its
    constructor then requires every field without a default, such as an ``id``
    declared without ``init=False``, and nothing says why.
    """
    base = next((c for c in cls.__mro__ if DeclarativeBase in c.__bases__), None)
    if base is None or issubclass(base, MappedAsDataclass):
        return
    raise RestlyConfigurationError(
        f"{cls.__name__} uses fr.{mixin}, which adds dataclass fields, but its "
        f"base {base.__name__} is not a dataclass base. SQLAlchemy would make "
        f"{cls.__name__} a dataclass while your other models are not, and that "
        f"changes its constructor. Add fr.ModelDefaults to {base.__name__}: "
        f"class {base.__name__}(fr.ModelDefaults, DeclarativeBase). Or declare "
        f"the columns on {cls.__name__} yourself."
    )


class TimestampsMixin(MappedAsDataclass, kw_only=True):
    """
    Dataclass mixin adding UTC-aware created_at and updated_at timestamps.

    Use it on models whose base has :class:`ModelDefaults`.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default_factory=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )

    def __init_subclass__(cls, **kw: Any) -> None:
        _require_dataclass_base(cls, "TimestampsMixin")
        super().__init_subclass__(**kw)


class IDMixin(MappedAsDataclass, kw_only=True):
    """Dataclass mixin adding an auto-incrementing integer `id` primary key.

    Use it on models whose base has :class:`ModelDefaults`.
    """

    id: Mapped[int] = mapped_column(init=False, primary_key=True)

    def __init_subclass__(cls, **kw: Any) -> None:
        _require_dataclass_base(cls, "IDMixin")
        super().__init_subclass__(**kw)


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


# A type checker reads kw_only from the metaclass, not from a base class's
# keyword, so without this it rejects a required column after a defaulted one.
# The field specifiers repeat SQLAlchemy's own.
@dataclass_transform(
    kw_only_default=True,
    field_specifiers=(
        MappedColumn,
        RelationshipProperty,
        Composite,
        Synonym,
        mapped_column,
        relationship,
        composite,
        synonym,
        deferred,
    ),
)
class _KwOnlyDataclassMeta(DCTransformDeclarative):
    pass


_TYPE_ANNOTATION_MAP: dict[Any, Any] = {
    # Datetimes represent UTC instants by default. PostgreSQL enforces this
    # with TIMESTAMP WITH TIME ZONE. Use mapped_column(DateTime()) to opt a
    # specific column into naive wall-clock semantics.
    datetime: DateTime(timezone=True),
    # native_enum=False so enums are persisted as strings in the
    # database, not as Postgres TYPE objects. This prevents
    # requiring database migrations for every enum change.
    enum.Enum: Enum(enum.Enum, native_enum=False, length=64),
}


class ModelDefaults(
    AsyncAttrs,
    TableNameMixin,
    MappedAsDataclass,
    metaclass=_KwOnlyDataclassMeta,
    kw_only=True,
):
    """Restly's defaults for SQLAlchemy models, for your own declarative base.

    Add it to your base class, before ``DeclarativeBase``::

        class Base(fr.ModelDefaults, DeclarativeBase):
            pass

    ``Base`` stays yours: it has its own registry and ``MetaData``, as with
    any ``DeclarativeBase``. Your models get:

    * A dataclass constructor that takes keyword arguments only. Type checkers
      accept a required field after a field with a default.
    * A table name in snake_case from the class name: ``BlogPost`` gets
      ``blog_post``. Set ``__tablename__`` on a model to choose another name.
    * ``Mapped[datetime]`` as ``DateTime(timezone=True)``. Use
      ``mapped_column(DateTime())`` for a column without a time zone.
    * An ``enum.Enum`` as a string column of up to 64 characters, not a
      database enum type, so a new member needs no migration.
    * ``awaitable_attrs`` from SQLAlchemy's ``AsyncAttrs``. An unloaded
      attribute can then be reached from plain async code,
      ``await obj.awaitable_attrs.items``, where a bare ``obj.items`` would
      raise ``MissingGreenlet``. Views eager-load what the response class
      names, so this is for the code that runs outside that: an
      ``after_action_commit`` hook, a custom business method.

    Declare your own ``metadata`` and ``type_annotation_map`` on ``Base`` as
    SQLAlchemy documents them. Your type entries win over Restly's.
    """

    def __init_subclass__(cls, **kw: Any) -> None:
        super().__init_subclass__(**kw)
        if DeclarativeBase in cls.__bases__:
            # SQLAlchemy has set up the registry by now, with the base's own
            # type_annotation_map. Restly's entries fill in what it leaves out.
            own = cls.registry.type_annotation_map
            cls.registry.update_type_annotation_map(
                {
                    annotation: sql_type
                    for annotation, sql_type in _TYPE_ANNOTATION_MAP.items()
                    if annotation not in own
                }
            )


class DataclassBase(ModelDefaults, DeclarativeBase):
    """SQLAlchemy declarative base with dataclass semantics.

    It is :class:`ModelDefaults` on a ``DeclarativeBase`` that Restly owns.
    """


class IDBase(IDMixin, DataclassBase):
    """Convenience base: DataclassBase + integer `id` primary key."""

    __abstract__ = True
