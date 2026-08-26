"""Per-request context and the scope clause that reads it.

``Current`` declares the request-bound identity facts: org, user, and the
admin flag. ``bind_request_context`` is the generated dependency that
binds them once per request, fed by the auth sources themselves so
``app.dependency_overrides`` keeps working in tests. ``tenant_scope``
builds the tenant predicate that reads these values; each subject's
``ClauseNamespace`` (in its ``models.py``) composes it into the model's
``default_scope``.
"""

from __future__ import annotations

from typing import Any

import fastapi
import sqlalchemy as sa

import fastapi_restly as fr


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


# One generated dependency binds every Current member per request. The
# sources are the auth dependencies themselves, so
# ``app.dependency_overrides`` keeps working in tests.
bind_request_context = Current.depends(
    org_id=get_current_org_id,
    user_id=get_current_user_id,
    is_admin=get_is_admin,
)


def tenant_scope(model: type[Any]) -> fr.WhereClause:
    """Rows of ``model`` owned by the authenticated organization.

    Admin requests, and requests without an org in context, see every row.
    ``model`` must carry an ``organization_id`` column.
    """

    @fr.where_clause
    def owned_by_tenant() -> sa.ColumnElement[bool]:
        # Read before the admin check: an unbound context stays a loud error.
        org_id = Current.org_id()
        if Current.is_admin() or org_id is None:
            return sa.true()
        return model.organization_id == org_id

    return owned_by_tenant
