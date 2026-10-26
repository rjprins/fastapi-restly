from typing import Any as _Any
from typing import TypeVar as _TypeVar

import pydantic as _pydantic
from sqlalchemy import inspect as _sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
from sqlalchemy.orm import Session as _Session
from sqlalchemy.orm import object_mapper as _object_mapper

from .schemas._base import (
    _async_check_ref_exists,
    _async_resolve_ids_to_sqlalchemy_objects,
    _check_ref_exists,
    _resolve_ids_to_sqlalchemy_objects,
)
from .schemas._base import get_writable_inputs as _get_writable_inputs

_T = _TypeVar("_T")


def make_new_object(
    session: _Session, model: type[_T], data: _pydantic.BaseModel
) -> _T:
    """Build an object of ``model`` from ``data`` and add it to ``session``.

    This is the schema-to-ORM mapping primitive. It resolves Restly reference
    fields, skips read-only inputs, applies schema defaults, and stages the
    object in the session. A field that the client did not send, and whose
    value is ``None``, is not passed to the constructor, unless the
    constructor requires it. So the model's default applies to it. It does not
    flush, so the object has no id yet. The default ``create`` of a view
    calls this with the view's model.

    The schema of ``data`` decides what is written: every field it
    declares, except those it marks ``ReadOnly``. A time gets the form of its
    column; see :ref:`utc-times`.
    """
    from .views._base import (
        apply_create_assignments,
        build_create_plan,
        validate_resolved_reference_consistency,
    )

    resolved = _resolve_ids_to_sqlalchemy_objects(session, data, model)
    _check_ref_exists(session, data)
    validate_resolved_reference_consistency(model, data, resolved)
    create_plan = build_create_plan(model, data, resolved)
    obj = model(**create_plan.kwargs)
    apply_create_assignments(obj, create_plan)
    session.add(obj)
    return obj


def update_object(session: _Session, obj: _T, data: _pydantic.BaseModel) -> _T:
    """Apply writable fields from ``data`` to ``obj``.

    This is the schema-to-ORM update primitive. It resolves Restly reference
    fields and applies only writable inputs: the fields the client sent that
    the schema of ``data`` does not mark ``ReadOnly``. A time gets the form of
    its column, as in :func:`make_new_object`. It does not flush. The default
    ``update`` of a view calls this.
    """
    from .views._base import (
        apply_update_to_object,
        validate_resolved_reference_consistency,
    )

    resolved = _resolve_ids_to_sqlalchemy_objects(session, data, type(obj))
    _check_ref_exists(session, data)
    validate_resolved_reference_consistency(type(obj), data, resolved)
    unloaded = _unloaded_written_collections(obj, data)
    if unloaded:
        session.refresh(obj, unloaded)
    apply_update_to_object(obj, data, resolved)
    return obj


def _refresh_plan(obj: object) -> tuple[list[str], list[str]]:
    """What ``save_object`` expires and what it reloads, by attribute name.

    Naming the attributes keeps the refresh on ``obj`` itself: a plain
    ``session.refresh(obj)`` runs the ``refresh-expire`` cascade, which
    expires every related object behind a ``cascade="all"`` relationship
    without reloading it, and under ``AsyncSession`` the next attribute
    access on one of those raises ``MissingGreenlet``. The end state of
    ``obj`` is the one a plain refresh leaves: columns reloaded, eager
    relationships reloaded, lazy relationships unloaded (so a changed foreign
    key is never served from a stale relationship), deferred columns
    untouched.
    """
    mapper = _object_mapper(obj)
    columns = [attr.key for attr in mapper.column_attrs if not attr.deferred]
    eager = [
        rel.key
        for rel in mapper.relationships
        if rel.lazy in ("selectin", "joined", "subquery", "immediate")
    ]
    expire = [rel.key for rel in mapper.relationships]
    return expire, columns + eager


def save_object(session: _Session, obj: _T) -> _T:
    """Flush the session and refresh ``obj`` from the database.

    The refresh reloads the columns of ``obj`` and its eagerly loaded
    relationships, and nothing else: it does not reach related objects. Its
    lazy relationships are left unloaded, so a changed foreign key is not
    read from a stale relationship. Inside a view, the commit
    bracket saves the subject of the action and the other rows of the view's
    model that it wrote. Call this in code outside a view, or for a row of
    another model that must be fresh before the commit. It does not load the
    relationships of a response.
    """
    session.flush()
    _refresh_saved(session, obj)
    return obj


