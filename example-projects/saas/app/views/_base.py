"""Shared base view for the SaaS example.

Demonstrates:
- Class-level ``dependencies`` applied to every route on every subclass
- ``save_object`` override to run a side effect after every write
- Shared auth-context dependencies and helper accessors,
  used by subclasses to scope list/get operations to the current tenant
- ``_emit()`` helper that writes outbox events in the same transaction
  as the business write (use-cases: send email / fire webhook / invalidate
  cache after create/update). The worker that reads the outbox table and
  delivers the side-effect is out of scope for the example.

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

from typing import Annotated, Any, ClassVar

import fastapi

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
        """Whether the current request bypasses tenant + row scoping.

        Admin requests skip the ``WHERE organization_id = ...`` clause in
        ``TenantScopedMixin.build_list_query`` and any per-row scope check on
        the concrete view (see ``TaskView.handle_get`` for an assignee-scope
        example). The mixins consult this predicate cooperatively, so an
        admin request sees all rows across all tenants by simply having
        the flag set — no separate route tree, no second base view.

        Trade-off this surfaces: the bypass is *runtime*, not class-time.
        That keeps the route tree simple but couples every read scope to
        an ``if not self._is_admin():`` guard. The matrix's alternative
        ("admin views opt into a different base query") would mean a
        parallel ``AdminProjectView`` etc. — see commentary in the
        helper/handler design findings doc.
        """
        return bool(getattr(self.request.state, "is_admin", False))

    async def save_object(self, obj):
        """Flush, refresh, then emit an audit event.

        Overriding ``save_object`` is the right place for side effects that
        should run after *every* write — both create and update — because
        both ``handle_create`` and ``handle_update`` end by calling ``self.save_object``.
        """
        obj = await super().save_object(obj)
        # In production: publish to an audit log or event bus.
        # await audit_bus.emit("saved", model=type(obj).__name__, id=obj.id)
        return obj

    def _emit(
        self,
        event_type: str,
        aggregate: Any,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Write an outbox row in the current session.

        Call this *after* ``save_object`` so ``aggregate.id`` is populated.
        The session has flushed but not committed; the outbox row joins
        the same transaction and either both end up in the database or
        neither does.

        DO NOT replace this with a direct ``await email_service.send(...)``
        call before commit: if the transaction rolls back the email still
        goes out, and you've leaked information about a row that doesn't
        exist. The outbox is the durable boundary.
        """
        from ..models import OutboxEvent

        self.session.add(
            OutboxEvent(
                event_type=event_type,
                aggregate_type=type(aggregate).__name__,
                aggregate_id=getattr(aggregate, "id", 0) or 0,
                payload=payload or {},
            )
        )
