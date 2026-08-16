import fastapi_restly as fr

from .models import User
from .schemas import UserSchema


class UserView(fr.RestView[User, UserSchema]):
    prefix = "/users"
    model = User
    schema = UserSchema
