"""Mapped-class checks that ask the SQLAlchemy mapper, not the base class.

A class is mapped without subclassing ``DeclarativeBase`` when it comes from
``registry.mapped``, ``declarative_base()``, an imperative mapping, or SQLModel.
"""

from typing import Any, TypeGuard

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import InstanceState, Mapper
from sqlalchemy.orm.exc import UnmappedClassError


def is_mapped_class(cls: Any) -> TypeGuard[type[Any]]:
    """Whether ``cls`` has a SQLAlchemy mapper, whatever its base class."""
    if not isinstance(cls, type):
        return False
    try:
        return isinstance(sa_inspect(cls, raiseerr=False), Mapper)
    except UnmappedClassError:
        # a deferred mapping (DeferredReflection) is mapped once prepared
        return True


def is_mapped_instance(obj: Any) -> bool:
    """Whether ``obj`` is an instance of a mapped class."""
    return isinstance(sa_inspect(obj, raiseerr=False), InstanceState)


def is_to_many_relationship(cls: type[Any], name: str) -> bool:
    """Whether ``name`` is a relationship of ``cls`` that holds a collection."""
    try:
        mapper = sa_inspect(cls, raiseerr=False)
    except UnmappedClassError:
        return False
    if not isinstance(mapper, Mapper):
        return False
    relationship = mapper.relationships.get(name)
    return relationship is not None and relationship.uselist
