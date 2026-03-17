# How-To: Use Custom Schemas and Aliases

Use a custom schema when you need explicit validation, aliases, or read/write control.

## 1. Define Model

```python
class User(fr.IDBase):
    first_name: Mapped[str]
    email: Mapped[str]
```

## 2. Define Schema with Aliases

```python
from pydantic import Field

class UserSchema(fr.IDSchema):
    first_name: str = Field(alias="firstName")
    email: str
    internal_id: fr.ReadOnly[str]
```

## 3. Attach Schema to View

```python
@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
    prefix = "/users"
    model = User
    schema = UserSchema
```

## What You Get

- Incoming payloads accept alias keys (for example `firstName`).
- Response payloads use alias keys from the schema.
- Read-only fields are skipped for create/update.
