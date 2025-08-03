# FastAPI-Ding 🚀

> **Build CRUD APIs with FastAPI and SQLAlchemy in minutes**

> **⚠️ Development Status**: This project is in active development and has not been released on PyPI yet. For installation, please clone the repository and install in development mode.

FastAPI-Ding is a powerful framework that eliminates boilerplate code when building REST APIs with FastAPI and SQLAlchemy. Define your models and schemas, and get a fully functional API with automatic CRUD operations, validation, filtering, sorting, and OpenAPI documentation.
Designed to be fully extendable and customizable.

## ✨ Features

- **⚡ Class-based Views**: With class-level FastAPI dependencies, and full inheritance support
- **🚀 Instant CRUD**: Automatic endpoints for Create, Read, Update, Delete operations
- **🔄 Pydantic Model Generation**: Automatically create Pydantic models from SQLAlchemy ORM classes
- **🔍 Advanced Querying**: Built-in pagination, sorting and filtering, including relational fields (blog.author.name)
- **🎯 React-Admin Integration**: Zero-config support for React-Admin on CRUD views

Each feature is always made with the assumption that you want to customize it. 
FastAPI, Pydantic, and SQLAlchemy are combined with full functionality: Complex relationships, nested schemas, OpenAPI specification generation are all supported.

## 🎯 Ding Philosophy

### Made to Build Serious Apps

Ding's goal is to make web application development as easy as possible by providing the tools production apps commonly need.

- **Today**: Class-based views, automatic Pydantic schema generation, and database connection management.
- **Tomorrow**: Admin interface, authentication, permissions, background jobs, job scheduling, plugins, etc.

### Designed in Layers

Ding is a stack of micro-libraries.

- Each layer adds a step of convenience, but developers can always drop down a layer for deeper customization.
- Each layer makes deliberate, opinionated choices that higher layers can rely on and extend.
- The less customization you need, the more you get out-of-the-box—yet full customization never requires awkward hacks.
- Built on FastAPI, Pydantic, and SQLAlchemy, Ding sticks to patterns provided by those libraries.

## 🚀 Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/your-username/fastapi-ding.git
cd fastapi-ding

# Install in development mode
pip install -e .
```

### Basic Example

```python
import fastapi_ding as fd
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

# Setup database
fd.setup_async_database_connection("sqlite+aiosqlite:///app.db")

app = FastAPI()

# Define your model
class User(fd.IDBase):
    name: Mapped[str]
    email: Mapped[str]
    age: Mapped[int]

# Create instant CRUD endpoints with auto-generated schema
@fd.include_view(app)
class UserView(fd.AsyncAlchemyView):
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

## 🎯 Advanced Features

### Manual Schema Definition

If you need custom validation or field aliases, you can define schemas manually:

```python
class UserSchema(fd.IDSchema[User]):
    name: str
    email: str
    age: int
    # Field-level read-only
    internal_id: fd.ReadOnly[str]

@fd.include_view(app)
class UserView(fd.AsyncAlchemyView):
    prefix = "/users"
    model = User
    schema = UserSchema  # Use custom schema
```

### Query Modifiers

FastAPI-Ding currently supports two query parameter interfaces:

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
    id: fd.ReadOnly[UUID]  # Can't be set in requests
    name: str
    email: str
    password: fd.WriteOnly[str]  # Won't appear in responses
    created_at: fd.ReadOnly[datetime]
```

### Relationships

```python
class Order(fd.IDBase):
    customer_id: Mapped[int] = Mapped(foreign_key="customer.id")
    total: Mapped[float]

class OrderSchema(fd.IDSchema[Order]):
    customer: CustomerSchema  # Nested object
    customer_id: fd.IDSchema[Customer]  # Just the ID
    total: float
```

### Custom Endpoints

```python
@fd.include_view(app)
class UserView(fd.AsyncAlchemyView):
    prefix = "/users"
    model = User
    schema = UserSchema

    @fd.get("/{id}/download")
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

## 📚 Documentation

- **[Tutorial](docs/tutorial.md)** - Get started with FastAPI-Ding
- **[Technical Details](docs/technical_details.md)** - Learn how the framework works
- **[API Reference](docs/api_reference.md)** - Complete API documentation

## 🛠️ Examples

Check out the [example projects](example-projects/) for complete applications:

- **[Shop](example-projects/shop/)** - E-commerce API with products, orders, and customers
- **[Blog](example-projects/blog/)** - Simple blog with posts and comments

## 🧪 Testing

FastAPI-Ding includes testing utilities:

```python
import fastapi_ding as fd
from fastapi_ding.pytest_fixtures import client

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

## 🔧 Configuration

### Database Setup

```python
# Async SQLite
fd.setup_async_database_connection("sqlite+aiosqlite:///app.db")

# Async PostgreSQL
fd.setup_async_database_connection("postgresql+asyncpg://user:pass@localhost/db")

# Sync SQLite
fd.setup_database_connection("sqlite:///app.db")
```

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

**Built with ❤️ for the FastAPI community**
