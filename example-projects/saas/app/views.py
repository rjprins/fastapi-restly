"""Application-wide view foundation for the SaaS example.

``TenantBase`` provides shared auth-context dependencies, tenant helpers, and
transactional outbox emission. ``TenantScopedMixin``, ``SoftDeleteMixin``, and
``AuditStampedMixin`` add reusable behavior through cooperative ``super()``
chains. Write-side stamps use ``make_new_object`` and ``update_object``.
Read-side filters use ``build_query``, while soft deletion uses
``delete_object``.

The mixins run before ``save_object``, only stamp or scope data, and compose
linearly so combinations work without ordering surprises. Concrete subject
views import the foundation and mixins from this root module.

Inheritance and prefix concatenation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
All concrete views inherit from TenantBase instead of AsyncRestView directly.
Prefixes from each class in the MRO are concatenated, so adding a version
prefix to TenantBase (e.g. ``prefix = "/api/v1"``) would automatically
update every route::

    class TenantBase(fr.AsyncRestView):
        prefix = "/api/v1"          # shared namespace

    class ProjectView(TenantBase):
        prefix = "/projects"         # → /api/v1/projects
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Annotated, Any, ClassVar

import fastapi
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

import fastapi_restly as fr


def check_api_key(request: fastapi.Request) -> None:
    """Placeholder auth check.

    In production, validate a JWT or API key from the Authorization header.
    Raise ``fastapi.HTTPException(401)`` if the token is missing or invalid.
    This dependency runs before every route on every TenantBase subclass.
    """
    pass  # Always passes in this example; replace with real auth logic


def get_current_org_id(request: fastapi.Request) -> int | None:
    """Return the authenticated tenant ID set by auth middleware."""
    return getattr(request.state, "org_id", None)


def get_current_user_id(request: fastapi.Request) -> int | None:
    """Return the authenticated user ID set by auth middleware."""
    return getattr(request.state, "user_id", None)


class TenantBase(fr.AsyncRestView):
    """Base view wired with auth and audit logging for every concrete view.

    Subclasses inherit:
    - Router-level ``check_api_key`` dependency on every route
    - ``save_object`` that calls through to super() then logs the write
    - FastAPI dependencies for current user/org context
    - ``_current_org_id()`` helper for tenant-scoped filtering
    """

    # Applied to every route registered by this view and all subclasses.
    dependencies: ClassVar[list[Any]] = [fastapi.Depends(check_api_key)]
    current_org_id: Annotated[int | None, fastapi.Depends(get_current_org_id)]
    current_user_id: Annotated[int | None, fastapi.Depends(get_current_user_id)]

    def _current_org_id(self) -> int | None:
        """Return the current tenant's org ID.

        In production: set by auth middleware via ``request.state.org_id``.
        In tests: controlled with ``app.dependency_overrides``.
        Returns ``None`` when neither is set (all rows visible, no scoping).
        """
        return self.current_org_id

    def _current_user_id(self) -> int | None:
        """Return the current authenticated user ID."""
        return self.current_user_id

    def _is_admin(self) -> bool:
        """Whether this request bypasses tenant and row scoping."""
        return bool(getattr(self.request.state, "is_admin", False))

    async def save_object(self, obj):
        """Flush and refresh, with a placeholder for audit side effects."""
        obj = await super().save_object(obj)
        # In production: publish to an audit log or event bus.
        # await audit_bus.emit("saved", model=type(obj).__name__, id=obj.id)
        return obj

    def _emit(
        self, event_type: str, aggregate: Any, payload: dict[str, Any] | None = None
    ) -> None:
        """Write an outbox row in the current session.

        Call after ``save_object`` so ``aggregate.id`` is populated. The outbox
        row joins the same transaction as the aggregate.

        Do not replace this with a direct ``await email_service.send(...)``
        before commit: if the transaction rolls back, the email still goes out
        and leaks a row that does not exist. The outbox is the durable boundary.
        """
        from .outbox import OutboxEvent

        self.session.add(
            OutboxEvent(
                event_type=event_type,
                aggregate_type=type(aggregate).__name__,
                aggregate_id=getattr(aggregate, "id", 0) or 0,
                payload=payload or {},
            )
        )


class TenantScopedMixin:
    """Stamp ``organization_id`` from auth on writes and filter reads to it.

    Assumes ``self.model`` has an ``organization_id`` column. Concrete
    views inherit this before ``TenantBase`` so ``_current_org_id`` is
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
    """Hide deleted rows from reads and set ``deleted_at`` on deletion.

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
    """Stamp ``created_by_id`` and ``updated_by_id`` from request state.

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
