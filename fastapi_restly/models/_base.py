import enum
import re
from datetime import datetime, timezone
from inspect import unwrap
from typing import Any, dataclass_transform

from sqlalchemy import DateTime, Enum, func, inspect
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

from ..exc import RestlyConfigurationError

#: The cascade ``"all"`` without ``"refresh-expire"``, for
#: ``relationship(cascade=...)`` on an async session:
#: ``"save-update, merge, delete, expunge"``. With ``"all"``, your own
#: ``session.refresh(obj)`` also expires the related objects. On an async
#: session, the next read of one can then raise ``MissingGreenlet``. Restly's
#: own save after a write refreshes by attribute name and does not cascade,
#: so ``"all"`` is safe on Restly's write paths. See SQLAlchemy's
#: `Preventing Implicit IO when Using AsyncSession
#: <https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#preventing-implicit-io-when-using-asyncsession>`_.
CASCADE_ALL_ASYNC = "save-update, merge, delete, expunge"
#: :data:`CASCADE_ALL_ASYNC` with ``"delete-orphan"``: the cascade
#: ``"all, delete-orphan"`` without ``"refresh-expire"``.
CASCADE_ALL_DELETE_ORPHAN_ASYNC = CASCADE_ALL_ASYNC + ", delete-orphan"


def _reject_dataclass_method_from_a_mixin(cls: type, base: type, option: str) -> None:
    """Raise when a model, or ``base`` itself, gets ``__eq__`` or ``__repr__``
    from a mixin.

    ``option`` is ``"eq"`` or ``"repr"``. A dataclass mixin that leaves the
    option out gets Python's default, True. What happens then depends on
    where the mixin is in the bases. When the mixin comes first, SQLAlchemy
    takes the class's options from it, so the class gets a method that reads
    all its fields. When it comes later, the class inherits the mixin's own
    method, which reads the mixin's fields only. Either way the class loses
    its default: identity equality for ``eq``, or the repr of
    ``ModelDefaults`` for ``repr``.

    A class that passes the option as True, or that has a parent model that
    does, asks for the method. The caller skips a class that writes the
    method itself.
    """
    method = f"__{option}__"
    owner = next(b for b in cls.__mro__ if method in b.__dict__)
    if not _left_to_python(owner, option) or _written_by_hand(owner, method):
        # The default (object.__eq__ or ModelDefaults.__repr__), a method
        # someone wrote, or a method someone asked for.
        return
    if owner is cls:
        # Generated from the options that SQLAlchemy took from a base.
        mixin = next(
            (
                b
                for b in cls.__mro__[1:]
                if _left_to_python(b, option) and not issubclass(b, base)
            ),
            None,
        )
        if mixin is None:
            return
    elif not issubclass(owner, base):
        mixin = owner
    else:
        # A method that a parent model wrote.
        return
    name = cls.__name__
    target = "each model" if cls is base else "the model"
    if option == "eq":
        what = "compares its objects field by field"
        risk = f"Comparing two {name} objects can then read a relationship"
        keep = f"To compare {name} by its fields, pass eq=True on {target}."
    else:
        what = "gets a dataclass repr"
        risk = f"The repr of a {name} object can then read a relationship or a column"
        keep = f"For the dataclass repr, pass repr=True on {target}."
    raise RestlyConfigurationError(
        f"{name} {what}, because its dataclass base {mixin.__name__} does not "
        f"set {option}=False. A dataclass mixin gets Python's default "
        f"{option}=True, and {base.__name__} does not change that. {risk} that "
        f"is not loaded. In async code, that raises MissingGreenlet. Declare "
        f"the mixin with the options of fr.IDMixin: class {mixin.__name__}("
        f"MappedAsDataclass, kw_only=True, eq=False, repr=False). {keep}"
    )


def _written_by_hand(cls: type, method: str) -> bool:
    """Whether ``cls`` wrote ``method`` itself. The dataclass decorator keeps
    such a method, also when the option is True, and it builds the methods
    that it adds from a string of code."""
    func = unwrap(cls.__dict__[method])
    code = getattr(func, "__code__", None)
    return code is not None and code.co_filename != "<string>"


def _left_to_python(cls: type, option: str) -> bool:
    """Whether ``cls`` itself is a dataclass with ``option`` True only because
    no class passed it, so Python's default applies.

    SQLAlchemy keeps the options that it applied in
    ``_sa_apply_dc_transforms``, with ``NO_ARG`` for an option that is left
    out. This is the same in SQLAlchemy 2.0 and 2.1.
    """
    params = cls.__dict__.get("__dataclass_params__")
    applied = cls.__dict__.get("_sa_apply_dc_transforms", {})
    return (
        params is not None
        and getattr(params, option)
        and applied.get(option) is not True
    )


