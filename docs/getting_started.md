# Getting Started

This guide walks from zero to a working REST API with FastAPI-Restly.

**Requirements:** Python 3.10 or later.

## 1. Install

```bash
pip install "fastapi-restly[standard]" aiosqlite
```

The base package intentionally stays small. The `standard` extra adds FastAPI's
standard server dependencies (the `fastapi dev` toolchain), mirroring
`fastapi[standard]`. Restly is database-driver-agnostic, so the async driver is a
separate, explicit dependency — `aiosqlite` for the SQLite examples in this guide,
or `asyncpg`/`psycopg` for PostgreSQL. Test tooling lives in its own extra; see
[Testing](howto_testing.md) for `fastapi-restly[testing]`.

## 2. Create an App

Create `main.py`:

```python
from contextlib import asynccontextmanager

import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

fr.configure(async_database_url="sqlite+aiosqlite:///app.db")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    email: Mapped[str]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Dev/demo table creation only; use Alembic migrations in production.
    # Runs after model classes are declared, so metadata contains every table.
    await fr.db.async_create_all(Base)
    yield


app = FastAPI(lifespan=lifespan)


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
```

A few things to note:

- The model uses standard SQLAlchemy declarative style: your own `DeclarativeBase`,
  an explicit `__tablename__`, and an explicit primary-key column.
- If you prefer dataclass-oriented SQLAlchemy models, FastAPI-Restly also provides
  `fr.DataclassBase` and `fr.IDBase` convenience bases.
- `RestView` and `AsyncRestView` expect a single primary-key column; for
  composite-key tables, see
  [the view hierarchy](class_based_views.md#the-view-hierarchy).
- With no manual schema, FastAPI-Restly auto-generates `UserRead`, `UserCreate`,
  and `UserUpdate` from your model.
- The lifespan hook creates tables with the configured async engine. Use Alembic migrations in production.

When auto-generated schemas are a good fit:

- Internal tools and backoffice APIs
- Early project scaffolding or prototypes
- Straightforward models with minimal validation rules

### Sync or Async?

Default to `AsyncRestView` for new services. Use sync `RestView` for sync-only libraries or sync-first codebases. Mixed projects are fine; choose per view.

## 3. Run

```bash
fastapi dev main.py
```

> **Note:** `fastapi-restly[standard]` includes the extras needed by `fastapi dev`. The dev server is not for production.

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

Filter lists with query parameters, for example `GET /users/?name=Jane`. See [Filter, Sort, and Paginate Lists](howto_query_modifiers.md).

## 5. Add an Explicit Schema (Optional)

Replace the `UserView` definition from Section 2 with:

```python
class UserRead(fr.IDSchema):
    name: str
    email: str


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead
```

`schema` is the read/response contract. Restly derives `UserCreate` and
`UserUpdate` from it unless you override `schema_create` or `schema_update`.
`fr.IDSchema` includes `id` as `fr.ReadOnly`: present in responses, excluded from create/update. Use `fr.ReadOnly[T]` for other response-only fields and `fr.WriteOnly[T]` for input-only fields such as passwords.

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

- [Tutorial](tutorial.md) — build a complete multi-model API, step by step.
- [Class-Based Views](class_based_views.md) — what makes the views subclassable,
  and when to use `View`, `RestView`, or a plain FastAPI route.
- Already have a FastAPI app? See
  [Existing Project Integration](howto_existing_project.md) — Restly adopts
  per resource, beside your current routes.
- [Filter, Sort, and Paginate Lists](howto_query_modifiers.md)
- [Deploying](deploying.md) — production engine config, Alembic, and a `main.py` template.
- [API Reference](api_reference.md)
