"""Model mixins: the structural columns and the context they stamp from.

A structural field is declared once, here, and both halves hang off that
declaration: the read filter through the model namespace's
``default_scope`` (``TenantClauses`` in ``app.context`` derives the
tenant clause from the ``organization_id`` column), and the write stamp
through the column's insert default. A stamp is not a constructor
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

from sqlalchemy import ForeignKey, orm

from .context import Current


class TenantOwned(orm.MappedAsDataclass, kw_only=True):
    """The tenant column: the organization the request acts in.

    Never ``None`` and never chosen by the caller. A tenant's user writes
    into their own organization; an admin writes into another by acting
    as it. ``TenantClauses`` is the read half and derives
    ``owned_by_tenant`` from this column.
    """

    organization_id: orm.Mapped[int] = orm.mapped_column(
        ForeignKey("organization.id"),
        init=False,
        insert_default=lambda: Current.org_id(),
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