def utc_now() -> datetime:
    """Replacement for the deprecated datetime.utcnow()"""
    return datetime.now(timezone.utc)


class TimestampsMixin(MappedAsDataclass, kw_only=True, eq=False, repr=False):
    """
    Dataclass mixin adding UTC-aware created_at and updated_at timestamps.

    Made for models on a base with :class:`ModelDefaults`. On another base,
    SQLAlchemy makes each model that uses it a dataclass.
    """

    #: When the row was created, in UTC. The model sets it to the current
    #: time when you create the object. The database sets it when SQL that
    #: leaves it out inserts a row.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default_factory=utc_now, server_default=func.now()
    )
    #: When the row last changed, in UTC. The model sets it when you create
    #: the object, and again in each UPDATE that SQLAlchemy runs for the row.
    #: The database sets it when SQL that leaves it out inserts a row.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )


class IDMixin(MappedAsDataclass, kw_only=True, eq=False, repr=False):
    """Dataclass mixin adding an auto-incrementing integer `id` primary key.

    Made for models on a base with :class:`ModelDefaults`. On another base,
    SQLAlchemy makes each model that uses it a dataclass.
    """

    #: The primary key: an integer that the database generates when it
    #: inserts the row. The constructor does not take it, and it is ``None``
    #: until the session flushes the new object.
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
    def __new__(
        mcls, name: str, bases: tuple[type, ...], namespace: dict[str, Any], **kw: Any
    ) -> "_KwOnlyDataclassMeta":
        cls = super().__new__(mcls, name, bases, namespace, **kw)
        base = _declarative_base(cls)
        if base is not None:
            for option in ("eq", "repr"):
                # The class body, not cls.__dict__: with the bases in the order
                # (DeclarativeBase, fr.ModelDefaults), SQLAlchemy makes the
                # class a dataclass before ModelDefaults.__init_subclass__ runs.
                if f"__{option}__" not in namespace:
                    _reject_dataclass_method_from_a_mixin(cls, base, option)
        return cls


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
    repr=False,
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
    * A repr that shows the loaded columns and never loads anything, so it
      works in async code. Pass ``repr=True`` on a model for the dataclass
      repr, which reads every field.
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

    For the same reason, a dataclass mixin of your own declares the options
    of ``fr.IDMixin``: ``class Stamped(MappedAsDataclass, kw_only=True,
    eq=False, repr=False)``. Without ``eq=False``, a model with the mixin
    compares fields, and comparing two objects can load a relationship.
    Without ``repr=False``, the model gets a dataclass repr instead of this
    one, and that repr can load a relationship too. Such a model raises
    :class:`~fastapi_restly.exc.RestlyConfigurationError`, unless it passes
    ``eq=True`` or ``repr=True`` itself, or writes its own ``__eq__`` or
    ``__repr__``.
    """

    def __repr__(self) -> str:
        """Show the primary key and the loaded columns, without loading.

        A dataclass repr reads every field. In async code that raises
        MissingGreenlet for a relationship or a column that is not loaded.
        This repr reads only what the object holds: ``Post(id=1, title='Hi')``.
        ``...`` stands for the columns that are not loaded, and relationships
        are left out.
        """
        state = inspect(self, raiseerr=False)
        if state is None:  # SQLAlchemy has not set up the object yet
            return object.__repr__(self)
        mapper = state.mapper
        primary = [mapper.get_property_by_column(c).key for c in mapper.primary_key]
        values = {**dict(zip(primary, state.identity or ())), **state.dict}
        keys = primary + [a.key for a in mapper.column_attrs if a.key not in primary]
        shown = [f"{key}={values[key]!r}" for key in keys if key in values]
        if len(shown) < len(keys):
            shown.append("...")
        return f"{type(self).__name__}({', '.join(shown)})"

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


# Removed before 1.0. An old name gets this advice, not a bare AttributeError.
_REMOVED_BASES = {
    name: (
        f"fr.{name} was removed: Restly no longer owns a declarative base. "
        "Declare one for your app, class Base(fr.ModelDefaults, "
        "DeclarativeBase): pass, and use it in every model, as in "
        "class Post(fr.IDMixin, Base)."
    )
    for name in ("DataclassBase", "IDBase")
}