def _refresh_saved(session: _Session, obj: object) -> None:
    """The refresh of :func:`save_object`, after its flush."""
    expire, reload = _refresh_plan(obj)
    if expire:
        session.expire(obj, expire)
    session.refresh(obj, reload)


def delete_object(session: _Session, obj: object) -> None:
    """Delete ``obj`` and flush the session."""
    session.delete(obj)
    session.flush()


def snapshot(obj: _Any) -> dict[str, _Any]:
    """A dict of the column values that are loaded on ``obj``, to compare
    old and new values in the commit hooks.

    It is a shallow copy: a mutable value, such as a JSON dict, is shared
    with the object. So a change to such a value in place also shows in the
    snapshot. Assign a new value instead.

    It reads only what is loaded, so it never starts a lazy load. A deferred
    or unloaded column is left out, where a load would raise
    ``MissingGreenlet`` on an async session. It needs no session.
    """
    state = _sa_inspect(obj)
    loaded = state.dict
    return {
        attr.key: loaded[attr.key]
        for attr in state.mapper.column_attrs
        if attr.key in loaded
    }


async def async_make_new_object(
    session: _AsyncSession, model: type[_T], data: _pydantic.BaseModel
) -> _T:
    """Async equivalent of :func:`make_new_object`."""
    from .views._base import (
        apply_create_assignments,
        build_create_plan,
        validate_resolved_reference_consistency,
    )

    resolved = await _async_resolve_ids_to_sqlalchemy_objects(session, data, model)
    await _async_check_ref_exists(session, data)
    validate_resolved_reference_consistency(model, data, resolved)
    create_plan = build_create_plan(model, data, resolved)
    obj = model(**create_plan.kwargs)
    apply_create_assignments(obj, create_plan)
    session.add(obj)
    return obj


async def async_update_object(
    session: _AsyncSession, obj: _T, data: _pydantic.BaseModel
) -> _T:
    """Async equivalent of :func:`update_object`."""
    from .views._base import (
        apply_update_to_object,
        validate_resolved_reference_consistency,
    )

    resolved = await _async_resolve_ids_to_sqlalchemy_objects(session, data, type(obj))
    await _async_check_ref_exists(session, data)
    validate_resolved_reference_consistency(type(obj), data, resolved)
    unloaded = _unloaded_written_collections(obj, data)
    if unloaded:
        await session.refresh(obj, unloaded)
    apply_update_to_object(obj, data, resolved)
    return obj


def _unloaded_written_collections(obj: object, data: _pydantic.BaseModel) -> list[str]:
    """The collections that ``data`` writes and that are not loaded on
    ``obj``. The update loads them first.

    To replace a collection, SQLAlchemy first reads the old one. On an async
    session it cannot read inside the assignment and fails with
    ``MissingGreenlet``, and a relationship with ``lazy="raise"`` refuses
    the read on both. A scalar relationship needs no load: SQLAlchemy sets
    it without the old value, or reads that value at flush. Only
    ``active_history`` makes it read the old value at once.

    The view loads only the relationships of its response class, and an
    update can write others: a ``WriteOnly`` field, or a field of a
    ``schema_update`` that the response class does not have. The update
    loads them with ``session.refresh``, which also loads a relationship with
    ``lazy="raise"``.
    """
    state = _sa_inspect(obj)
    if not state.persistent:
        return []
    relationships = state.mapper.relationships
    unloaded = state.unloaded
    return [
        name
        for name in _get_writable_inputs(data)
        if name in relationships
        and name in unloaded
        and (relationships[name].uselist or relationships[name].active_history)
    ]


async def async_save_object(session: _AsyncSession, obj: _T) -> _T:
    """Async equivalent of :func:`save_object`."""
    await session.flush()
    await _async_refresh_saved(session, obj)
    return obj


async def _async_refresh_saved(session: _AsyncSession, obj: object) -> None:
    """Async equivalent of :func:`_refresh_saved`."""
    expire, reload = _refresh_plan(obj)
    if expire:
        session.expire(obj, expire)
    await session.refresh(obj, reload)


async def async_delete_object(session: _AsyncSession, obj: object) -> None:
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
    "snapshot",
    "update_object",
]
