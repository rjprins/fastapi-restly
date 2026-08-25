"""Per-request context and the scope clauses that read it.

``Current`` declares the request-bound values: org, user, the admin flag,
and the ``include_deleted`` toggle. ``bind_request_context`` is the
generated dependency that binds them once per request, fed by the auth
sources themselves so ``app.dependency_overrides`` keeps working in tests.
``tenant_scope`` and ``soft_delete_scope`` build the visibility predicates
that read these values; each subject's ``ClauseNamespace`` (in its
``models.py``) composes them into the model's ``default_scope``.
"""

from __future__ import annotations

from typing import Annotated, Any

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


def get_include_deleted(request: fastapi.Request) -> bool:
    """Whether ``?include_deleted=true`` asks to see soft-deleted rows."""
    return request.query_params.get("include_deleted", "false").lower() == "true"


class Current(fr.ContextNamespace):
    """Per-request context the scope clauses read.

    The bind dependency below broadcasts these once per request; the
    clauses branch on them. Member names are the bind names.
    """

    org_id: fr.ContextParam[int | None]
    user_id: fr.ContextParam[int | None]
    is_admin: fr.ContextParam[bool]
    include_deleted: fr.ContextParam[bool]


# One generated dependency binds every Current member per request. The
# sources are the auth dependencies themselves, so
# ``app.dependency_overrides`` keeps working in tests.
bind_request_context = Current.depends(
    org_id=get_current_org_id,
    user_id=get_current_user_id,
    is_admin=get_is_admin,
    include_deleted=get_include_deleted,
)


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
