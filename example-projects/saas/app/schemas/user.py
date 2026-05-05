"""User schema."""

from datetime import datetime

from pydantic import BaseModel

import fastapi_restly as fr

from ..models import UserRole


class UserSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    """Schema for User model.

    ``password`` is plaintext on the wire (write-only) and is never echoed
    back. The view's ``perform_create`` hashes it into ``User.password_hash``
    before the row is persisted. ``password_hash`` is intentionally not
    on the schema — clients should never see it.

    Soft-delete + audit fields are ReadOnly so they appear in responses
    (e.g. ``deleted_at`` after a soft delete) but PATCH bodies can't spoof
    them.
    """

    email: str
    name: str
    password: fr.WriteOnly[str] = ""
    role: UserRole = UserRole.MEMBER
    organization_id: int
    # Sensitive field - only visible to HR (see UserView.get_user_with_field_permissions)
    salary: int | None = None

    # Stamped server-side by the view's mixin chain.
    deleted_at: fr.ReadOnly[datetime | None] = None
    created_by_id: fr.ReadOnly[int | None] = None
    updated_by_id: fr.ReadOnly[int | None] = None


class UserPublicSchema(BaseModel):
    """Public user schema - excludes sensitive fields like salary.

    This schema is returned for non-HR users.
    """

    id: int
    email: str
    name: str
    role: UserRole
    organization_id: int


class UserFullSchema(BaseModel):
    """Full user schema - includes all fields including salary.

    This schema is only returned for HR users.
    """

    id: int
    email: str
    name: str
    role: UserRole
    organization_id: int
    salary: int | None = None
