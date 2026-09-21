"""Model mixins: the structural columns and the context they stamp from.

A structural field is declared once, here, and both halves hang off that
declaration: the write stamp through the column's insert default, and the
read filter through a ``do_orm_execute`` listener that adds the tenant
predicate to ORM entity and relationship loads, with
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

from .current import Current


class TenantOwned(orm.MappedAsDataclass, kw_only=True):
    """The tenant column: the organization the request acts in.

    Never ``None`` and never chosen by the caller. A tenant's user writes
    into their own organization; an admin writes into another by acting
    as it. The listener restricts reads of subclasses to ``Current.org_id()``,
    unless ``Current.is_admin()`` is true.
    """

    organization_id: orm.Mapped[int] = orm.mapped_column(
        ForeignKey("organization.id"),
        init=False,
        insert_default=lambda: Current.org_id(),
    )


# SQL parameters read identity only when a tenant criterion occurs in the SQL.
# Public queries still receive loader criteria, but need no bound identity.
# bindparam tests its callable's truth value, which ContextParam rejects.
tenant_org_id = sa.bindparam(
    "tenant_org_id", callable_=lambda: Current.org_id(), type_=sa.Integer
)
tenant_is_admin = sa.bindparam(
    "tenant_is_admin", callable_=lambda: Current.is_admin(), type_=sa.Boolean
)


@sa.event.listens_for(orm.Session, "do_orm_execute")
def _restrict_tenant_rows(state: orm.ORMExecuteState) -> None:
    # Include public roots such as Organization: they can load protected rows
    # through relationships. Apply to relationship queries too, including ones
    # whose parent was inserted rather than loaded by a SELECT.
    if not state.is_select or state.is_column_load:
        return
    state.statement = state.statement.options(
        orm.with_loader_criteria(
            TenantOwned,
            lambda cls: sa.or_(tenant_is_admin, cls.organization_id == tenant_org_id),
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
        ForeignKey("user.id"), init=False, insert_default=Current.user_id
    )
    updated_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"),
        init=False,
        insert_default=Current.user_id,
        onupdate=Current.user_id,
    )


class SoftDeletable(orm.MappedAsDataclass, kw_only=True):
    """The soft-delete column.

    ``SoftDeleteMixin`` (``app.views``) flips it in ``delete``; the model
    namespace's ``default_scope`` hides flipped rows.
    """

    deleted_at: orm.Mapped[datetime | None] = orm.mapped_column(default=None)
