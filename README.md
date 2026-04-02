# FastAPI-Restly

[![CI](https://github.com/rjprins/fastapi-restly/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/rjprins/fastapi-restly/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/rjprins/fastapi-restly/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/rjprins/fastapi-restly/blob/main/LICENSE)
[![Coverage](https://rjprins.github.io/fastapi-restly/coverage/badge.svg)](https://rjprins.github.io/fastapi-restly/coverage/)

<p align="center">
  <img src="docs/_static/restly-cat.png" alt="FastAPI-Restly logo" width="200">
</p>

> **Warning**: This project is in active development and has not been released on PyPI yet. For installation, please clone the repository and install in development mode.

**Documentation:** https://rjprins.github.io/fastapi-restly/

FastAPI-Restly helps you build **maintainable CRUD APIs faster** on top of **FastAPI**, **SQLAlchemy 2.0**, and **Pydantic v2**.
It provides auto-generated endpoints, schemas, and filters while keeping everything extensible or customizable.

FastAPI-Restly implements **true class-based views** — real Python classes that support inheritance and method overrides. Share common behavior across views by subclassing, and override individual CRUD methods without touching the rest.

## Why FastAPI-Restly?

* **CRUD endpoints in minutes** – Create endpoints for SQLAlchemy models with auto-generated Pydantic schemas.
* **True class-based views** – Real inheritance and method overrides, not just decorator wrappers. Share logic across views by subclassing.
* **Customizable** – Override any CRUD endpoint or just its business logic, without awkward hacks.
* **Modern stack** – Built for SQLAlchemy 2.0 and Pydantic v2, with async and sync support.
* **Filtering, pagination, and sorting** – Two query parameter interfaces (JSONAPI-style and standard HTTP).
* **Field control** – `ReadOnly` and `WriteOnly` field markers, plus relationship ID resolution via `IDSchema[...]`.
* **Testing utilities** – `RestlyTestClient` and savepoint-based isolation fixtures for clean, fast tests.

## Philosophy

Restly is a stack of micro-libraries. Each layer adds convenience while letting you drop down for deeper control. The less customization you need, the more you get out-of-the-box — full customization never requires awkward hacks. Restly stays close to patterns already provided by FastAPI, Pydantic, and SQLAlchemy.

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/rjprins/fastapi-restly.git
cd fastapi-restly

# Install project dependencies
uv sync
```

### Basic Example

```python
import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Mapped

fr.configure(async_database_url="sqlite+aiosqlite:///app.db")

# Create tables — dev/SQLite only; use Alembic migrations in production
fr.DataclassBase.metadata.create_all(create_engine("sqlite:///app.db"))

app = FastAPI()


# Define your model — IDBase adds an auto-incrementing integer id.
# Use IDStampsBase to also get created_at / updated_at timestamps.
class User(fr.IDBase):
    name: Mapped[str]
    email: Mapped[str]
    age: Mapped[int]


# Create instant CRUD endpoints with auto-generated schema.
# Use RestView instead of AsyncRestView for sync SQLAlchemy.
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    # Schema is auto-generated from the model!


# That's it! You now have a fully functional API with:
# - GET /users/ - List all users, comes with complete filtering and pagination
# - POST /users/ - Create a user
# - GET /users/{id} - Get a specific user
# - PATCH /users/{id} - Partially update a user
# - DELETE /users/{id} - Delete a user
```

The framework automatically generates the Pydantic schema from your SQLAlchemy model, so you don't need to write any schema definitions!

### Auto-schema vs Explicit schema

Use **auto-schema** when speed matters most (prototyping, internal tools, simple models).
Use an **explicit schema** when contract stability and control matter most (public APIs, custom validation, aliases, strict response shapes).

## Advanced Features

### Manual Schema Definition

If you need custom validation or field aliases, you can define schemas manually:

```python
class UserSchema(fr.IDSchema):
    name: str
    email: str
    age: int
    # Field-level read-only
    internal_id: fr.ReadOnly[str]

@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema  # Use custom schema
```

### Query Modifiers

FastAPI-Restly supports two query parameter interfaces:

**V1 (JSONAPI-style):**
```bash
# Filtering
GET /users/?filter[name]=John&filter[age]=>21

# Sorting
GET /users/?sort=name,-created_at

# Pagination
GET /users/?limit=10&offset=20

# Contains search
GET /users/?contains[name]=john
```

**V2 (Standard HTTP):**
```bash
# Filtering
GET /users/?name=John&email__contains=example

# Sorting
GET /users/?order_by=name,-created_at

# Pagination
GET /users/?page=2&page_size=10
```

Notes:
- V1 query parameters use schema field names, not aliases.
- V2 query parameters use schema aliases for flat fields. If `populate_by_name=True` is enabled, flat fields also accept the Python field name.
- For V2 relation filters, keep the relation path segment as the schema/model field name and only use aliases for nested fields. Example: `author.authorName=Alice`, not `writer.authorName=Alice`.

### Read-Only and Write-Only Fields

`IDSchema` already provides a read-only `id` field, so you don't need to redeclare it unless you want to narrow the type.

```python
class UserSchema(fr.IDSchema):
    # id is inherited from IDSchema as ReadOnly[Any]
    name: str
    email: str
    password: fr.WriteOnly[str]       # Won't appear in responses
    created_at: fr.ReadOnly[datetime]  # Can't be set in requests
```

### Relationships

```python
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

class Order(fr.IDBase):
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
    total: Mapped[float]

class OrderSchema(fr.IDSchema):
    customer: CustomerSchema  # Nested object
    customer_id: fr.IDSchema[Customer]  # Wire format: {"id": 123} — the framework resolves it to the FK value
    total: float
```

### Custom Endpoints

Add extra endpoints using `@fr.get`, `@fr.post`, `@fr.put`, `@fr.patch`, `@fr.delete`, or the generic `@fr.route`. Override `on_*` hooks (e.g. `on_list`, `on_get`) to customise built-in CRUD logic without replacing the whole endpoint.

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema

    @fr.get("/{id}/download")
    async def download_user(self, id: int):
        """Custom extra endpoint"""
        return {"id": id, "status": "ok"}

    async def on_list(self, query_params, query=None):
        """Override default list behavior"""
        # Custom logic here
        return await super().on_list(query_params, query=query)
```

### Excluding Built-in Routes

Disable any of the default CRUD endpoints with `exclude_routes`:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    exclude_routes = ("delete",)  # Names: "index", "get", "post", "patch", "delete"
```

### Pagination Metadata

Set `include_pagination_metadata = True` to wrap list responses with count and page information:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    include_pagination_metadata = True
    # Response shape: {"items": [...], "total": N, "page": 1, "page_size": 100, "total_pages": N, ...}
```

## Documentation

- **[Getting Started](https://rjprins.github.io/fastapi-restly/getting_started.html)** - Fast path from zero to a working API
- **[Tutorial](https://rjprins.github.io/fastapi-restly/tutorial.html)** - Get started with FastAPI-Restly
- **[How-To Guides](https://rjprins.github.io/fastapi-restly/howto.html)** - Recipes for common framework tasks
- **[Existing Project Integration](https://rjprins.github.io/fastapi-restly/howto_existing_project.html)** - Add Restly to a project with its own session management
- **[Technical Details](https://rjprins.github.io/fastapi-restly/technical_details.html)** - Learn how the framework works
- **[API Reference](https://rjprins.github.io/fastapi-restly/api_reference.html)** - Complete API documentation

## Examples

Check out the [example projects](example-projects/) for complete applications:

- **[Shop](example-projects/shop/)** - E-commerce API with products, orders, and customers
- **[Blog](example-projects/blog/)** - Minimal blog with a single `Blog` model
- **[SaaS](example-projects/saas/)** - Multi-tenant project management API

## Testing

`fastapi_restly.testing` provides pytest fixtures (`app`, `client`, `async_session`, `session`) with **savepoint-based isolation** — each test runs inside a database transaction that rolls back automatically, so no data leaks between tests. Add them to your project's `conftest.py`:

```python
# conftest.py
import fastapi_restly as fr

pytest_plugins = ["fastapi_restly.pytest_fixtures"]

fr.configure(async_database_url="sqlite+aiosqlite:///test.db")
```

`RestlyTestClient` automatically asserts the expected HTTP status code on every call (`200` for GET, `201` for POST, `204` for DELETE, etc.) and raises a descriptive `AssertionError` with the response body on failure — no manual status checks needed.

```python
# test_users.py
def test_create_and_fetch_user(client):
    # Raises AssertionError if status != 201
    response = client.post("/users/", json={"name": "John", "email": "john@example.com"})
    user_id = response.json()["id"]

    # Raises AssertionError if status != 200
    data = client.get(f"/users/{user_id}").json()
    assert data["name"] == "John"
```

Pass `assert_status_code=None` to skip the assertion and inspect the response yourself.

## Configuration

### Database Setup

```python
# Async SQLite
fr.configure(async_database_url="sqlite+aiosqlite:///app.db")

# Async PostgreSQL
fr.configure(async_database_url="postgresql+asyncpg://user:pass@localhost/db")

# Sync SQLite
fr.configure(database_url="sqlite:///app.db")
```

## Contributing

We welcome contributions through pull requests and issue discussions.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
