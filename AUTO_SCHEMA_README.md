# Auto-Generated Schemas

FastAPI-Ding now supports auto-generating Pydantic schemas from SQLAlchemy models, eliminating the need to manually define schemas for simple CRUD operations.

## Overview

When you create an `AsyncAlchemyView` or `AlchemyView` without specifying a `schema`, FastAPI-Ding will automatically generate a Pydantic schema from your SQLAlchemy model.

## Basic Usage

```python
import fastapi_ding as fd
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

app = FastAPI()

# Define your model
class User(fd.IDBase):
    name: Mapped[str]
    email: Mapped[str]
    is_active: Mapped[bool] = fd.mapped_column(default=True)

# Create view WITHOUT specifying a schema - it will be auto-generated
@fd.include_view(app)
class UserView(fd.AsyncAlchemyView):
    prefix = "/users"
    model = User
    # No schema needed - auto-generated!

# Your API is ready!
```

## Manual Schema Generation

You can also manually generate schemas if needed:

```python
from fastapi_ding import create_schema_from_model

# Generate schema manually
UserSchema = create_schema_from_model(User)

# Generate with custom name
CustomUserSchema = create_schema_from_model(
    User, 
    schema_name="CustomUserSchema"
)
```

## Advanced Features

### Read-Only Fields

Auto-generated schemas properly handle read-only fields:

```python
class Product(fd.IDBase, fd.TimestampsMixin):
    name: Mapped[str]
    price: Mapped[float]
    description: Mapped[str] = fd.mapped_column(default="")

# Auto-generated schema will have:
# - id: read-only (from IDSchema)
# - created_at: read-only (from TimestampsMixin)
# - updated_at: read-only (from TimestampsMixin)
# - name, price, description: editable
```

### Custom Field Types

You can use `fd.ReadOnly` for custom read-only fields:

```python
class User(fd.IDBase):
    name: Mapped[str]
    email: Mapped[str]
    internal_id: fd.ReadOnly[str]  # This will be read-only in the schema
```

## Type Mapping

SQLAlchemy types are automatically mapped to Pydantic types:

| SQLAlchemy Type | Pydantic Type |
|----------------|---------------|
| `str` | `str` |
| `int` | `int` |
| `float` | `float` |
| `bool` | `bool` |
| `datetime` | `datetime` |
| `date` | `date` |
| `UUID` | `UUID` |

## Benefits

1. **Reduced Boilerplate**: No need to manually define schemas for simple models
2. **Type Safety**: Auto-generated schemas maintain full type safety
3. **Consistency**: Schemas automatically match SQLAlchemy models
4. **Flexibility**: Users can still manually define schemas when needed
5. **OpenAPI Integration**: Auto-generated schemas work perfectly with FastAPI's OpenAPI generation

## Complete Example

```python
import fastapi_ding as fd
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

# Setup
fd.setup_async_database_connection("sqlite+aiosqlite:///app.db")
app = FastAPI()

# Models
class User(fd.IDBase):
    name: Mapped[str]
    email: Mapped[str]
    is_active: Mapped[bool] = fd.mapped_column(default=True)

class Product(fd.IDBase, fd.TimestampsMixin):
    name: Mapped[str]
    price: Mapped[float]
    description: Mapped[str] = fd.mapped_column(default="")

# Views with auto-generated schemas
@fd.include_view(app)
class UserView(fd.AsyncAlchemyView):
    prefix = "/users"
    model = User

@fd.include_view(app)
class ProductView(fd.AsyncAlchemyView):
    prefix = "/products"
    model = Product

# Your API is ready with full CRUD operations!
```

This feature makes FastAPI-Ding even more user-friendly by eliminating the need for manual schema definition in most cases. 