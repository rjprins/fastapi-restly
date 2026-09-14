"""Application-wide view foundation for the SaaS example.

``TenantBase`` provides shared auth-context dependencies and transactional
outbox emission. A request without an
authenticated identity does not reach a ``TenantBase`` route (the context
sources answer 401), so every tenant read and write acts as one user in
one organization; the plain views (organizations, countries) take no
identity. Structural fields live on
the models: a subject's ``models.py`` mixes in ``TenantOwned``,
``AuditStamped``, or ``SoftDeletable`` from ``app.models``, which stamp
``organization_id`` and the audit ids from ``Current`` on every write
path; a session listener there restricts every read of a tenant-owned
class to the same organization, so the namespaces declare only the
soft-delete rule as ``default_scope``. A view that should see something
else declares its own ``scope``, and a route names one per read; the
tenant restriction holds underneath either. The one write-side mixin
left here is
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
from sqlalchemy.ext.asyncio import AsyncSession

import fastapi_restly as fr

from .context import bind_request_context

# Module level, not inside _emit(): Alembic reaches models through this graph.
from .outbox import OutboxEvent


def check_api_key(request: fastapi.Request) -> None:
    """Placeholder auth check.

    In production, validate a JWT or API key from the Authorization header
    and set ``request.state.org_id``, ``user_id``, ``user_role`` and
    ``is_admin`` from it; the sources in ``app.context`` read them and
    answer 401 when they are missing. An admin acting in another tenant
    gets that tenant as ``org_id`` (from an act-as header, say). This
    dependency runs before every route on every TenantBase subclass.
    """
    pass  # Always passes in this example; replace with real auth logic


class TenantBase(fr.AsyncRestView):
    """Base view wired with auth and audit logging for every concrete view.

    Subclasses inherit:
    - Router-level ``check_api_key`` dependency on every route
    - ``bind_request_context``, so ``Current`` reads work in every route and
      a request without an identity is a 401
    - ``before_action_commit`` with a placeholder for audit side effects
    """

    # Applied to every route registered by this view and all subclasses.
    dependencies: ClassVar[list[Any]] = [
        fastapi.Depends(check_api_key),
        bind_request_context,
    ]

    async def before_action_commit(
        self, action: str, new: Any, old: Any = None
    ) -> None:
        """Placeholder for an audit row, committed atomically with the write.

        Bulk routes share one commit across their handlers. Their per-row
        savepoints keep this hook's writes atomic with each row.
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
