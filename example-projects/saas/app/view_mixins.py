"""Cross-cutting mixins for the SaaS example.

These compose through cooperative ``super()`` chains. Write-side stamps use
``make_new_object`` / ``update_object``; read-side filters use ``build_query``;
soft-delete uses ``delete_object``.

- They run *before* ``save_object`` (no flush-timing issue).
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

    Type stubs below describe what the mixin expects from its host class.
    """

    # Required from the host class (TenantBase / AsyncRestView).
    # Keep stubs under TYPE_CHECKING so runtime MRO uses the host implementation.
    if TYPE_CHECKING:
        request: fastapi.Request
        session: AsyncSession
        model: type[DeclarativeBase]

        def _current_org_id(self) -> int | None: ...
        def _is_admin(self) -> bool: ...

    def build_query(self) -> sa.Select:
        # Filters listing, count, and retrieve through one read hook.
        q = super().build_query()  # type: ignore[misc]
        if self._is_admin():
            return q
        org_id = self._current_org_id()
        if org_id is not None and hasattr(self.model, "organization_id"):
            q = q.where(self.model.organization_id == org_id)
        return q

    async def make_new_object(self, schema_obj: Any) -> Any:
        obj = await super().make_new_object(schema_obj)  # type: ignore[misc]
        # Admins get tenant-stamping when request context provides an org.
        org_id = self._current_org_id()
        if org_id is not None and hasattr(obj, "organization_id"):
            obj.organization_id = org_id
        return obj


class SoftDeleteMixin:
    """Hide deleted rows from reads; ``delete_object`` sets ``deleted_at``.

    Assumes ``self.model`` has a ``deleted_at: datetime | None`` column.
    Pass ``?include_deleted=true`` on list/get to bypass the filter.

    Concrete views can still replace the DELETE route when they need a
    different HTTP contract, such as ``200 + body``.
    """

    # Required from the host class.
    if TYPE_CHECKING:
        request: fastapi.Request
        session: AsyncSession
        model: type[DeclarativeBase]

    # Allow ``?include_deleted=true`` through the listing endpoint's
    # unknown-query-param guard.
    extra_query_params = ("include_deleted",)

    def _include_deleted(self) -> bool:
        return (
            self.request.query_params.get("include_deleted", "false").lower() == "true"
        )

    def build_query(self) -> sa.Select:
        q = super().build_query()  # type: ignore[misc]
        if not self._include_deleted() and hasattr(self.model, "deleted_at"):
            q = q.where(self.model.deleted_at.is_(None))
        return q

    async def delete_object(self, obj: Any) -> None:
        if hasattr(obj, "deleted_at"):
            obj.deleted_at = datetime.now(timezone.utc)
            await self.session.flush()
            return
        await super().delete_object(obj)  # type: ignore[misc]


class AuditStampedMixin:
    """Stamp ``created_by_id`` / ``updated_by_id`` from request state.

    Assumes the columns exist on ``self.model``. Stamps before flush in
    ``make_new_object`` and ``update_object``.
    """

    # Required from the host class.
    if TYPE_CHECKING:
        request: fastapi.Request
        current_user_id: int | None

    def _current_user_id(self) -> int | None:
        return self.current_user_id

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
