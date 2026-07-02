# Getting Started

This guide walks from zero to a working REST API with FastAPI-Restly. Python
3.10 or later is required.

To try FastAPI-Restly without installing anything, open the project in GitHub
Codespaces, which builds the dev environment and its example projects for you:

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/rjprins/fastapi-restly?quickstart=1)

Once the Codespace finishes setup, run `cd example-projects/blog && uv run uvicorn blog.main:app --port 8000` in the terminal, then open the forwarded port 8000 and visit `/docs` for the interactive API.

## Installation

Install the framework and an async driver for your database:

```bash
pip install "fastapi-restly[standard]" aiosqlite
```

The base package intentionally stays small. The `standard` extra adds FastAPI's
standard server dependencies (the `fastapi dev` toolchain), mirroring
`fastapi[standard]`. Restly is database-driver-agnostic, so the async driver is a
separate, explicit dependency: `aiosqlite` for the SQLite examples in this guide,
or `asyncpg`/`psycopg` for PostgreSQL. Test tooling lives in its own extra; see
[Testing](howto_testing.md) for `fastapi-restly[testing]`.

## Create an app

A first application fits in one file, `main.py`:

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

A few details are worth noting:

- The model uses standard SQLAlchemy declarative style: your own `DeclarativeBase`,
  an explicit `__tablename__`, and an explicit primary-key column.
- If you prefer dataclass-oriented SQLAlchemy models, FastAPI-Restly also provides
  {class}`fr.DataclassBase <fastapi_restly.models.DataclassBase>` and {class}`fr.IDBase <fastapi_restly.models.IDBase>` convenience bases.
- {class}`RestView <fastapi_restly.views.RestView>` and {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` expect a single primary-key column; for
  composite-key tables, see
  [the view hierarchy](class_based_views.md#the-view-hierarchy).
- With no manual schema, FastAPI-Restly auto-generates `UserRead`, `UserCreate`,
  and `UserUpdate` from your model; see
  [Auto-Generated Schemas](technical_details.md#auto-generated-schemas).
- The lifespan hook creates tables with the async engine configured by
  {func}`fr.configure() <fastapi_restly.db.configure>`. Use
  [Alembic migrations](deploying.md#migrations-with-alembic) in production.

Auto-generated schemas are a good fit for internal tools and backoffice APIs,
early project scaffolding or prototypes, and straightforward models with
minimal validation rules.

### Sync or async?

Default to {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` for new services. Use sync {class}`RestView <fastapi_restly.views.RestView>` for sync-only libraries or sync-first codebases. Mixed projects are fine; choose per view.

## Run the app

With `main.py` in place, start the development server:

```bash
fastapi dev main.py
```

The `fastapi dev` command comes from the `standard` extra; the dev server is
not for production. Then open `http://127.0.0.1:8000/docs` or
`http://127.0.0.1:8000/openapi.json`.

## Use the generated endpoints

Registering `UserView` with `prefix = "/users"` generated five endpoints:

- `GET /users/`
- `POST /users/`
- `GET /users/{id}`
- `PATCH /users/{id}`
- `DELETE /users/{id}`

Update semantics are `PATCH` (partial update); see
[Generated REST Endpoints](api_reference.md#generated-rest-endpoints) for the
full contract. Filter lists with query parameters, for example
`GET /users/?name=Jane`. See [Filter, Sort, and Paginate Lists](howto_query_modifiers.md).

## Add an explicit schema (optional)

Auto-generated schemas can be replaced at any point. Replace the `UserView`
definition above with:

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

{attr}`schema <fastapi_restly.views.BaseRestView.schema>` is the read/response contract. Restly derives `UserCreate` and
`UserUpdate` from it unless you override {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` or {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>`.
{class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>` includes `id` as `fr.ReadOnly`: present in responses, excluded from create/update. Use `fr.ReadOnly[T]` for other response-only fields and `fr.WriteOnly[T]` for input-only fields such as passwords (see [ReadOnly and WriteOnly](howto_custom_schema.md#readonly-and-writeonly)).

Choose explicit schemas for public API contracts you want to keep stable,
custom validation logic, field aliases and strict response shaping, or extra
clarity for teams that prefer less implicit behavior;
[Auto-Generated vs Explicit Schemas](howto_custom_schema.md#auto-generated-vs-explicit-schemas)
compares the two.

## Test quickly

A quick check with FastAPI's regular `TestClient` confirms the API works:

```python
from fastapi.testclient import TestClient
from main import app

with TestClient(app) as client:
    res = client.post("/users/", json={"name": "Jane", "email": "jane@example.com"})
    assert res.status_code == 201
```

For test isolation (rolling back test data between tests), see the
[Testing](howto_testing.md) guide.

## Next steps

Continue with the **[Tutorial](tutorial.md)**, which builds a complete
multi-model API step by step. The pages below go deeper into individual topics:

- [Class-Based Views](class_based_views.md): what makes the views subclassable,
  and when to use {class}`View <fastapi_restly.views.View>`, {class}`RestView <fastapi_restly.views.RestView>`, or a plain FastAPI route.
- Already have a FastAPI app? [Use Restly in an Existing Project](howto_existing_project.md)
  shows how Restly adopts per resource, beside your current routes.
- [Filter, Sort, and Paginate Lists](howto_query_modifiers.md)
- [Deploying](deploying.md): production engine config, Alembic, and a `main.py` template.
- [API Reference](api_reference.md)
