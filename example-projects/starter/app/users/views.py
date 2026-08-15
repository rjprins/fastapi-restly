"""The User API.

Restly generates list, retrieve, create, update, and delete from ``model`` and
``schema``. Nothing else is needed to serve them.

Shape those routes by overriding ``build_query`` for reads and a business
method such as ``create`` for writes, and add routes of your own with
``fr.get`` and ``fr.post``. See the Customize RestView guide.

Add your own view classes beside this one, then list them in ``app/main.py``.
"""

import fastapi_restly as fr

from .models import User
from .schemas import UserSchema


class UserView(fr.AsyncRestView[User, UserSchema]):
    """CRUD for ``/users``.

    The type parameters tell a type checker that ``super().create()`` returns a
    ``User``. Without them it returns a bare ``DeclarativeBase``, and reading a
    field off the result is rejected as soon as you override a method.
    """

    prefix = "/users"
    model = User
    schema = UserSchema
