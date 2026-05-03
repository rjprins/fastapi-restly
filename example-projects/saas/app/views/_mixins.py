"""Cross-cutting mixins for the SaaS example.

These compose by cooperative ``super()`` chains. Application-layer logic
in concrete views (``ProjectView.handle_create`` etc.) doesn't have to know
they exist — the mixins inject their behavior into the right framework
hook (``make_new_object`` / ``update_object`` for *write-side* stamps,
``build_list_query`` / ``handle_get`` for *read-side* filters).

The discussion in ``rut-notes/discussion_save_object.md`` warned against
overriding ``make_new_object`` to layer *application* logic. The mixins
here override it for *structural cross-cutting concerns* (audit, tenant,
soft-delete) — a different rule with the same shape:

- They run *before* ``save_object`` (no flush-timing trap).
- They only stamp/scope; they don't compute business values.
- They compose linearly via ``super()`` so combinations work without
  ordering surprises.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import fastapi
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase


class TenantScopedMixin:
    """Stamp ``organization_id`` from auth on writes; filter reads to it.

    Assumes ``self.model`` has an ``organization_id`` column. Concrete
    views inherit this *before* ``TenantBase`` so ``_current_org_id`` is
    available via the cooperative chain.

    Type stubs below describe what the mixin requires from its host
    class. Since fastapi-restly's class-level DI is now marker-based,
    bare type annotations like these no longer shadow inherited DI
    wiring (``session: AsyncSessionDep`` on ``AsyncRestView``) — they're
    simply pyright-visible documentation of the host's interface.
    """

    # Required from the host class (TenantBase / AsyncRestView).
    # Bare type annotations rather than executable stubs — a real method
    # body would shadow the host's implementation via MRO, even if just
    # ``...``. Pyright is satisfied; runtime sees the inherited method.
    if TYPE_CHECKING:
        request: fastapi.Request
        session: AsyncSession
        model: type[DeclarativeBase]
        def _current_org_id(self) -> int | None: ...
        def _is_admin(self) -> bool: ...

    def build_list_query(self) -> sa.Select:
        q = super().build_list_query()  # type: ignore[misc]
        if self._is_admin():
            return q
        org_id = self._current_org_id()
        if org_id is not None and hasattr(self.model, "organization_id"):
            q = q.where(self.model.organization_id == org_id)
        return q

    async def handle_get(self, id: Any) -> Any:
        obj = await super().handle_get(id)  # type: ignore[misc]
        if self._is_admin():
            return obj
        org_id = self._current_org_id()
        if (
            org_id is not None
            and getattr(obj, "organization_id", org_id) != org_id
        ):
            raise fastapi.HTTPException(404, "Not found")
        return obj

    async def make_new_object(self, schema_obj: Any) -> Any:
        obj = await super().make_new_object(schema_obj)  # type: ignore[misc]
        # Admins still get tenant-stamping by default — they shouldn't be
        # *required* to specify org_id, even though they can see across
        # tenants. If admin's request.state.org_id is unset, the body's
        # value wins (fall-through behavior).
        org_id = self._current_org_id()
        if org_id is not None and hasattr(obj, "organization_id"):
            obj.organization_id = org_id
        return obj


class SoftDeleteMixin:
    """Hide deleted rows from reads; ``delete_object`` sets ``deleted_at``.

    Assumes ``self.model`` has a ``deleted_at: datetime | None`` column.
    Pass ``?include_deleted=true`` on list/get to bypass the filter.

    Concrete views typically also replace the generated DELETE route
    with a ``200 + body`` contract — that's a *route-level* HTTP-contract
    decision (matrix: "Return deleted record instead of 204"), separate
    from the soft-delete behavior the mixin provides.
    """

    # Required from the host class.
    if TYPE_CHECKING:
        request: fastapi.Request
        session: AsyncSession
        model: type[DeclarativeBase]

    def _include_deleted(self) -> bool:
        return (
            self.request.query_params.get("include_deleted", "false").lower()
            == "true"
        )

    def build_list_query(self) -> sa.Select:
        q = super().build_list_query()  # type: ignore[misc]
        if not self._include_deleted() and hasattr(self.model, "deleted_at"):
            q = q.where(self.model.deleted_at.is_(None))
        return q

    async def handle_get(self, id: Any) -> Any:
        obj = await super().handle_get(id)  # type: ignore[misc]
        if (
            not self._include_deleted()
            and getattr(obj, "deleted_at", None) is not None
        ):
            raise fastapi.HTTPException(404, "Not found")
        return obj

    async def delete_object(self, obj: Any) -> None:
        if hasattr(obj, "deleted_at"):
            obj.deleted_at = datetime.now(timezone.utc)
            await self.session.flush()
            return
        await super().delete_object(obj)  # type: ignore[misc]


class AuditStampedMixin:
    """Stamp ``created_by_id`` / ``updated_by_id`` from request state.

    Assumes the columns exist on ``self.model``. Stamps in
    ``make_new_object`` (pre-flush, so the row inserts with the values)
    and ``update_object`` (also pre-flush). No business logic — purely
    "who did this write" book-keeping.
    """

    # Required from the host class.
    if TYPE_CHECKING:
        request: fastapi.Request

    def _current_user_id(self) -> int | None:
        return getattr(self.request.state, "user_id", None)

    async def make_new_object(self, schema_obj: Any) -> Any:
        obj = await super().make_new_object(schema_obj)  # type: ignore[misc]
        uid = self._current_user_id()
        if hasattr(obj, "created_by_id") and obj.created_by_id is None:
            obj.created_by_id = uid
        if hasattr(obj, "updated_by_id"):
            obj.updated_by_id = uid
        return obj

    async def update_object(self, obj: Any, schema_obj: Any) -> Any:
        obj = await super().update_object(obj, schema_obj)  # type: ignore[misc]
        if hasattr(obj, "updated_by_id"):
            obj.updated_by_id = self._current_user_id()
        return obj
