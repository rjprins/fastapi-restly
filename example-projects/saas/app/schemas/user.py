"""User schema."""

import fastapi_restly as fr

from ..models import User, UserRole


class UserSchema(fr.TimestampsSchemaMixin, fr.IDSchema[User]):
    """Schema for User model."""

    email: str
    name: str
    role: UserRole = UserRole.MEMBER
    organization_id: int
