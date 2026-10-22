"""User schema."""

from datetime import datetime

from pydantic import BaseModel

import fastapi_restly as fr

from .roles import UserRole


class UserSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    """Schema for User model.

    ``password`` is plaintext in the request (write-only) and is never echoed
    back. The view's ``create`` (the bare business verb) hashes it into the
    ``User.password`` column before the row is persisted. The PATCH body,
    ``UserUpdate``, leaves it out: a password changes only through the
    change-password action.

    ``salary`` is write-only too. The create and update bodies take it, but
    no CRUD response shows it, and a client cannot filter or sort on it.
    HR and owners read it on ``GET /users/{id}/with-permissions``.

    Soft-delete + audit fields are ReadOnly so they appear in responses
    (e.g. ``deleted_at`` after a soft delete) but PATCH bodies can't spoof
    them. So is ``organization_id``, the tenant stamp: a user is created in
    the organization the request acts in, never in one the body names.
    """

    email: str
    name: str
    password: fr.WriteOnly[str] = ""
    role: UserRole = UserRole.MEMBER
    organization_id: fr.ReadOnly[int]
    # Sensitive field: only HR and owners see it, through
    # UserView.get_user_with_field_permissions.
    salary: fr.WriteOnly[int | None] = None

    # Stamped server-side by the view's mixin chain.
    deleted_at: fr.ReadOnly[datetime | None] = None
    created_by_id: fr.ReadOnly[int | None] = None
    updated_by_id: fr.ReadOnly[int | None] = None


class UserUpdate(BaseModel):
    """The PATCH body for a user: every writable field except ``password``.

    A password changes only through ``POST /users/{id}/change-password``,
    which checks the current password first. A PATCH that sends
    ``password`` has it ignored, like any field this class does not declare.
    """

    email: str | None = None
    name: str | None = None
    role: UserRole | None = None
    salary: int | None = None


class UserPublicResponse(BaseModel):
    """Public user schema - excludes sensitive fields like salary.

    This schema is returned for non-HR users.
    """

    id: int
    email: str
    name: str
    role: UserRole
    organization_id: int


class UserFullResponse(BaseModel):
    """Full user schema - includes all fields including salary.

    This schema is only returned for HR users.
    """

    id: int
    email: str
    name: str
    role: UserRole
    organization_id: int
    salary: int | None = None
