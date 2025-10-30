# FastAPI-Restly

> **⚠️ Development Status**: This project is in active development and has not been released on PyPI yet. For installation, please clone the repository and install in development mode.

FastAPI-Restly helps you build **maintainable CRUD APIs faster** on top of **FastAPI**, **SQLAlchemy 2.0**, and **Pydantic v2**.
It provides auto-generated endpoints, schemas, and filters while keeping you in control—perfect for projects where you want to move quickly without giving up flexibility.

---

## Why FastAPI-Restly?

* **Faster CRUD development** – Create endpoints for SQLAlchemy models by generating Pydantic models automatically.
* **Maintainable** – Class-based views with inheritance and dependencies to keep things organized.
* **Customizable** – Generated endpoints are fully overridable whenever you need custom behavior.
* **Modern stack** – Built for SQLAlchemy 2.0 and Pydantic v2, with async support.

---

## Current Features

* **CRUD endpoints in minutes** for SQLAlchemy models
* **Class-based views** with dependency injection and inheritance
* **Automatic Pydantic schemas** for create, update, and read
* **Filtering, pagination, and sorting** (including on nested relationships)
* **OpenAPI docs** with all generated routes

---

## Restly Philosophy

### Made to Build Apps

Restly is not only a REST framework, it aims to grow with the most common tools web apps need.

- **Current features**: Class-based views, automatic Pydantic schema generation, and database connection management.
- **Possible future features**: Admin interface, authentication, permissions, background jobs, job scheduling, plugins, etc.

### Designed in Layers

Restly is a stack of micro-libraries.

- Each layer adds a step of convenience, but developers can always drop down a layer for deeper customization.
- Each layer makes deliberate, opinionated choices that higher layers can rely on and extend.
- The less customization you need, the more you get out-of-the-box — yet full customization never requires awkward hacks.
- Built on FastAPI, Pydantic, and SQLAlchemy; Restly sticks to patterns provided by those libraries.

Current layers:
- SQLAlchemy connection management
- Class-based views supporting FastAPI dependencies
- Pydantic schema generation from SQLAlchemy models or other pydantic schemas.
- AlchemyView: CRUD view classes for SQLAlchemy models
- Pytest fixtures for SQLAlchemy, alembic, and AlchemyViews.

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/your-username/fastapi-restly.git
cd fastapi-restly

# Install in development mode
pip install -e .
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
# - PUT /users/{id} - Update a user
# - DELETE /users/{id} - Delete a user
```

The framework automatically generates the Pydantic schema from your SQLAlchemy model, so you don't need to write any schema definitions!

## Advanced Features

### Manual Schema Definition

If you need custom validation or field aliases, you can define schemas manually:

```python
class UserSchema(fr.IDSchema[User]):
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
GET /users/?sort=name,-created_at

# Pagination
GET /users/?page=2&page_size=10
```

### Read-Only and Write-Only Fields

```python
class UserSchema(pydantic.BaseModel):
    id: fr.ReadOnly[UUID]  # Can't be set in requests
    name: str
    email: str
    password: fr.WriteOnly[str]  # Won't appear in responses
    created_at: fr.ReadOnly[datetime]
```

### Relationships

```python
class Order(fr.IDBase):
    customer_id: Mapped[int] = Mapped(foreign_key="customer.id")
    total: Mapped[float]

class OrderSchema(fr.IDSchema[Order]):
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
    async def (self, q: str):
        """Custom search endpoint"""
        query = sqlalchemy.select(self.model).where(
            self.model.name.ilike(f"%{q}%")
        )
        result = await self.session.scalars(query)
        return result.all()

    async def process_index(self, query_params):
        """Override default list behavior"""
        # Custom logic here
        return await super().process_index(query_params)
```

## Documentation

- **[Tutorial](docs/tutorial.md)** - Get started with FastAPI-Restly
- **[Technical Details](docs/technical_details.md)** - Learn how the framework works
- **[API Reference](docs/api_reference.md)** - Complete API documentation

## Examples

Check out the [example projects](example-projects/) for complete applications:

- **[Shop](example-projects/shop/)** - E-commerce API with products, orders, and customers
- **[Blog](example-projects/blog/)** - Simple blog with posts and comments

## Testing

FastAPI-Restly includes testing utilities:

```python
import fastapi_restly as fr
from fastapi_restly.pytest_fixtures import client

def test_user_crud(client):
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

## Future Plans

* Authentication & permissions
* Admin interface
* Background jobs & scheduling
* CLI for code generation and scaffolrestly


## Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

**Built with ❤️ for the FastAPI community**
