"""User view."""

import fastapi_restly as fr

from ..models import User
from ..schemas import UserSchema


class UserView(fr.AsyncAlchemyView):
    """CRUD endpoints for users."""

    prefix = "/users"
    model = User
    schema = UserSchema
