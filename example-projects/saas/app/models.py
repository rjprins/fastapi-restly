"""Model mixins: the structural columns and the context they stamp from.

A structural field is declared once, here, and both halves hang off that
declaration: the write stamp through the column's insert default, and the
read filter through a ``do_orm_execute`` listener that adds the tenant
predicate to every ORM SELECT touching a tenant-owned class, with
SQLAlchemy's ``with_loader_criteria``. A stamp is not a constructor
argument (``init=False``) and not a payload field (``fr.ReadOnly`` on the
schema): the value comes from ``Current`` at flush time, whoever builds
the row, so it covers every write path: the view verbs, a custom route
that builds the object by hand, the free ``fr.objects`` helpers, and the
bulk routes. Nothing on the view side stamps anything. A verb that needs
the value before the flush reads ``Current`` itself, as the project slug
probe does.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import ForeignKey, orm

from .context import Current


class TenantOwned(orm.MappedAsDataclass, kw_only=True):
    """The tenant column: the organization the request acts in.

    Never ``None`` and never chosen by the caller. A tenant's user writes
    into their own organization; an admin writes into another by acting
    as it. The listener below is the read half: every SELECT that touches
    a subclass carries ``organization_id == Current.org_id()``.
    """

    organization_id: orm.Mapped[int] = orm.mapped_column(
        ForeignKey("organization.id"),
        init=False,
        insert_default=lambda: Current.org_id(),
    )


def tenant_org_for(state: orm.ORMExecuteState, *classes: type) -> int | None:
    """The organization a SELECT over ``classes`` is restricted to, or None.

    None when the statement is not a SELECT, is a column or relationship
    load (those inherit the criteria of the statement that loaded the
    parent), touches none of ``classes``, or is an admin's. Reading a
    tenant-owned class outside a bound context raises: system code binds
    an identity with ``Current.bind`` instead of reading unscoped by
    accident.
    """
    if not state.is_select or state.is_column_load or state.is_relationship_load:
        return None
    if not any(issubclass(m.class_, classes) for m in state.all_mappers):
        return None
    if Current.is_admin():
        return None
    return Current.org_id()


@sa.event.listens_for(orm.Session, "do_orm_execute")
def _restrict_tenant_rows(state: orm.ORMExecuteState) -> None:
    # Every ORM SELECT that touches a tenant-owned class, through a view,
    # a reference check, a lazy load or a hand-written select, carries the
    # tenant predicate. Task has no organization_id and declares its own
    # rule in tasks/models.py.
    org_id = tenant_org_for(state, TenantOwned)
    if org_id is not None:
        state.statement = state.statement.options(
            orm.with_loader_criteria(
                TenantOwned,
                lambda cls: cls.organization_id == org_id,
                include_aliases=True,
            )
        )


class AuditStamped(orm.MappedAsDataclass, kw_only=True):
    """Who created and last updated the row, from ``Current.user_id``.

    ``created_by_id`` is set on insert; ``updated_by_id`` on insert and on
    every update that changes the row. Nullable for one row only: the
    first admin is seeded by a migration and has no creator. Every row
    written through the API has one.
    """

    created_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"), init=False, insert_default=lambda: Current.user_id()
    )
    updated_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"),
        init=False,
        insert_default=lambda: Current.user_id(),
        onupdate=lambda: Current.user_id(),
    )


class SoftDeletable(orm.MappedAsDataclass, kw_only=True):
    """The soft-delete column.

    ``SoftDeleteMixin`` (``app.views``) flips it in ``delete``; the model
    namespace's ``default_scope`` hides flipped rows.
    """

    deleted_at: orm.Mapped[datetime | None] = orm.mapped_column(default=None)
