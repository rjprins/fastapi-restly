"""Per-request context and the tenant floor that reads it.

``Current`` declares the request-bound identity facts: the organization
the request acts in, the user, the user's role, and the platform-admin
flag. None of them is ever ``None``. A request without an authenticated
identity does not reach a tenant route (the sources below answer 401),
and a script binds what it needs with ``Current.bind``.
``bind_request_context`` is the generated dependency that binds them
once per request, fed by the auth sources themselves so
``app.dependency_overrides`` keeps working in tests. The tenant floor
that reads these values lives next to the tenant column, in
``app.models``: a session listener restricts every ORM SELECT over a
tenant-owned class to ``Current.org_id`` unless ``Current.is_admin``.

Admin flows are conditionals on ``Current.is_admin``, not a separate
route tree: an admin reads across tenants and writes into one by acting
as it, with the auth layer binding the acted-as organization. The first
admin is seeded by a migration; every later user is created through the
API by someone acting in that user's organization.
"""

from __future__ import annotations

from typing import Any

import fastapi

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
