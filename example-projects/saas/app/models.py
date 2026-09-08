"""Model mixins: the structural columns and the context they stamp from.

A structural field is declared once, here, and both halves hang off that
declaration: the read filter through the model namespace's
``default_scope`` (``TenantClauses`` in ``app.context`` derives the
tenant clause from the ``organization_id`` column), and the write stamp
through the column's insert default, or for the tenant an ``init``
listener. The stamps read ``Current`` at construction or flush time, so
they cover every write path: the view verbs, a custom route that builds
the object by hand, the free ``fr.objects`` helpers, and the bulk routes.
Nothing on the view side stamps anything.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import ForeignKey, orm

from .context import Current


class TenantOwned(orm.MappedAsDataclass, kw_only=True):
    """The tenant column, stamped from context at construction.

    With an organization in the request context, every new row is stamped
    with it, whatever the caller passed; without one (an admin, or no
    auth), the value the caller set stands. ``TenantClauses`` is the read
    half and derives ``owned_by_tenant`` from this column.
    """

    organization_id: orm.Mapped[int] = orm.mapped_column(ForeignKey("organization.id"))


@sa.event.listens_for(TenantOwned, "init", propagate=True)
def _stamp_tenant(target: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    # At construction rather than at flush, so a verb that reads the value
    # before saving (the slug probe) already sees the stamp.
    org_id = Current.org_id()
    if org_id is not None:
        kwargs["organization_id"] = org_id


class AuditStamped(orm.MappedAsDataclass, kw_only=True):
    """Who created and last updated the row, from ``Current.user_id``.

    ``created_by_id`` is set on insert unless the caller set it;
    ``updated_by_id`` on insert and on every update that changes the row.
    Both are ``fr.ReadOnly`` on the schemas, so no payload value competes.
    """

    created_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"), default=None, insert_default=lambda: Current.user_id()
    )
    updated_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"),
        default=None,
        insert_default=lambda: Current.user_id(),
        onupdate=lambda: Current.user_id(),
    )


class SoftDeletable(orm.MappedAsDataclass, kw_only=True):
    """The soft-delete column.

    ``SoftDeleteMixin`` (``app.views``) flips it in ``delete``; the model
    namespace's ``default_scope`` hides flipped rows.
    """

    deleted_at: orm.Mapped[datetime | None] = orm.mapped_column(default=None)
