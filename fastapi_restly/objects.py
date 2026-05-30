from typing import TypeVar as _TypeVar

import pydantic as _pydantic
from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
from sqlalchemy.orm import DeclarativeBase as _DeclarativeBase
from sqlalchemy.orm import Session as _Session

from .schemas._base import (
    _async_resolve_ids_to_sqlalchemy_objects,
    _resolve_ids_to_sqlalchemy_objects,
)

_T = _TypeVar("_T", bound=_DeclarativeBase)


def make_new_object(
    session: _Session,
    model_cls: type[_T],
    schema_obj: _pydantic.BaseModel,
    schema_cls: type[_pydantic.BaseModel] | None = None,
) -> _T:
    """Build ``model_cls`` from ``schema_obj`` and add it to ``session``.

    This is the schema-to-ORM mapping primitive. It resolves Restly reference
    fields, skips read-only inputs, applies schema defaults, and stages the
    object in the session. It does not flush and does not run a view's
    ``create`` business logic.
    """
    from .views._base import (
        apply_create_assignments,
        build_create_plan,
        validate_resolved_reference_consistency,
    )

    _resolve_ids_to_sqlalchemy_objects(session, schema_obj)
    validate_resolved_reference_consistency(model_cls, schema_obj, schema_cls)
    create_plan = build_create_plan(model_cls, schema_obj, schema_cls)
    obj = model_cls(**create_plan.kwargs)
    apply_create_assignments(obj, create_plan.post_assignments)
    session.add(obj)
    return obj


def update_object(
    session: _Session,
    obj: _T,
    schema_obj: _pydantic.BaseModel,
    schema_cls: type[_pydantic.BaseModel] | None = None,
) -> _T:
    """Apply writable fields from ``schema_obj`` to ``obj``.

    This is the schema-to-ORM update primitive. It resolves Restly reference
    fields and applies only writable inputs. It does not flush and does not run
    a view's ``update`` business logic.
    """
    from .views._base import (
        apply_update_to_object,
        validate_resolved_reference_consistency,
    )

    _resolve_ids_to_sqlalchemy_objects(session, schema_obj)
    validate_resolved_reference_consistency(type(obj), schema_obj, schema_cls)
    apply_update_to_object(obj, schema_obj, schema_cls)
    return obj


def save_object(session: _Session, obj: _T) -> _T:
    """Flush the session and refresh ``obj`` from the database."""
    session.flush()
    session.refresh(obj)
    return obj


def delete_object(session: _Session, obj: _DeclarativeBase) -> None:
    """Delete ``obj`` and flush the session."""
    session.delete(obj)
    session.flush()


async def async_make_new_object(
    session: _AsyncSession,
    model_cls: type[_T],
    schema_obj: _pydantic.BaseModel,
    schema_cls: type[_pydantic.BaseModel] | None = None,
) -> _T:
    """Async equivalent of :func:`make_new_object`."""
    from .views._base import (
        apply_create_assignments,
        build_create_plan,
        validate_resolved_reference_consistency,
    )

    await _async_resolve_ids_to_sqlalchemy_objects(session, schema_obj)
    validate_resolved_reference_consistency(model_cls, schema_obj, schema_cls)
    create_plan = build_create_plan(model_cls, schema_obj, schema_cls)
    obj = model_cls(**create_plan.kwargs)
    apply_create_assignments(obj, create_plan.post_assignments)
    session.add(obj)
    return obj


async def async_update_object(
    session: _AsyncSession,
    obj: _T,
    schema_obj: _pydantic.BaseModel,
    schema_cls: type[_pydantic.BaseModel] | None = None,
) -> _T:
    """Async equivalent of :func:`update_object`."""
    from .views._base import (
        apply_update_to_object,
        validate_resolved_reference_consistency,
    )

    await _async_resolve_ids_to_sqlalchemy_objects(session, schema_obj)
    validate_resolved_reference_consistency(type(obj), schema_obj, schema_cls)
    apply_update_to_object(obj, schema_obj, schema_cls)
    return obj


async def async_save_object(session: _AsyncSession, obj: _T) -> _T:
    """Async equivalent of :func:`save_object`."""
    await session.flush()
    await session.refresh(obj)
    return obj


async def async_delete_object(
    session: _AsyncSession, obj: _DeclarativeBase
) -> None:
    """Async equivalent of :func:`delete_object`."""
    await session.delete(obj)
    await session.flush()


__all__ = [
    "async_delete_object",
    "async_make_new_object",
    "async_save_object",
    "async_update_object",
    "delete_object",
    "make_new_object",
    "save_object",
    "update_object",
]
