"""Application-wide view foundation for the SaaS example.

``TenantBase`` provides shared auth-context dependencies, tenant helpers, and
transactional outbox emission. Read-side visibility is declared as scope
clauses: ``tenant_scope(Model)`` and ``soft_delete_scope(Model)`` build the
predicates, ``bind_request_context`` broadcasts the per-request values they
read (org, user, admin flag, the ``include_deleted`` toggle), and each view
declares its composition as ``scope = ...``. ``TenantScopedMixin``,
``SoftDeleteMixin``, and ``AuditStampedMixin`` add the write-side behavior
through cooperative ``super()`` chains: ``make_new_object`` /
``update_object`` stamps, and soft deletion via ``delete_object``.

The mixins run before ``save_object``, only stamp data, and compose linearly
so combinations work without ordering surprises. Concrete subject views
import the foundation, the scope factories, and the mixins from this root
module.

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

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Annotated, Any, ClassVar

import fastapi
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

import fastapi_restly as fr

# Module level, not inside _emit(): Alembic reaches models through this graph.
from .outbox import OutboxEvent


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


def get_is_admin(request: fastapi.Request) -> bool:
    """Whether this request bypasses tenant and row scoping."""
    return bool(getattr(request.state, "is_admin", False))


class Current(fr.ContextNamespace):
    """Per-request context the scope clauses read.

    The bind dependency below broadcasts these once per request; the
    clauses branch on them. Member names are the bind names.
    """

    org_id: fr.ContextParam[int | None]
    user_id: fr.ContextParam[int | None]
    is_admin: fr.ContextParam[bool]
    include_deleted: fr.ContextParam[bool]


async def bind_request_context(
    request: fastapi.Request,
    org_id: Annotated[int | None, fastapi.Depends(get_current_org_id)],
    user_id: Annotated[int | None, fastapi.Depends(get_current_user_id)],
    is_admin: Annotated[bool, fastapi.Depends(get_is_admin)],
) -> AsyncIterator[None]:
    """Bind auth and request context for the scope clauses.

    Runs on every TenantBase route (see ``TenantBase.dependencies``). The
    sources are the auth dependencies themselves, so
    ``app.dependency_overrides`` keeps working in tests. Must be an
    ``async def`` generator: the bind has to land in the request's task.
    """
    include_deleted = (
        request.query_params.get("include_deleted", "false").lower() == "true"
    )
    with Current.bind(
        org_id=org_id,
        user_id=user_id,
        is_admin=is_admin,
        include_deleted=include_deleted,
    ):
        yield


def tenant_scope(model: type[Any]) -> fr.WhereClause:
    """Rows of ``model`` owned by the authenticated organization.

    Admin requests, and requests without an org in context, see every row.
    ``model`` must carry an ``organization_id`` column.
    """

    @fr.where_clause
    def owned_by_tenant(
        org_id: Annotated[int | None, Current.org_id],
        admin: Annotated[bool, Current.is_admin],
    ) -> sa.ColumnElement[bool]:
        if admin or org_id is None:
            return sa.true()
        return model.organization_id == org_id

    return owned_by_tenant


def soft_delete_scope(model: type[Any]) -> fr.WhereClause:
    """Rows of ``model`` not soft-deleted, unless ``?include_deleted=true``.

    Pair with ``SoftDeleteMixin`` (which sets ``deleted_at`` on delete) and
    keep ``include_deleted`` in ``extra_query_params`` so the listing
    endpoint accepts the toggle.
    """

    @fr.where_clause
    def not_deleted(
        include_deleted: Annotated[bool, Current.include_deleted],
    ) -> sa.ColumnElement[bool]:
        return sa.true() if include_deleted else model.deleted_at.is_(None)

    return not_deleted


class TenantBase(fr.AsyncRestView):
    """Base view wired with auth and audit logging for every concrete view.

    Subclasses inherit:
    - Router-level ``check_api_key`` dependency on every route
    - ``save_object`` that calls through to super() then logs the write
    - FastAPI dependencies for current user/org context
    - ``_current_org_id()`` helper for tenant-scoped filtering
    """

    # Applied to every route registered by this view and all subclasses.
    dependencies: ClassVar[list[Any]] = [
        fastapi.Depends(check_api_key),
        fastapi.Depends(bind_request_context),
    ]
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
        self.session.add(
            OutboxEvent(
                event_type=event_type,
                aggregate_type=type(aggregate).__name__,
                aggregate_id=getattr(aggregate, "id", 0) or 0,
                payload=payload or {},
            )
        )


class TenantScopedMixin:
    """Stamp ``organization_id`` from auth context on writes.

    The read-side counterpart is ``tenant_scope(Model)``, declared on the
    view's ``scope``. Concrete views inherit this before ``TenantBase`` so
    ``_current_org_id`` is available via the cooperative chain.

    Type stubs below describe what the mixin expects from its host class.
    """

    # Required from the host class (TenantBase / AsyncRestView).
    # Keep stubs under TYPE_CHECKING so runtime MRO uses the host implementation.
    if TYPE_CHECKING:
        request: fastapi.Request
        session: AsyncSession
        model: type[DeclarativeBase]

        def _current_org_id(self) -> int | None: ...

    async def make_new_object(self, schema_obj: Any) -> Any:
        obj = await super().make_new_object(schema_obj)  # type: ignore[misc]
        # Admins get tenant-stamping when request context provides an org.
        org_id = self._current_org_id()
        if org_id is not None and hasattr(obj, "organization_id"):
            obj.organization_id = org_id
        return obj


class SoftDeleteMixin:
    """Set ``deleted_at`` on deletion instead of removing the row.

    The read-side counterpart is ``soft_delete_scope(Model)``, declared on
    the view's ``scope``; ``?include_deleted=true`` on list/get bypasses
    that filter. Assumes ``self.model`` has a ``deleted_at`` column.

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
