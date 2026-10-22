"""User view."""

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

import fastapi_restly as fr
from fastapi_restly.views import ViewResponse

from ..auth import hash_password, verify_password
from ..current import Current
from ..views import AuthenticatedView, SoftDeleteMixin
from .models import User
from .roles import UserRole
from .schemas import UserFullResponse, UserPublicResponse, UserSchema, UserUpdate


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


class UserView(SoftDeleteMixin, AuthenticatedView[User]):
    """CRUD endpoints for users.

    The session listener restricts reads to the tenant. ``UserClauses`` hides
    soft-deleted rows. ``User`` stamps its tenant and audit ids
    itself (``app.models``), and ``SoftDeleteMixin`` flips ``deleted_at``
    on delete. This view keeps user-specific behavior: password hashing,
    field-level permissions, /me routes, and change-password.

    ``schema_update`` leaves ``password`` out of the PATCH body. Otherwise a
    PATCH could set a password without the current-password check of
    change-password, and without a hash.

    ``UserSchema`` marks ``salary`` write-only, so the CRUD routes never
    show it, and it is not a filter or a sort key. The one route that shows
    it is ``get_user_with_field_permissions``, and only to HR and owners.
    """

    prefix = "/users"
    model = User
    schema = UserSchema
    schema_update = UserUpdate
    # No scope declared: reads apply UserClauses.default_scope.

    class Action(fr.Action):
        CHANGE_PASSWORD = "change_password"

    def _can_see_salary(self) -> bool:
        return Current.role() in (UserRole.HR, UserRole.OWNER)

    async def create(self, data: Any) -> User:
        """Hash the password. The commit bracket saves the new row after this."""
        user = await super().create(data)
        if data.password:
            user.password = hash_password(data.password)
        return user

    @fr.post("/{id}/change-password")
    async def change_password(
        self, id: int, request: ChangePasswordRequest
    ) -> ViewResponse:
        """Change a user's password.

        This is an action route because the payload differs from PATCH. The row
        is loaded through ``get_one`` and the mutation is gated and committed
        by ``write_action``.
        """
        user = await self.get_one(id)
        if not verify_password(request.current_password, user.password):
            raise fr.exc.Forbidden("Current password is incorrect")
        if not request.new_password:
            raise HTTPException(422, "new_password is required")

        async with self.write_action(self.Action.CHANGE_PASSWORD, obj=user):
            user.password = hash_password(request.new_password)
        return user

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
            schema: type[BaseModel] = UserFullResponse
        else:
            # Others get public schema without salary
            schema = UserPublicResponse

        return schema.model_validate(user, from_attributes=True).model_dump()

    @fr.get("/me")
    async def get_current_user(self) -> ViewResponse:
        """Get current user's profile."""
        return await self.handle_get_one(Current.user_id())

    @fr.patch("/me")
    async def update_current_user(self, request: UpdateMeRequest) -> ViewResponse:
        """Update current user's profile.

        Delegates to ``handle_update`` so this action route follows the exact
        same path as ``PATCH /users/{id}``: the request handler loads the row
        (scope + 404), runs ``authorize``, calls the business ``update``, and
        commits via the bracket. Any future ``update`` override (validation,
        auditing) applies here automatically.
        """
        return await self.handle_update(Current.user_id(), request)
