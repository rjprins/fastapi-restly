# FastAPI-Restly

[![CI](https://github.com/rjprins/fastapi-restly/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/rjprins/fastapi-restly/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/rjprins/fastapi-restly/blob/main/pyproject.toml)
[![License](https://img.shields.io/github/license/rjprins/fastapi-restly)](https://github.com/rjprins/fastapi-restly/blob/main/LICENSE)
[![Coverage](https://rjprins.github.io/fastapi-restly/coverage/badge.svg)](https://rjprins.github.io/fastapi-restly/coverage/)

<p align="center">
  <img src="https://raw.githubusercontent.com/rjprins/fastapi-restly/main/docs/_static/restly-cat-white-bg.png" alt="FastAPI-Restly logo" width="200">
</p>

**Build maintainable REST APIs on FastAPI, SQLAlchemy 2.0, and Pydantic v2 — with real class-based views.**

> **Status:** `0.5.1` — public beta release.
>
> After four years of internal development at two separate companies, Restly is finally ready for its first public release! Right now the goal is to see if the public API of Restly hits the right abstractions, and to stabilize the API for a `1.0.0` release. From `0.5.0` onwards, expect small breaking changes in naming and functionality on the deeper parts of the API surface. Feedback is always appreciated!

```bash
pip install "fastapi-restly[standard]"
```

**Docs:** <https://rjprins.github.io/fastapi-restly/> · **[Changelog](CHANGELOG.md)** · **[Contributing](CONTRIBUTING.md)** · **[Security](SECURITY.md)** · **[Examples](example-projects/)**

## Why FastAPI-Restly?

Restly helps building FastAPI apps faster, with consistent APIs.

It features **class-based views** that support inheritance, mixins, and method overrides.

Class-based views are essential for re-using code. The `RestView` and `AsyncRestView` provide full CRUD on top of a SQLAlchemy model with a single class declaration. It stays fully customizable by overriding endpoints, `handle_*` request handlers, the business verbs, and other class methods.

- **class-based views**: group endpoints on real Python classes with inheritance and method overrides.
- **REST endpoints in minutes**: use `View` for custom resources, or `AsyncRestView` / `RestView` for generated CRUD.
- **Incremental adoption**: Restly doesn't get in your way; use it per resource and step out anytime. See [Existing Projects](docs/howto_existing_project.md).
- **Class-level dependencies**: Put dependencies that all endpoints need on the class level, and get them as attributes on `self`.
- **Explicit override points**: Call-chain allows for overriding at multiple levels.
- **Filtering, pagination, sorting**: Get fully-featured list routes specific to your Pydantic schema.
- **Field control**: `ReadOnly` / `WriteOnly` markers, plus foreign-key validation in Pydantic schemas via `IDRef[...]`.
- **React Admin ready**: `AsyncReactAdminView` speaks the `ra-data-simple-rest` wire contract, no custom data provider needed.
- **General app utilities**: Things most FastAPI apps will need: SQLAlchemy engine and session setup, alembic test fixtures, etc.

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
pip install "fastapi-restly[standard]"
```

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

Use **auto-schema** for prototypes and internal tools. Use an **explicit schema** when contract stability and validation control matter (public APIs, aliases, strict response shapes).

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

Parameter keys follow the **response schema's public names** end-to-end — including dotted relation paths. If `ArticleRead.author` has `Field(alias="writer")` and `AuthorRead.name` has `Field(alias="authorName")`, the URL key is `writer.authorName`. Aliased fields are only reachable by their alias; `populate_by_name` does not extend the URL surface with the Python field name.

Pagination is opt-in: omitting `page_size` returns every matching row. For public/production endpoints set `default_page_size` and `max_page_size` on the view class:

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

Validate relationships on create and update using `fr.IDRef[...]`.
SQLAlchemy init is handled smartly; init is with either the foreign key (`customer_id`) or the related object (`Customer`), whichever is in the signature of the SQLAlchemy mapper `__init__`.

```python
class Order(fr.IDBase):
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
    customer: Mapped[Customer] = relationship()

class OrderRead(fr.IDSchema):
    customer_id: fr.IDRef[Customer]
    customer: fr.ReadOnly[CustomerRead]
```

### Custom endpoints

Add custom routes using the same form of decorators you would use for regular FastAPI routes.

- `@fr.get`
- `@fr.post`
- `@fr.put`
- `@fr.patch`
- `@fr.delete`
- `@fr.route`

These simply forward all arguments to their standard FastAPI counterparts.

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

Use `AsyncReactAdminView` to get a backend that [react-admin](https://marmelab.com/react-admin/) with [`ra-data-simple-rest`](https://github.com/marmelab/react-admin/tree/master/packages/ra-data-simple-rest) connects to out of the box:

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

`fastapi_restly.pytest_fixtures` provides namespaced pytest fixtures (`restly_app`, `restly_client`, `restly_async_session`, `restly_session`) for test clients and **savepoint-based isolation**. The testing extra installs a pytest plugin entry point, so pytest auto-loads these fixtures.

Install the testing extra when consuming FastAPI-Restly as a package:

```bash
pip install "fastapi-restly[testing]"
```

Configure Restly for your test database in `conftest.py`.

`RestlyTestClient` automatically asserts the expected HTTP status (`200` for GET, `201` for POST, `204` for DELETE, ...) and raises a descriptive `AssertionError` with the response body on failure:

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

Restly has one public process-wide configuration. For per-view databases, read replicas, or other custom session wiring, use a normal FastAPI dependency on that view; see the existing-project how-to in the documentation.

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

Pull requests and issue discussions welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, coding standards, and the test workflow. Security issues: see [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
