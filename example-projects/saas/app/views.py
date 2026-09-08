"""Application-wide view foundation for the SaaS example.

``TenantBase`` provides shared auth-context dependencies, the tenant floor
on reads, and transactional outbox emission. Structural fields live on
the models: a subject's ``models.py`` mixes in ``TenantOwned``,
``AuditStamped``, or ``SoftDeletable`` from ``app.models``, which stamp
``organization_id`` and the audit ids from ``Current`` on every write
path, and its namespace declares the soft-delete rule as
``default_scope`` with ``TenantClauses`` from ``app.context`` putting the
tenant clause under it. A view that should see something else declares
its own ``scope``, and a route names one per read, with the floor holding
underneath either. The one write-side mixin left here is
``SoftDeleteMixin``: ``delete`` flips ``deleted_at`` instead of removing
the row. Concrete subject views import the foundation and the mixin from
this root module; the context values and the scope factories live in
``app.context``.

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

from .context import bind_request_context, tenant_rule

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
    - ``before_action_commit`` with a placeholder for audit side effects
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

    async def before_action_commit(
        self, action: str, new: Any, old: Any = None
    ) -> None:
        """Placeholder for an audit row, committed atomically with the write.

        The bulk routes own their commit and skip this bracket; an audit
        that must see every flush is a session ``after_flush`` listener.
        """
        # In production: self.session.add(AuditRow(action=action, ...)), or
        # publish to an event bus from after_action_commit once durable.

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


class SoftDeleteMixin:
    """Set ``deleted_at`` on deletion instead of removing the row.

    The read-side counterpart lives in the model's namespace: the default
    scope hides deleted rows unconditionally, and a trash route on the
    view names ``is_deleted`` as its own scope per read
    (``handle_get_many(query_params, scope=...)``), restore likewise.
    For views of a ``SoftDeletable`` model (``app.models``): the column is
    there because the model said so, so there is nothing to guard.

    Concrete views can still replace the DELETE route when they need a
    different HTTP contract, such as ``200 + body``.
    """

    # Required from the host class.
    if TYPE_CHECKING:
        session: AsyncSession

    async def delete(self, obj: Any) -> None:
        obj.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
