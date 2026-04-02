"""User view."""

from typing import Any

from pydantic import BaseModel

import fastapi_restly as fr

from ..models import User, UserRole
from ..schemas import UserSchema
from ..schemas.user import UserFullSchema, UserPublicSchema
from ._base import TenantBase

# Set by tests to simulate field-level permissions without real auth middleware.
_TEST_USER_ROLE: "UserRole | None" = None
# Set by tests to simulate the currently authenticated user.
_TEST_USER_ID: int | None = None


class UpdateMeRequest(BaseModel):
    """Request body for updating current user's profile."""

    name: str | None = None
    email: str | None = None


class UserView(TenantBase):
    """CRUD endpoints for users.

    Inherits from TenantBase for the auth dependency and audit save_object.
    Demonstrates field-level permissions: salary is only visible to HR role.
    """

    prefix = "/users"
    model = User
    schema = UserSchema

    def _current_user_role(self) -> UserRole | None:
        """Return the current user's role.

        In production: set by auth middleware via ``request.state.user_role``.
        In tests: controlled via the module-level ``_TEST_USER_ROLE`` variable.
        """
        return getattr(self.request.state, "user_role", None) or _TEST_USER_ROLE

    def _can_see_salary(self) -> bool:
        role = self._current_user_role()
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
    async def get_current_user(self) -> Any:
        """Get current user's profile."""
        from fastapi import HTTPException

        user_id = getattr(self.request.state, "user_id", None) or _TEST_USER_ID
        user = await self.session.get(User, user_id) if user_id else None
        if not user:
            raise HTTPException(status_code=404, detail="Current user not found")
        return self.to_response_schema(user)

    @fr.patch("/me", response_model=UserSchema)
    async def update_current_user(self, request: UpdateMeRequest) -> Any:
        """Update current user's profile."""
        from fastapi import HTTPException

        user_id = getattr(self.request.state, "user_id", None) or _TEST_USER_ID
        user = await self.session.get(User, user_id) if user_id else None
        if not user:
            raise HTTPException(status_code=404, detail="Current user not found")

        # Update only provided fields
        data = request.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(user, key, value)

        user = await self.save_object(user)
        return self.to_response_schema(user)
