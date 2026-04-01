# How-To: Use Custom Schemas and Aliases

Use a custom schema when you need explicit validation, aliases, or read/write field control. If you omit `schema` on a view, FastAPI-Restly generates one automatically from the model — this guide covers the cases where you want full control instead.

## 1. Define Model

```python
import fastapi_restly as fr
from sqlalchemy.orm import Mapped

class User(fr.IDBase):
    first_name: Mapped[str]
    email: Mapped[str]
    password_hash: Mapped[str]
```

## 2. Define Schema

```python
from pydantic import Field
import fastapi_restly as fr

class UserSchema(fr.IDSchema):
    # Alias: clients send "firstName", the ORM field is "first_name"
    first_name: str = Field(alias="firstName")
    email: str
    # WriteOnly: accepted on POST/PATCH but never included in responses
    password_hash: fr.WriteOnly[str]
```

`IDSchema` already provides an `id` field marked `fr.ReadOnly`, so `id` is automatically excluded from POST and PATCH payloads — you do not need to declare it yourself.

`fr.WriteOnly[T]` marks a field as write-only: it is accepted in create and update requests but stripped from all responses.

## 3. Attach Schema to View

```python
@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
    prefix = "/users"
    model = User
    schema = UserSchema
```

## What You Get

- Incoming payloads accept the alias key (`firstName`).
- Responses use the alias key (`firstName`) on Restly routes as well.
- `id` never appears in POST/PATCH request bodies because it is `ReadOnly` on `IDSchema`.
- `password_hash` never appears in responses because it is `WriteOnly`.
