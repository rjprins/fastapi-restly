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

    Mixin-composed: tenant scope (filter + stamp), audit stamps, soft
    delete — all delivered by the chain. The view body retains only the
    user-specific concerns: password hashing, field-level permissions,
    /me routes, and change-password.

    The ``perform_create`` override below is the canonical illustration of the
    "rewrite from scratch using the helpers" pattern from
    ``rut-notes/discussion_save_object.md``: build the ORM object via
    ``self.build_from_schema`` (which transparently runs through the mixin
    chain to stamp tenant + audit fields), mutate the password, then
    flush+refresh via ``save_object``.
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

    async def perform_create(self, schema_obj):
        """Hash the plaintext password before the row is persisted.

        Canonical "rewrite using helpers" pattern (option B' from
        ``rut-notes/discussion_save_object.md``):

        DO NOT do this::

            user = await super().perform_create(schema_obj)   # already flushed
            user.password = hash_password(...)           # in-memory only
            return user                                  # plaintext on disk

        DO this — build the row with the helpers, mutate, then save::

            user = await self.build_from_schema(schema_obj)
            user.password = hash_password(schema_obj.password)
            return await self.save_object(user)
        """
        user = await self.build_from_schema(schema_obj)
        if schema_obj.password:
            user.password = hash_password(schema_obj.password)
        return await self.save_object(user)

    @fr.post("/{id}/change-password", response_model=UserSchema)
    async def change_password(self, id: int, request: ChangePasswordRequest) -> Any:
        """Change a user's password.

        Action route rather than PATCH because the request contract is
        different (proof-of-possession via ``current_password``, no public
        body fields). Calls ``perform_get`` for the fetch+404 (so any row-level
        access checks layered into ``perform_get`` apply here too) and uses
        ``save_object`` as a utility for the final flush+refresh.
        """
        user = await self.perform_get(id)
        if not verify_password(request.current_password, user.password):
            raise HTTPException(403, "Current password is incorrect")
        if not request.new_password:
            raise HTTPException(422, "new_password is required")

        user.password = hash_password(request.new_password)
        user = await self.save_object(user)
        return self.to_response_schema(user)

    @fr.get("/{id}/with-permissions", response_model=dict)
    async def get_user_with_field_permissions(self, id: int) -> dict[str, Any]:
        """Get a user with field-level permissions applied.

        - HR and Owner roles can see salary field
        - Other roles see a limited view without salary

        Example: GET /users/1/with-permissions
        """
        user = await self.perform_get(id)

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
        user = await self.perform_get(user_id)
        return self.to_response_schema(user)

    @fr.patch("/me", response_model=UserSchema)
    async def update_current_user(self, request: UpdateMeRequest) -> Any:
        """Update current user's profile.

        Delegates to ``perform_update`` so this action route follows the exact
        same path as ``PATCH /users/{id}``: any future ``perform_update`` override
        (validation, auditing, side-effects) applies here automatically.
        """
        user_id = self._current_user_id()
        if not user_id:
            raise HTTPException(status_code=404, detail="Current user not found")
        user = await self.perform_update(user_id, request)
        return self.to_response_schema(user)
