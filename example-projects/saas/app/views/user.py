"""User view."""

from pydantic import BaseModel
from typing import Any

import fastapi_restly as fr

from ..models import User, UserRole
from ..schemas import UserSchema
from ..schemas.user import UserPublicSchema, UserFullSchema


# Simulating a "current user" - in real apps this would come from auth
CURRENT_USER_ID = 1  # Would be set by auth middleware
# Simulating current user's role - in real apps would come from JWT/session
CURRENT_USER_ROLE: UserRole | None = None  # Set to enable field-level permissions


class UpdateMeRequest(BaseModel):
    """Request body for updating current user's profile."""

    name: str | None = None
    email: str | None = None


class UserView(fr.AsyncAlchemyView):
    """CRUD endpoints for users.

    Demonstrates field-level permissions: salary is only visible to HR role.
    Set CURRENT_USER_ROLE = UserRole.HR to see salary field.
    """

    prefix = "/users"
    model = User
    schema = UserSchema

    def _get_current_user_role(self) -> UserRole | None:
        """Get current user's role from auth context (placeholder for real auth)."""
        return CURRENT_USER_ROLE

    def _can_see_salary(self) -> bool:
        """Check if current user can see salary fields."""
        role = self._get_current_user_role()
        return role in (UserRole.HR, UserRole.OWNER)

    @fr.get("/{id}/with-permissions", response_model=dict)
    async def get_user_with_field_permissions(self, id: int) -> dict[str, Any]:
        """Get a user with field-level permissions applied.

        - HR and Owner roles can see salary field
        - Other roles see a limited view without salary

        Example: GET /users/1/with-permissions
        """
        from fastapi import HTTPException

        user = await self.session.get(User, id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Select schema based on viewer's role
        if self._can_see_salary():
            # HR/Owner gets full schema with salary
            schema = UserFullSchema
        else:
            # Others get public schema without salary
            schema = UserPublicSchema

        return schema.model_validate(user, from_attributes=True).model_dump()

    @fr.get("/me", response_model=UserSchema)
    async def get_current_user(self) -> User:
        """Get current user's profile."""
        from fastapi import HTTPException

        # In real apps, get user ID from auth context
        user_id = CURRENT_USER_ID
        user = await self.session.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Current user not found")
        return user

    @fr.patch("/me", response_model=UserSchema)
    async def update_current_user(self, request: UpdateMeRequest) -> User:
        """Update current user's profile."""
        from fastapi import HTTPException

        user_id = CURRENT_USER_ID
        user = await self.session.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Current user not found")

        # Update only provided fields
        data = request.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(user, key, value)

        return user
