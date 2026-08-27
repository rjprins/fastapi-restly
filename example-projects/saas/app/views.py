"""Application-wide view foundation for the SaaS example.

``TenantBase`` provides shared auth-context dependencies, the tenant floor
on reads, and transactional outbox emission. Read-side visibility lives
next to each model: its namespace (in the subject's ``models.py``)
declares the soft-delete rule as ``default_scope``, and ``TenantClauses``
from ``app.context`` puts the tenant clause under it; a view that should
see something else declares its own ``scope``, and a route names one per
read, with the floor holding underneath either. ``TenantScopedMixin``,
``SoftDeleteMixin``, and ``AuditStampedMixin`` add the write-side behavior
through cooperative ``super()`` chains: ``make_new_object`` /
``update_object`` stamps, and soft deletion via ``delete_object``.

The mixins run before ``save_object``, only stamp data, and compose linearly
so combinations work without ordering surprises. Concrete subject views
import the foundation and the mixins from this root module; the context
values and the scope factories live in ``app.context``.

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
from typing import TYPE_CHECKING, Any, ClassVar

import fastapi
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

import fastapi_restly as fr

from .context import Current, bind_request_context, tenant_rule

# Module level, not inside _emit(): Alembic reaches models through this graph.
from .outbox import OutboxEvent


def check_api_key(request: fastapi.Request) -> None:
    """Placeholder auth check.

    In production, validate a JWT or API key from the Authorization header.
    Raise ``fastapi.HTTPException(401)`` if the token is missing or invalid.
    This dependency runs before every route on every TenantBase subclass.
    """
    pass  # Always passes in this example; replace with real auth logic


class TenantBase(fr.AsyncRestView):
    """Base view wired with auth and audit logging for every concrete view.

    Subclasses inherit:
    - Router-level ``check_api_key`` dependency on every route
    - ``bind_request_context``, so ``Current`` reads work in every route
    - ``apply_scope`` that stacks the model's tenant rule under every read
    - ``save_object`` that calls through to super() then logs the write
    """

    # Applied to every route registered by this view and all subclasses.
    dependencies: ClassVar[list[Any]] = [
        fastapi.Depends(check_api_key),
        bind_request_context,
    ]

    def apply_scope(
        self, query: sa.Select[Any], scope: fr.Clause | fr.clauses.Unscoped
    ) -> sa.Select[Any]:
        """Stack the model's tenant rule under whatever scope the read named.

        The view half of the application floor (``TenantClauses`` is the
        reference-check half): a view that declared its own ``scope`` and
        a route that named one per read stay tenant-bound without saying
        so. A default read carries the rule twice, from the namespace and
        from here; the database does not mind.
        """
        floor = tenant_rule(self.model)
        if floor is None:
            return fr.apply_clauses(query, scope)
        return fr.apply_clauses(query, floor, scope)

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

    The read-side counterpart is the ``owned_by_tenant`` clause
    ``TenantClauses`` gives the model's namespace, applied through its
    ``default_scope`` and under every read by ``TenantBase.apply_scope``;
    both halves read the same ``Current.org_id``.
    """

    async def make_new_object(self, schema_obj: Any) -> Any:
        obj = await super().make_new_object(schema_obj)  # type: ignore[misc]
        # Admins get tenant-stamping when request context provides an org.
        org_id = Current.org_id()
        if org_id is not None and hasattr(obj, "organization_id"):
            obj.organization_id = org_id
        return obj


class SoftDeleteMixin:
    """Set ``deleted_at`` on deletion instead of removing the row.

    The read-side counterpart lives in the model's namespace: the default
    scope hides deleted rows unconditionally, and a trash route on the
    view names ``is_deleted`` as its own scope per read
    (``handle_get_many(query_params, scope=...)``), restore likewise.
    Assumes ``self.model`` has a ``deleted_at`` column.

    Concrete views can still replace the DELETE route when they need a
    different HTTP contract, such as ``200 + body``.
    """

    # Required from the host class.
    if TYPE_CHECKING:
        session: AsyncSession

    async def delete_object(self, obj: Any) -> None:
        if hasattr(obj, "deleted_at"):
            obj.deleted_at = datetime.now(timezone.utc)
            await self.session.flush()
            return
        await super().delete_object(obj)  # type: ignore[misc]


class AuditStampedMixin:
    """Stamp ``created_by_id`` and ``updated_by_id`` from ``Current``.

    Assumes the columns exist on ``self.model``. Stamps before flush in
    ``make_new_object`` and ``update_object``.
    """

    async def make_new_object(self, schema_obj: Any) -> Any:
        obj = await super().make_new_object(schema_obj)  # type: ignore[misc]
        uid = Current.user_id()
        if hasattr(obj, "created_by_id") and obj.created_by_id is None:
            obj.created_by_id = uid
        if hasattr(obj, "updated_by_id"):
            obj.updated_by_id = uid
        return obj

    async def update_object(self, obj: Any, schema_obj: Any) -> Any:
        obj = await super().update_object(obj, schema_obj)  # type: ignore[misc]
        if hasattr(obj, "updated_by_id"):
            obj.updated_by_id = Current.user_id()
        return obj
