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

from collections.abc import Callable
from datetime import datetime
from typing import Any

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


class TenantCriteria(orm.UserDefinedOption):
    """Marks a statement that carries the tenant criteria.

    A relationship load inherits the marker together with the criteria, so
    the listener adds them to a statement once.
    """

    propagate_to_loaders = True


TenantCriterion = Callable[[Any], sa.ColumnElement[bool]]
_tenant_criteria: list[tuple[type, TenantCriterion]] = []


def restrict_to_tenant(entity: type, criterion: TenantCriterion) -> None:
    """Restrict ORM reads of ``entity`` to the rows ``criterion`` selects.

    ``criterion`` receives the class, or an alias of it. Pass a lambda:
    SQLAlchemy caches it, and rebuilds a plain expression for every statement.
    A ``.has()`` inside it names the tenant itself, because loader criteria
    do not reach into an ``EXISTS``.
    """
    # Stored, not built: building the option calls the lambda, and a
    # relationship in it needs every model imported.
    _tenant_criteria.append((entity, criterion))


restrict_to_tenant(
    TenantOwned,
    lambda cls: sa.or_(tenant_is_admin, cls.organization_id == tenant_org_id),
)


@sa.event.listens_for(orm.Session, "do_orm_execute")
def _restrict_tenant_rows(state: orm.ORMExecuteState) -> None:
    # Include public roots such as Organization: they can load protected rows
    # through relationships. Include relationship loads: one whose parent was
    # inserted, not selected, inherits no options.
    if not state.is_select or state.is_column_load:
        return
    # A load that inherited the options would stack another copy of each.
    if any(isinstance(o, TenantCriteria) for o in state.user_defined_options):
        return
    state.statement = state.statement.options(
        TenantCriteria(),
        *(
            orm.with_loader_criteria(entity, criterion, include_aliases=True)
            for entity, criterion in _tenant_criteria
        ),
    )


class AuditStamped(orm.MappedAsDataclass, kw_only=True):
    """Who created and last updated the row, from ``Current.user_id``.

    ``created_by_id`` is set on insert; ``updated_by_id`` on insert and on
    every update that changes the row. Every row written through the API
    has both. They are null in two cases: the first admin is seeded by a
    migration and has no creator, and a row outlives its user.

    ``ON DELETE SET NULL`` is that second case. A stamp records who acted
    and does not own the row. No relationship orders a user against the rows
    they stamped, so SQLAlchemy may delete the user first when an
    organization goes, and a plain foreign key rejects that.
    """

    created_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"),
        init=False,
        insert_default=Current.user_id,
    )
    updated_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"),
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
