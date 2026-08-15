"""The public API contract for a user.

Restly derives the create and update bodies from this one schema, dropping the
read-only fields, so there is a single place to change when the contract does.
"""

import fastapi_restly as fr


class UserSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    """Request and response shape for ``/users``.

    ``fr.IDSchema`` adds a read-only ``id``, and ``fr.TimestampsSchemaMixin``
    adds read-only timestamps. Mark any other server-owned field with
    ``fr.ReadOnly[...]``: it appears in responses and is refused in request
    bodies.
    """

    email: str
    name: str
