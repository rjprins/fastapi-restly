# Getting Started

This guide walks from zero to a working CRUD API with FastAPI-Restly.

**Requirements:** Python 3.10 or later.

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

fr.configure(async_database_url="sqlite+aiosqlite:///app.db")


class User(fr.IDBase):
    name: Mapped[str]
    email: Mapped[str]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Dev/demo table creation only; use Alembic migrations in production.
    # Runs after model classes are declared, so metadata contains every table.
    engine = fr.get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(fr.DataclassBase.metadata.create_all)
    yield


app = FastAPI(lifespan=lifespan)


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
```

A few things to note:

- The table name is derived automatically from the class name (`User` → `user` table).
- `fr.DataclassBase` is the explicit dataclass-oriented declarative base.
- `fr.IDBase` is the convenience alias that combines `DataclassBase` with an auto-incrementing integer `id` primary key.
- If you prefer standard SQLAlchemy declarative style (without dataclass semantics), use `fr.PlainIDBase` instead — both work with the rest of the framework.
- With no manual schema, FastAPI-Restly auto-generates one from your model.
- The lifespan hook creates tables through the same async engine configured for the app. For production, use Alembic migrations instead of `create_all()`.

When auto-generated schemas are a good fit:

- Internal tools and backoffice APIs
- Early project scaffolding or prototypes
- Straightforward models with minimal validation rules

### Sync or Async?

Default to `AsyncRestView` for new services; it follows FastAPI's async-first grain and works with async SQLAlchemy drivers. Choose sync `RestView` when a view depends on sync-only libraries or when you are integrating with a sync-first codebase. Mixed projects are fine: pick `AsyncRestView` or `RestView` per view based on the dependencies that view calls.

## 3. Run

```bash
uv run fastapi dev main.py
```

> **Note:** `fastapi dev` requires the `fastapi[standard]` extras (`pip install "fastapi[standard]"` or add it to your dependencies). It is not needed for production — only for the development server.

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

You can filter results using query parameters. For example, `GET /users/?filter[name]=Jane` returns only users named Jane (V1 syntax; see [How-To: Filter, Sort, and Paginate Lists](howto_query_modifiers.md) for the full V1 and V2 syntax reference).

## 5. Add an Explicit Schema (Optional)

Replace the `UserView` definition from Section 2 with:

```python
class UserSchema(fr.IDSchema):
    name: str
    email: str


@fr.include_view(app)
class UserView(fr.AsyncRestView):
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

with TestClient(app) as client:
    res = client.post("/users/", json={"name": "Jane", "email": "jane@example.com"})
    assert res.status_code == 201
```

For test isolation (rolling back test data between tests), see the [Testing](howto_testing.md) guide.

## Next Steps

- [API Reference](api_reference.md)
- [How-To: Filter, Sort, and Paginate Lists](howto_query_modifiers.md)
- [Tutorial](tutorial.md)
