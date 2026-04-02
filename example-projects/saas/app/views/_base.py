"""Shared base view for the SaaS example.

Demonstrates:
- Class-level ``dependencies`` applied to every route on every subclass
- ``save_object`` override to run a side effect after every write
- A shared ``_current_org_id()`` helper that reads from request state,
  used by subclasses to scope list/get operations to the current tenant

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

from typing import Any, ClassVar

import fastapi

import fastapi_restly as fr

# Set by tests to simulate tenant isolation without real auth middleware.
# In production, request.state.org_id is set by your auth middleware instead.
_TEST_ORG_ID: int | None = None


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
    - ``save_object`` that calls through to super() then logs the write
    - ``_current_org_id()`` helper for tenant-scoped filtering
    """

    # Applied to every route registered by this view and all subclasses.
    dependencies: ClassVar[list[Any]] = [fastapi.Depends(check_api_key)]

    def _current_org_id(self) -> int | None:
        """Return the current tenant's org ID.

        In production: set by auth middleware via ``request.state.org_id``.
        In tests: controlled via the module-level ``_TEST_ORG_ID`` variable.
        Returns ``None`` when neither is set (all rows visible, no scoping).
        """
        return getattr(self.request.state, "org_id", None) or _TEST_ORG_ID

    async def save_object(self, obj):
        """Flush, refresh, then emit an audit event.

        Overriding ``save_object`` is the right place for side effects that
        should run after *every* write — both create and update — because
        both ``on_create`` and ``on_update`` end by calling ``self.save_object``.
        """
        obj = await super().save_object(obj)
        # In production: publish to an audit log or event bus.
        # await audit_bus.emit("saved", model=type(obj).__name__, id=obj.id)
        return obj
