import enum
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Enum, func
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import (
    Composite,
    DeclarativeBase,
    DeclarativeBaseNoMeta,
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


class TimestampsMixin(MappedAsDataclass, kw_only=True, eq=False):
    """
    Dataclass mixin adding UTC-aware created_at and updated_at timestamps.

    Made for models on a base with :class:`ModelDefaults`. On another base,
    SQLAlchemy makes each model that uses it a dataclass.
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


class IDMixin(MappedAsDataclass, kw_only=True, eq=False):
    """Dataclass mixin adding an auto-incrementing integer `id` primary key.

    Made for models on a base with :class:`ModelDefaults`. On another base,
    SQLAlchemy makes each model that uses it a dataclass.
    """

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


def _declarative_base(cls: type) -> type | None:
    """Return the class in ``cls``'s bases that subclasses DeclarativeBase."""
    return next(
        (
            c
            for c in cls.__mro__
            if DeclarativeBase in c.__bases__ or DeclarativeBaseNoMeta in c.__bases__
        ),
        None,
    )


class ModelDefaults(
    AsyncAttrs,
    TableNameMixin,
    MappedAsDataclass,
    metaclass=_KwOnlyDataclassMeta,
    kw_only=True,
    eq=False,
):
    """Restly's defaults for SQLAlchemy models, for your own declarative base.

    Add it to your base class, before ``DeclarativeBase``::

        class Base(fr.ModelDefaults, DeclarativeBase):
            pass

    ``Base`` stays yours: it has its own registry and ``MetaData``, as with
    any ``DeclarativeBase``. Your models get:

    * A dataclass constructor that takes keyword arguments only. Type checkers
      accept a required field after a field with a default.
    * Equality by identity, as for any SQLAlchemy model: two objects are equal
      only when they are the same object. So a model object can go in a
      ``set``, and comparing objects never loads a relationship.
    * A table name in snake_case from the class name: ``BlogPost`` gets
      ``blog_post``. Set ``__tablename__`` on a model to choose another name.
    * ``Mapped[datetime]`` as ``DateTime(timezone=True)``. Use
      ``mapped_column(DateTime())`` for a column without a time zone.
    * An ``enum.Enum`` as a string column of up to 64 characters that stores
      the member's name, not a database enum type. A new member then needs no
      migration.
    * ``awaitable_attrs`` from SQLAlchemy's ``AsyncAttrs``. An unloaded
      attribute can then be reached from plain async code,
      ``await obj.awaitable_attrs.items``, where a bare ``obj.items`` would
      raise ``MissingGreenlet``. Views eager-load what the response class
      names, so this is for the code that runs outside that: an
      ``after_action_commit`` hook, a custom business method.

    Declare your own ``metadata`` and ``type_annotation_map`` on ``Base`` as
    SQLAlchemy documents them. Your type entries win over Restly's.

    Set dataclass options such as ``eq=True`` on a model, not on ``Base``.
    SQLAlchemy takes the options from the first dataclass class in a model's
    bases, which is often :class:`IDMixin`, so options on ``Base`` would apply
    to some models only. ``Base`` with options raises
    :class:`~fastapi_restly.exc.RestlyConfigurationError`.
    """

    def __init_subclass__(cls, **kw: Any) -> None:
        base = _declarative_base(cls)
        if base is cls and kw:
            option, value = next(iter(kw.items()))
            raise RestlyConfigurationError(
                f"{cls.__name__} sets the dataclass options {', '.join(kw)}. On a "
                f"base with fr.ModelDefaults, set them on each model instead: "
                f"class Post(fr.IDMixin, {cls.__name__}, {option}={value!r}). "
                f"SQLAlchemy takes the options from the first dataclass class in "
                f"a model's bases. For a model with fr.IDMixin or "
                f"fr.TimestampsMixin, that is the mixin, so options on "
                f"{cls.__name__} would not apply to it."
            )
        if base is not None and not issubclass(base, ModelDefaults):
            raise RestlyConfigurationError(
                f"{cls.__name__} adds fr.ModelDefaults below the declarative base "
                f"{base.__name__}. Restly's defaults change the registry of the "
                f"base, so fr.ModelDefaults goes on the base itself: "
                f"class {base.__name__}(fr.ModelDefaults, DeclarativeBase). That "
                f"makes every model on {base.__name__} a dataclass. Or leave "
                f"fr.ModelDefaults out: Restly's views also work without it."
            )
        super().__init_subclass__(**kw)
        if base is cls:
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
