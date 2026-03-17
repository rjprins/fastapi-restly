<p align="center">
  <img src="docs/_static/restly-cat.png" alt="FastAPI-Restly logo" width="200">
</p>

# FastAPI-Restly

> **Warning**: This project is in active development and has not been released on PyPI yet. For installation, please clone the repository and install in development mode.

**Documentation:** https://rjprins.github.io/fastapi-restly/

FastAPI-Restly helps you build **maintainable CRUD APIs faster** on top of **FastAPI**, **SQLAlchemy 2.0**, and **Pydantic v2**.
It provides auto-generated endpoints, schemas, and filters while keeping everything extensible or customizable.

## Why FastAPI-Restly?

* **CRUD endpoints in minutes** – Create endpoints for SQLAlchemy models with auto-generated Pydantic schemas.
* **Maintainable** – Class-based views with dependency injection and inheritance to keep things organized.
* **Customizable** – Generated endpoints are fully overridable whenever you need custom behavior.
* **Modern stack** – Built for SQLAlchemy 2.0 and Pydantic v2, with async and sync support.
* **Filtering, pagination, and sorting** – Two query parameter interfaces (JSONAPI-style and standard HTTP).
* **Field control** – `ReadOnly` and `WriteOnly` field markers, plus relationship ID resolution via `IDSchema[...]`.
* **Testing utilities** – `RestlyTestClient` and savepoint-based isolation fixtures for clean, fast tests.

## Restly Philosophy

### Made to Build Apps

Restly is not only a REST framework, it aims to grow with the most common tools web apps need.

### Designed in Layers

Restly is a stack of micro-libraries.

- Each layer adds a step of convenience, but developers can always drop down a layer for deeper customization.
- Each layer makes deliberate, opinionated choices that higher layers can rely on and extend.
- The less customization you need, the more you get out-of-the-box — yet full customization never requires awkward hacks.
- Built on FastAPI, Pydantic, and SQLAlchemy; Restly sticks to patterns provided by those libraries.

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
from sqlalchemy.orm import Mapped

# Setup database
fr.setup_async_database_connection("sqlite+aiosqlite:///app.db")

app = FastAPI()

# Define your model
class User(fr.IDBase):
    name: Mapped[str]
    email: Mapped[str]
    age: Mapped[int]

# Create instant CRUD endpoints with auto-generated schema
@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
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
class UserView(fr.AsyncAlchemyView):
    prefix = "/users"
    model = User
    schema = UserSchema  # Use custom schema
```

### Query Modifiers

FastAPI-Restly currently supports two query parameter interfaces:

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

### Read-Only and Write-Only Fields

```python
class UserSchema(fr.IDSchema):
    id: fr.ReadOnly[UUID]  # Can't be set in requests
    name: str
    email: str
    password: fr.WriteOnly[str]  # Won't appear in responses
    created_at: fr.ReadOnly[datetime]
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
    customer_id: fr.IDSchema[Customer]  # Just the ID
    total: float
```

### Custom Endpoints

```python
@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
    prefix = "/users"
    model = User
    schema = UserSchema

    @fr.get("/{id}/download")
    async def download_user(self, id: int):
        """Custom endpoint"""
        return {"id": id, "status": "ok"}

    async def process_index(self, query_params):
        """Override default list behavior"""
        # Custom logic here
        return await super().process_index(query_params)
```

## Documentation

- **[Getting Started](https://rjprins.github.io/fastapi-restly/getting_started.html)** - Fast path from zero to a working API
- **[Tutorial](https://rjprins.github.io/fastapi-restly/tutorial.html)** - Get started with FastAPI-Restly
- **[How-To Guides](https://rjprins.github.io/fastapi-restly/howto.html)** - Recipes for common framework tasks
- **[Technical Details](https://rjprins.github.io/fastapi-restly/technical_details.html)** - Learn how the framework works
- **[API Reference](https://rjprins.github.io/fastapi-restly/api_reference.html)** - Complete API documentation

## Examples

Check out the [example projects](example-projects/) for complete applications:

- **[Shop](example-projects/shop/)** - E-commerce API with products, orders, and customers
- **[Blog](example-projects/blog/)** - Simple blog with posts and comments
- **[SaaS](example-projects/saas/)** - Multi-tenant project management API

## Testing

FastAPI-Restly includes testing utilities with **savepoint-based isolation**, so each test runs inside a database transaction that is rolled back automatically — no test data leaks between tests.

```python
import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient
from fastapi import FastAPI

app = FastAPI()
client = RestlyTestClient(app)

def test_user_crud():
    # Create user
    response = client.post("/users/", json={"name": "John", "email": "john@example.com"})
    assert response.status_code == 201

    # Get user
    user_id = response.json()["id"]
    response = client.get(f"/users/{user_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "John"
```

## Configuration

### Database Setup

```python
# Async SQLite
fr.setup_async_database_connection("sqlite+aiosqlite:///app.db")

# Async PostgreSQL
fr.setup_async_database_connection("postgresql+asyncpg://user:pass@localhost/db")

# Sync SQLite
fr.setup_database_connection("sqlite:///app.db")
```

## Contributing

We welcome contributions through pull requests and issue discussions.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
