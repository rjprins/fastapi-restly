# Getting Started

This guide walks from zero to a working CRUD API with FastAPI-Restly.

## 1. Install

From the repository root:

```bash
uv sync
```

If you want example project dependencies too:

```bash
make install-dev
```

## 2. Create an App

Create `main.py`:

```python
import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

fr.setup_async_database_connection("sqlite+aiosqlite:///app.db")
app = FastAPI()

class User(fr.IDBase):
    name: Mapped[str]
    email: Mapped[str]

@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
    prefix = "/users"
    model = User
```

With no manual schema, FastAPI-Restly auto-generates one from your model.

When this mode is a good fit:
- Internal tools and backoffice APIs
- Early project scaffolding or prototypes
- Straightforward models with minimal validation rules

## 3. Run

```bash
uv run fastapi dev main.py
```

Open:
- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/openapi.json`

## 4. Use the Generated Endpoints

For `prefix = "/users"`, generated endpoints are:

- `GET /users/`
- `POST /users/`
- `GET /users/{id}`
- `PATCH /users/{id}`
- `DELETE /users/{id}`

Update semantics are `PATCH` (partial update).

## 5. Add an Explicit Schema (Optional)

```python
class UserSchema(fr.IDSchema):
    name: str
    email: str
    internal_id: fr.ReadOnly[str]

@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
    prefix = "/users"
    model = User
    schema = UserSchema
```

Choose explicit schemas when you need:
- Public API contracts you want to keep stable
- Custom validation logic
- Field aliases and strict response shaping
- Extra clarity for teams that prefer less implicit behavior

## 6. Test Quickly

```python
from fastapi.testclient import TestClient

client = TestClient(app)
res = client.post("/users/", json={"name": "Jane", "email": "jane@example.com"})
assert res.status_code == 201
```

## Next Steps

- [API Reference](api_reference.md)
- [Query Modifiers](query_modifiers.md)
- [Tutorial](tutorial.md)
- Testing guide: `TESTING.md` (repository root)
