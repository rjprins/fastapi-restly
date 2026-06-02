# FastAPI-Restly

[![CI](https://github.com/rjprins/fastapi-restly/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/rjprins/fastapi-restly/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/rjprins/fastapi-restly/blob/main/pyproject.toml)
[![License](https://img.shields.io/github/license/rjprins/fastapi-restly)](https://github.com/rjprins/fastapi-restly/blob/main/LICENSE)
[![Coverage](https://rjprins.github.io/fastapi-restly/coverage/badge.svg)](https://rjprins.github.io/fastapi-restly/coverage/)

<p align="center">
  <img src="https://raw.githubusercontent.com/rjprins/fastapi-restly/main/docs/_static/restly-cat-white-bg.png" alt="FastAPI-Restly logo" width="200">
</p>

**Build maintainable REST APIs on FastAPI, SQLAlchemy 2.0, and Pydantic v2 — with real class-based views.**

> **Status:** `0.6.0` — public beta release.
>
> Restly is public after four years of internal use. The API is settling on the
> way to `1.0.0`; expect small breaking changes in deeper extension points.
> Feedback is welcome.

```bash
pip install "fastapi-restly[standard]"
```

**Docs:** <https://rjprins.github.io/fastapi-restly/> · **[Changelog](CHANGELOG.md)** ·
**[Contributing](CONTRIBUTING.md)** · **[Security](SECURITY.md)** · **[Examples](example-projects/)**

## Why FastAPI-Restly?

Restly turns SQLAlchemy models into FastAPI resources without hiding FastAPI.
Its class-based views are real Python classes: use inheritance, mixins, and
method overrides to share behavior across resources.

- **Class-based views**: group endpoints on Python classes with inheritance and method overrides.
- **REST endpoints in minutes**: use `View` for custom resources, or `AsyncRestView` / `RestView` for generated CRUD.
- **Incremental adoption**: use Restly per resource; drop to ordinary FastAPI when needed.
- **Class-level dependencies**: declare shared dependencies once and read their values from `self`.
- **Explicit override points**: change the route shell, request handler, or business verb.
- **Filtering, pagination, sorting**: get schema-derived list parameters.
- **Field control**: `ReadOnly` / `WriteOnly` markers, plus foreign-key validation through `IDRef[...]`.
- **React Admin ready**: `AsyncReactAdminView` speaks `ra-data-simple-rest`.
- **App utilities**: SQLAlchemy engine/session setup, exception handlers, and test fixtures.

## Quickstart

FastAPI-Restly turns a SQLAlchemy model into a class-based CRUD resource:

```python
import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

app = FastAPI()

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    email: Mapped[str]

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

Restly generates the Pydantic schemas automatically. For the full copy-paste app see [Getting Started](docs/getting_started.md).

## Installation

```bash
pip install "fastapi-restly[standard]" aiosqlite
```

The `standard` extra mirrors `fastapi[standard]` (the `fastapi dev` server
toolchain). Restly is database-driver-agnostic, so install the async driver for
your database alongside it — `aiosqlite` for SQLite (used in the examples),
`asyncpg`/`psycopg` for PostgreSQL. Test tooling lives in a separate extra:
`pip install "fastapi-restly[testing]"`.

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
GET /users/?name=John&age__gte=21
GET /users/?status=active,pending           # comma-separated → OR (IN)
GET /users/?status__ne=archived,deleted     # comma-separated → NOT IN
GET /users/?email__icontains=example
GET /users/?deleted_at__isnull=true
GET /users/?sort=-created_at,name
GET /users/?page=2&page_size=10
```

Parameter keys use the **response schema's public names**, including dotted
relation paths. Aliases apply to each path segment: `writer.authorName`, not
`author.name`.

Pagination is opt-in: omitting `page_size` returns every matching row. For
public endpoints, set `default_page_size` and `max_page_size` on the view:

```python
class UserView(fr.AsyncRestView):
    default_page_size = 25
    max_page_size = 200
```

See [How-To: Filter, Sort, and Paginate Lists](docs/howto_query_modifiers.md) for the full operator surface, alias rules, and pagination guidance.

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

Validate relationships on create and update with `fr.IDRef[...]`.
Restly passes either the foreign key (`customer_id`) or the related object (`Customer`) to SQLAlchemy, depending on the model constructor.

```python
class Order(fr.IDBase):
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
    customer: Mapped[Customer] = relationship()

class OrderRead(fr.IDSchema):
    customer_id: fr.IDRef[Customer]
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

Use `AsyncReactAdminView` for a [react-admin](https://marmelab.com/react-admin/)
backend compatible with [`ra-data-simple-rest`](https://github.com/marmelab/react-admin/tree/master/packages/ra-data-simple-rest):

```python
@fr.include_view(app)
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product
    schema = ProductRead
```

The view speaks the `ra-data-simple-rest` wire contract.

See [React Admin Integration](https://rjprins.github.io/fastapi-restly/howto_react_admin.html) in the docs for CORS setup and customization.

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
savepoint-based isolation. The testing extra auto-loads them as a pytest plugin.

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

Pass `assert_status_code=None` to skip the assertion and inspect the response yourself.

## Configuration

```python
# Async SQLite
fr.configure(async_database_url="sqlite+aiosqlite:///app.db")

# Async PostgreSQL
fr.configure(async_database_url="postgresql+asyncpg://user:pass@localhost/db")

# Sync SQLite
fr.configure(database_url="sqlite:///app.db")
```

Restly has one public process-wide configuration. For per-view databases, read
replicas, or custom sessions, use a normal FastAPI dependency on that view.

## Documentation

- **[Getting Started](https://rjprins.github.io/fastapi-restly/getting_started.html)** — fast path from zero to a working API
- **[User Guide](https://rjprins.github.io/fastapi-restly/user_guide.html)** — tutorial walkthroughs and topic guides
- **[API Reference](https://rjprins.github.io/fastapi-restly/api_reference.html)** — complete API docs

## Examples

Complete applications under [`example-projects/`](example-projects/):

- **[Shop](example-projects/shop/)** — e-commerce API with products, orders, customers
- **[Blog](example-projects/blog/)** — minimal blog with a single `Blog` model
- **[SaaS](example-projects/saas/)** — multi-tenant project management API

## Contributing

Pull requests and issue discussions welcome. See [CONTRIBUTING.md](CONTRIBUTING.md)
for setup and tests. For security issues, see [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
