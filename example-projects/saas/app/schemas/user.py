"""User schema."""

from pydantic import BaseModel

import fastapi_restly as fr

from ..models import User, UserRole


class UserSchema(fr.TimestampsSchemaMixin, fr.IDSchema[User]):
    """Schema for User model."""

    email: str
    name: str
    role: UserRole = UserRole.MEMBER
    organization_id: int
    # Sensitive field - only visible to HR (see UserView.get_user_with_field_permissions)
    salary: int | None = None


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
