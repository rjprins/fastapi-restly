# FastAPI-Restly

[![PyPI](https://img.shields.io/pypi/v/fastapi-restly)](https://pypi.org/project/fastapi-restly/)
[![CI](https://github.com/rjprins/fastapi-restly/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/rjprins/fastapi-restly/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://github.com/rjprins/fastapi-restly/blob/main/pyproject.toml)
[![License](https://img.shields.io/github/license/rjprins/fastapi-restly)](https://github.com/rjprins/fastapi-restly/blob/main/LICENSE)
[![Coverage](https://www.fastapi-restly.org/coverage/badge.svg)](https://www.fastapi-restly.org/coverage/)

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/rjprins/fastapi-restly?quickstart=1)

Try FastAPI-Restly in your browser: the badge above opens a GitHub Codespace with the dev environment installed, ready to run a live example API.

<p align="center">
  <img src="https://raw.githubusercontent.com/rjprins/fastapi-restly/main/docs/_static/restly-cat-white-bg.png" alt="FastAPI-Restly logo" width="200">
</p>

**Build maintainable REST APIs on FastAPI, SQLAlchemy 2.0, and Pydantic v2 — with real class-based views.**

> **Status:** public beta release ([changelog](https://github.com/rjprins/fastapi-restly/blob/main/CHANGELOG.md)).
>
> Restly is public after four years of internal use. The API is settling on the
> way to `1.0.0`; expect small breaking changes in deeper extension points.
> Feedback is welcome.

```bash
pip install "fastapi-restly[standard]" aiosqlite
```

**Docs:** <https://www.fastapi-restly.org/> ·
**[Changelog](https://github.com/rjprins/fastapi-restly/blob/main/CHANGELOG.md)** ·
**[Contributing](https://github.com/rjprins/fastapi-restly/blob/main/CONTRIBUTING.md)** ·
**[Security](https://github.com/rjprins/fastapi-restly/blob/main/SECURITY.md)** ·
**[Examples](https://github.com/rjprins/fastapi-restly/tree/main/example-projects)**

## Why FastAPI-Restly?

Restly turns SQLAlchemy models into FastAPI resources without hiding FastAPI.
Its class-based views are real Python classes: use inheritance, mixins, and
method overrides to share behavior across resources.

- **Class-based views**: group endpoints on Python classes with inheritance and method overrides.
- **REST endpoints in minutes**: use `View` for custom endpoint groups, or `AsyncRestView` / `RestView` for generated CRUD.
- **Incremental adoption**: use Restly per resource; drop to ordinary FastAPI when needed — see [Existing Project Integration](https://www.fastapi-restly.org/howto_existing_project.html).
- **Class-level dependencies**: declare shared dependencies once and read their values from `self`.
- **Explicit override points**: change the route shell, request handler, or business verb.
- **Filtering, pagination, sorting**: get schema-derived list parameters.
- **Field control**: `ReadOnly` / `WriteOnly` markers, plus foreign-key validation through `MustExist[...]`.
- **React Admin ready**: `AsyncReactAdminView` / `ReactAdminView` speak `ra-data-simple-rest`.
- **App utilities**: SQLAlchemy engine/session setup, exception handlers, and test fixtures.

## Quickstart

FastAPI-Restly turns a SQLAlchemy model into a class-based CRUD resource.
This example is complete and runnable; it creates SQLite tables at startup for
local development. Use Alembic migrations in production.

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
    await fr.db.async_create_all(Base)  # dev tables; use Alembic in production
    yield

app = FastAPI(lifespan=lifespan)

@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
```

That view exposes these HTTP routes:

```http
GET    /users/       # list users, with filtering, sorting, and pagination
POST   /users/       # create a user
GET    /users/{id}   # read one user
PATCH  /users/{id}   # partially update one user
DELETE /users/{id}   # delete one user
```

Restly generates the Pydantic schemas automatically.

The Quickstart uses plain SQLAlchemy on purpose — Restly doesn't hide it. In
real projects you'll usually inherit `fr.IDBase` for models and `fr.IDSchema`
for schemas, which supply the `id` for you; both appear in the examples below.

### Not just CRUD

`View` is the same class-based machinery without the generated routes — use it
to group related non-CRUD endpoints (auth flows, webhook receivers, actions):

```python
@fr.include_view(app)
class AuthView(fr.View):
    prefix = "/auth"
    tags = ["auth"]
    session: fr.AsyncSessionDep

    @fr.post("/login")
    async def login(self, credentials: LoginRequest) -> Token: ...

    @fr.post("/logout")
    async def logout(self) -> None: ...
```

And because views truly subclass, the biggest everyday win takes only a few lines:
declare your app's request context once on a base view, and read it from
`self` everywhere — instead of re-declaring the same `Depends` parameters on
every function in the project:

```python
class AppView(fr.View):
    session: fr.AsyncSessionDep
    current_user: Annotated[User, Depends(get_current_user)]

class ProfileView(AppView): ...          # custom endpoint groups
class AppRestView(AppView, fr.AsyncRestView): ...  # CRUD resources, same context
```

The rule of thumb: a plain FastAPI route for a one-off endpoint, `View` for a
group of related custom endpoints, `AsyncRestView` / `RestView` for a CRUD
resource, and `RestView` plus custom `@fr.post` methods for CRUD with actions.

Why a `View` over a bare `APIRouter`?

- Prefix, tags, responses, and dependencies are declared once on the class.
- Views compose: inheritance and mixins share behavior across endpoint groups.
- One registration call (`fr.include_view`) per class, bindable to any app or router.
- And when a group grows into a resource, `RestView` adds generated CRUD and lifecycle hooks on the same class shape.

## Installation

The install command at the top is the whole story: the `standard` extra brings
the `fastapi dev` toolchain, and `aiosqlite` is the async SQLite driver used in
the examples (Restly is driver-agnostic — use `asyncpg`/`psycopg` for
PostgreSQL). Details, including the `[testing]` extra, are in
[Getting Started](https://www.fastapi-restly.org/getting_started.html).

## Main features

### Manual schema definition

For custom validation, aliases, or stable public contracts, define an explicit read schema:

```python
from datetime import datetime

class UserRead(fr.IDSchema):
    name: str
    email: str
    password: fr.WriteOnly[str]
    created_at: fr.ReadOnly[datetime]

@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead
    # schema_create = UserCreate  # auto-generated from UserRead
    # schema_update = UserUpdate  # auto-generated from UserRead
```

Restly derives create and update schemas from `UserRead` by default.
The `UserCreate` schema is created by omitting `ReadOnly` fields.
The `UserUpdate` schema allows for partial updates by making all fields optional.

When you need full control over write payloads, declare them explicitly:

```python
class UserCreate(fr.BaseSchema):
    name: str
    email: str

class UserUpdate(fr.BaseSchema):
    name: str | None = None
    email: str | None = None

@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead
    schema_create = UserCreate
    schema_update = UserUpdate
```

Use **auto-schema** for prototypes and internal tools. Use an **explicit schema** for public contracts, aliases, and strict validation.

### List endpoint query parameters

List endpoints expose a stable URL parameter dialect generated from the response schema:

```bash
GET /users/?name=John&created_at__gte=2024-01-01
GET /users/?email__icontains=example
GET /users/?sort=-created_at&page=2&page_size=10
```

Parameter keys use the **response schema's public names**, including dotted
relation paths; unknown keys are rejected with `422`.

Pagination is opt-in: omitting `page_size` returns every matching row. For
public endpoints, set `default_page_size` and `max_page_size` on the view:

```python
class UserView(fr.AsyncRestView):
    default_page_size = 25
    max_page_size = 200
```

See [Filter, Sort, and Paginate Lists](https://www.fastapi-restly.org/howto_query_modifiers.html) for the full operator surface, alias rules, and pagination guidance.

### Read-only and write-only fields

`IDSchema` already provides a read-only `id`, so don't redeclare it unless you need to narrow the type.

```python
class UserRead(fr.IDSchema):
    name: str
    email: str
    password: fr.WriteOnly[str]        # stripped by to_response_schema()
    created_at: fr.ReadOnly[datetime]  # excluded from schema_create / schema_update
```

### Relationship handling

Validate a foreign-key column on create and update with `fr.MustExist[int, Model]`.
It keeps the plain id (`customer_id`) and checks the referenced row exists; declare the relationship (`customer`) separately when you also want the nested object.

```python
class Order(fr.IDBase):
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
    customer: Mapped[Customer] = relationship()

class OrderRead(fr.IDSchema):
    customer_id: fr.MustExist[int, Customer]
    customer: fr.ReadOnly[CustomerRead]
```

### Custom endpoints

Add custom routes with FastAPI-style decorators.

- `@fr.get`
- `@fr.post`
- `@fr.put`
- `@fr.patch`
- `@fr.delete`
- `@fr.route`

They forward keyword arguments to FastAPI's route registration.

```python
class UploadView(fr.AsyncRestView):
    prefix = "/uploads"
    model = Upload

    @fr.get(
        "/{id}/download",
        response_class=FileResponse,
        responses={200: {"content": {EXCEL_MIME_TYPE: {}}}},
    )
    async def download_excel(self, id: int):
        upload = await self.handle_get_one(id)
        return to_excel_response(upload)
```

### React Admin integration

Use `AsyncReactAdminView` (or `ReactAdminView` for a sync stack) for a
[react-admin](https://marmelab.com/react-admin/) backend compatible with
[`ra-data-simple-rest`](https://github.com/marmelab/react-admin/tree/master/packages/ra-data-simple-rest):

```python
@fr.include_view(app)
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product
    schema = ProductRead
```

The view speaks the `ra-data-simple-rest` wire contract.

See [React Admin Integration](https://www.fastapi-restly.org/howto_react_admin.html) in the docs for CORS setup and customization.

### Excluding built-in routes

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    exclude_routes = (fr.ViewRoute.DELETE,)
```

### Pagination metadata

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    include_pagination_metadata = True
    # Response: {"items": [...], "total": N, "page": 1, "page_size": 100, "total_pages": N, ...}
```

## Testing

`fastapi_restly.pytest_fixtures` provides client and session fixtures with
savepoint-based isolation. Restly registers them as a pytest plugin
automatically whenever it and pytest are installed.

Install the testing extra when consuming FastAPI-Restly as a package:

```bash
pip install "fastapi-restly[testing]"
```

Configure Restly for your test database in `conftest.py`.

`RestlyTestClient` asserts the expected status (`200` for GET, `201` for POST,
`204` for DELETE, ...) and includes the response body on failure:

```python
# test_users.py
def test_create_and_fetch_user(restly_client):
    # Raises AssertionError if status != 201
    response = restly_client.post("/users/", json={"name": "John", "email": "john@example.com"})
    user_id = response.json()["id"]

    # Raises AssertionError if status != 200
    data = restly_client.get(f"/users/{user_id}").json()
    assert data["name"] == "John"
```

Pass `assert_status_code=None` to relax the check to any success status (2xx/3xx).
To inspect an error response, assert its exact code instead (e.g. `assert_status_code=422`).

## Configuration

```python
# Async SQLite
fr.configure(async_database_url="sqlite+aiosqlite:///app.db")

# Async PostgreSQL
fr.configure(async_database_url="postgresql+asyncpg://user:pass@localhost/db")

# Sync SQLite
fr.configure(database_url="sqlite:///app.db")

# Or hand Restly the engine you already have — it does not need to own it
fr.configure(async_engine=existing_engine)
```

Restly has one public process-wide configuration. For per-view databases, read
replicas, or custom sessions, use a normal FastAPI dependency on that view.
One rule to know up front: **Restly owns the commit on its views** — custom
session generators construct and clean up, but never commit.
For wiring Restly into an existing app's engine, sessions, and models, see
[Existing Project Integration](https://www.fastapi-restly.org/howto_existing_project.html).

## Documentation

- **[Getting Started](https://www.fastapi-restly.org/getting_started.html)** — fast path from zero to a working API
- **[Class-Based Views](https://www.fastapi-restly.org/class_based_views.html)** — what "real class-based views" means, and when to use `View` vs `RestView`
- **[The Handle Design](https://www.fastapi-restly.org/the_handle_design.html)** — the three tiers behind every CRUD verb, and which one to override
- **[User Guide](https://www.fastapi-restly.org/user_guide.html)** — tutorial walkthroughs and topic guides
- **[API Reference](https://www.fastapi-restly.org/api_reference.html)** — complete API docs

## Examples

Complete applications under [`example-projects/`](https://github.com/rjprins/fastapi-restly/tree/main/example-projects):

- **[Shop](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/shop)** — e-commerce API with products, orders, customers
- **[Blog](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/blog)** — minimal blog with a single `Blog` model
- **[SaaS](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/saas)** — multi-tenant project management API

## Contributing

Pull requests and issue discussions welcome. See [CONTRIBUTING.md](https://github.com/rjprins/fastapi-restly/blob/main/CONTRIBUTING.md)
for setup and tests. For security issues, see [SECURITY.md](https://github.com/rjprins/fastapi-restly/blob/main/SECURITY.md).

## License

MIT — see [LICENSE](https://github.com/rjprins/fastapi-restly/blob/main/LICENSE).
