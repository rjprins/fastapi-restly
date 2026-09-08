"""Per-request context and the tenant floor that reads it.

``Current`` declares the request-bound identity facts: the organization
the request acts in, the user, the user's role, and the platform-admin
flag. None of them is ever ``None``. A request without an authenticated
identity does not reach a tenant route (the sources below answer 401),
and a script binds what it needs with ``Current.bind``.
``bind_request_context`` is the generated dependency that binds them
once per request, fed by the auth sources themselves so
``app.dependency_overrides`` keeps working in tests. ``tenant_scope``
builds the tenant predicate that reads these values, and
``TenantClauses`` is the namespace base that puts it under every
tenant-bound model's ``default_scope``; ``TenantBase.apply_scope`` (in
``app.views``) stacks it under every view read. Together they are the
application's floor: what holds whatever scope a view or a route named.

Admin flows are conditionals on ``Current.is_admin``, not a separate
route tree: an admin reads across tenants and writes into one by acting
as it, with the auth layer binding the acted-as organization. The first
admin is seeded by a migration; every later user is created through the
API by someone acting in that user's organization.
"""

from __future__ import annotations

from typing import Any, ClassVar

import fastapi
import sqlalchemy as sa

import fastapi_restly as fr

from .users.roles import UserRole


def _required(request: fastapi.Request, name: str) -> Any:
    """A ``request.state`` value the auth layer must have set; 401 without it."""
    value = getattr(request.state, name, None)
    if value is None:
        raise fastapi.HTTPException(401, "Authentication required")
    return value


def get_current_org_id(request: fastapi.Request) -> int:
    """The organization this request acts in, set by the auth layer.

    A tenant's user acts in their own organization; an admin acts in the
    one the request names (an act-as header, say).
    """
    return _required(request, "org_id")


def get_current_user_id(request: fastapi.Request) -> int:
    """The authenticated user, set by the auth layer."""
    return _required(request, "user_id")


def get_current_role(request: fastapi.Request) -> UserRole:
    """The authenticated user's role in the organization, set by the auth layer."""
    return _required(request, "user_role")


def get_is_admin(request: fastapi.Request) -> bool:
    """Whether this is a platform admin's request: its reads cross every tenant."""
    return bool(getattr(request.state, "is_admin", False))


class Current(fr.ContextNamespace):
    """Per-request context the scope clauses and the column stamps read.

    The bind dependency below broadcasts these once per request; the
    clauses branch on them and the model mixins in ``app.models`` stamp
    from them. Member names are the bind names.
    """

    org_id: fr.ContextParam[int]
    user_id: fr.ContextParam[int]
    role: fr.ContextParam[UserRole]
    is_admin: fr.ContextParam[bool]


# One generated dependency binds every Current member per request. The
# sources are the auth dependencies themselves, so
# ``app.dependency_overrides`` keeps working in tests.
bind_request_context = Current.depends(
    org_id=get_current_org_id,
    user_id=get_current_user_id,
    role=get_current_role,
    is_admin=get_is_admin,
)


def tenant_scope(model: type[Any]) -> fr.WhereClause:
    """Rows of ``model`` owned by the organization the request acts in.

    An admin request sees every row. ``model`` must carry an
    ``organization_id`` column.
    """

    @fr.where_clause
    def owned_by_tenant() -> sa.ColumnElement[bool]:
        if Current.is_admin():
            return sa.true()
        return model.organization_id == Current.org_id()

    return owned_by_tenant


_TENANT_RULES: dict[type[Any], fr.WhereClause] = {}


class TenantClauses(fr.ClauseNamespace):
    """Namespace base for a tenant-bound model.

    A subclass declares ``model``; ``owned_by_tenant`` is derived from the
    model's ``organization_id`` unless the subclass declares its own (Task
    reaches the organization through its project). The tenant clause is
    composed under whatever ``default_scope`` the subclass declared, so
    every reference check and every default read carries it. Restly's
    scopes replace each other; this base is what makes the tenant rule
    hold regardless.
    """

    owned_by_tenant: ClassVar[fr.WhereClause]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        model = vars(cls).get("model")
        if model is not None:
            if "owned_by_tenant" not in vars(cls):
                cls.owned_by_tenant = tenant_scope(model)
            declared = vars(cls).get("default_scope")
            cls.default_scope = (
                fr.all_of(cls.owned_by_tenant, declared)
                if isinstance(declared, fr.WhereClause)
                else cls.owned_by_tenant
            )
            _TENANT_RULES[model] = cls.owned_by_tenant
        super().__init_subclass__(**kwargs)


def tenant_rule(model: type[Any]) -> fr.WhereClause | None:
    """The tenant clause of ``model``, or None for a model without one."""
    return _TENANT_RULES.get(model)
