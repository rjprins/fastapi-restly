"""User view."""

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

import fastapi_restly as fr

from ..auth import hash_password, verify_password
from ..models import User, UserRole
from ..schemas import UserSchema
from ..schemas.user import UserFullSchema, UserPublicSchema
from ._base import TenantBase
from ._mixins import AuditStampedMixin, SoftDeleteMixin, TenantScopedMixin

# Set by tests to simulate field-level permissions without real auth middleware.
_TEST_USER_ROLE: "UserRole | None" = None


class UpdateMeRequest(BaseModel):
    """Request body for updating current user's profile."""

    name: str | None = None
    email: str | None = None


class ChangePasswordRequest(BaseModel):
    """Request body for the change-password action.

    The action route exists because changing a password has different
    inputs from a generic PATCH: it needs the *current* password as
    proof-of-possession, and the new password is not part of the
    public ``User`` representation.
    """

    current_password: str
    new_password: str


class UserView(SoftDeleteMixin, AuditStampedMixin, TenantScopedMixin, TenantBase):
    """CRUD endpoints for users.

    Mixins handle tenant scope, audit stamps, and soft delete. This view keeps
    user-specific behavior: password hashing, field-level permissions, /me
    routes, and change-password.
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

    async def create(self, schema_obj):
        """Hash the password before saving the new row."""
        user = await self.make_new_object(schema_obj)
        if schema_obj.password:
            user.password = hash_password(schema_obj.password)
        return await self.save_object(user)

    @fr.post("/{id}/change-password", response_model=UserSchema)
    async def change_password(self, id: int, request: ChangePasswordRequest) -> Any:
        """Change a user's password.

        This is an action route because the payload differs from PATCH. The row
        is loaded through ``handle_get_one`` and the mutation is committed by
        ``write_action``.
        """
        user = await self.handle_get_one(id)
        if not verify_password(request.current_password, user.password):
            raise HTTPException(403, "Current password is incorrect")
        if not request.new_password:
            raise HTTPException(422, "new_password is required")

        async with self.write_action("change_password", obj=user):
            user.password = hash_password(request.new_password)
            await self.save_object(user)
        return self.to_response_schema(user)

    @fr.get("/{id}/with-permissions", response_model=dict)
    async def get_user_with_field_permissions(self, id: int) -> dict[str, Any]:
        """Get a user with field-level permissions applied.

        - HR and Owner roles can see salary field
        - Other roles see a limited view without salary

        Example: GET /users/1/with-permissions
        """
        user = await self.handle_get_one(id)

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
        user_id = self._current_user_id()
        if not user_id:
            raise HTTPException(status_code=404, detail="Current user not found")
        user = await self.handle_get_one(user_id)
        return self.to_response_schema(user)

    @fr.patch("/me", response_model=UserSchema)
    async def update_current_user(self, request: UpdateMeRequest) -> Any:
        """Update current user's profile.

        Delegates to ``handle_update`` so this action route follows the exact
        same path as ``PATCH /users/{id}``: the request handler loads the row
        (scope + 404), runs ``authorize``, calls the business ``update``, and
        commits via the bracket. Any future ``update`` override (validation,
        auditing) applies here automatically.
        """
        user_id = self._current_user_id()
        if not user_id:
            raise HTTPException(status_code=404, detail="Current user not found")
        user = await self.handle_update(user_id, request)
        return self.to_response_schema(user)
