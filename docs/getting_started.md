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
from contextlib import asynccontextmanager

import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

DATABASE_URL = "sqlite+aiosqlite:///app.db"
fr.configure(async_database_url=DATABASE_URL)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    engine = fr.FRAsyncSession.kw["bind"]
    async with engine.begin() as conn:
        await conn.run_sync(fr.DataclassBase.metadata.create_all)
    yield


app = FastAPI(lifespan=lifespan)


class User(fr.IDBase):
    name: Mapped[str]
    email: Mapped[str]


@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
    prefix = "/users"
    model = User
```

A few things to note:

- The table name is derived automatically from the class name (`User` → `user` table).
- `fr.DataclassBase` is the explicit dataclass-oriented declarative base.
- `fr.IDBase` is the convenience alias that combines `DataclassBase` with an auto-incrementing integer `id` primary key.
- If you prefer standard SQLAlchemy declarative style (without dataclass semantics), use `fr.PlainIDBase` instead — both work with the rest of the framework.
- With no manual schema, FastAPI-Restly auto-generates one from your model.

When auto-generated schemas are a good fit:

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

You can filter results using query parameters. For example, `GET /users/?name=Jane` returns only users named Jane. See [Query Modifiers](query_modifiers.md) for the full filter syntax.

## 5. Add an Explicit Schema (Optional)

Replace the `UserView` definition from Section 2 with:

```python
class UserSchema(fr.IDSchema):
    name: str
    email: str


@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
    prefix = "/users"
    model = User
    schema = UserSchema
```

`fr.IDSchema` already includes the `id` field as `fr.ReadOnly` (excluded from create/update requests, present in responses). You can apply the same marker to your own fields: `fr.ReadOnly[str]` keeps a field out of write operations. `fr.WriteOnly[T]` does the opposite — accepted on input, omitted from responses (useful for passwords).

Choose explicit schemas when you need:

- Public API contracts you want to keep stable
- Custom validation logic
- Field aliases and strict response shaping
- Extra clarity for teams that prefer less implicit behavior

## 6. Test Quickly

```python
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)
res = client.post("/users/", json={"name": "Jane", "email": "jane@example.com"})
assert res.status_code == 201
```

`TestClient` handles the async lifespan automatically, so the database tables are created before the first request. For test isolation (rolling back test data between tests), see the [Testing](api_reference.md) utilities in `fastapi_restly.testing`.

## Next Steps

- [API Reference](api_reference.md)
- [Query Modifiers](query_modifiers.md)
- [Tutorial](tutorial.md)
