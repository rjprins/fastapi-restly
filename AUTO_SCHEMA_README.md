# Auto-Generated Schemas

FastAPI-Alchemy now supports auto-generating Pydantic schemas from SQLAlchemy models, eliminating the need to manually define schemas for simple CRUD operations.

## How It Works

When you create an `AsyncAlchemyView` or `AlchemyView` without specifying a `schema`, FastAPI-Alchemy will automatically generate a Pydantic schema from your SQLAlchemy model.

### Basic Usage

```python
import fastapi_alchemy as fa
from fastapi import FastAPI
from sqlalchemy.orm import Mapped, mapped_column

# Setup database
fa.setup_async_database_connection("sqlite+aiosqlite:///app.db")

app = FastAPI()

# Define your SQLAlchemy model
class User(fa.IDBase):
    name: Mapped[str]
    email: Mapped[str]
    is_active: Mapped[bool] = mapped_column(default=True)

# Create view WITHOUT specifying a schema
@fa.include_view(app)
class UserView(fa.AsyncAlchemyView):
    prefix = "/users"
    model = User
    # No schema specified - will be auto-generated!
```

That's it! The schema will be automatically generated with:
- All model fields with appropriate types
- Read-only fields (id, created_at, updated_at) marked as read-only
- Proper inheritance (IDSchema, TimestampsSchemaMixin)
- Optional fields with defaults

### Advanced Features

#### Timestamps Support

If your model includes timestamp fields, they'll be automatically handled:

```python
class Product(fa.IDBase, fa.TimestampsMixin):
    name: Mapped[str]
    price: Mapped[float]
    # created_at and updated_at will be auto-generated and read-only
```

#### Manual Schema Generation

You can also manually generate schemas if needed:

```python
from fastapi_alchemy import create_schema_from_model

# Generate a schema manually
UserSchema = fa.create_schema_from_model(User)

# Generate with custom name
CustomUserSchema = fa.create_schema_from_model(
    User, 
    schema_name="CustomUserSchema"
)
```

#### Customization Options

The `create_schema_from_model` function supports several options:

```python
# Generate schema without relationships (to avoid circular references)
SimpleUserSchema = fa.create_schema_from_model(
    User,
    include_relationships=False
)

# Generate schema without read-only fields
WritableUserSchema = fa.create_schema_from_model(
    User,
    include_readonly_fields=False
)
```

### What Gets Auto-Generated

The auto-generated schemas include:

1. **Field Types**: SQLAlchemy types are mapped to appropriate Pydantic types
2. **Read-Only Fields**: `id`, `created_at`, `updated_at` are marked as read-only
3. **Inheritance**: Proper base classes (IDSchema, TimestampsSchemaMixin)
4. **Defaults**: Field defaults are preserved
5. **Optional Fields**: Fields with defaults become optional in the schema

### Type Mapping

Common SQLAlchemy types are automatically mapped:

| SQLAlchemy Type | Pydantic Type |
|----------------|---------------|
| `str` | `str` |
| `int` | `int` |
| `float` | `float` |
| `bool` | `bool` |
| `datetime` | `datetime` |
| `date` | `date` |
| `UUID` | `UUID` |

### Example: Complete Auto-Generated API

```python
import fastapi_alchemy as fa
from fastapi import FastAPI
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey

fa.setup_async_database_connection("sqlite+aiosqlite:///shop.db")
app = FastAPI()

# Models with auto-generated schemas
class Customer(fa.IDBase):
    email: Mapped[str]
    name: Mapped[str]

class Product(fa.IDBase, fa.TimestampsMixin):
    name: Mapped[str]
    price: Mapped[float]
    category_id: Mapped[int] = mapped_column(ForeignKey("category.id"))

class Category(fa.IDBase):
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")

# Views with auto-generated schemas
@fa.include_view(app)
class CustomerView(fa.AsyncAlchemyView):
    prefix = "/customers"
    model = Customer

@fa.include_view(app)
class ProductView(fa.AsyncAlchemyView):
    prefix = "/products"
    model = Product

@fa.include_view(app)
class CategoryView(fa.AsyncAlchemyView):
    prefix = "/categories"
    model = Category
```

This creates a complete CRUD API with:
- `GET /customers/` - List customers
- `POST /customers/` - Create customer
- `GET /customers/{id}` - Get customer
- `PUT /customers/{id}` - Update customer
- `DELETE /customers/{id}` - Delete customer

And the same for products and categories, all with auto-generated schemas!

### Benefits

1. **Less Boilerplate**: No need to manually define schemas for simple models
2. **Type Safety**: Auto-generated schemas maintain type safety
3. **Consistency**: Schemas automatically match your SQLAlchemy models
4. **Flexibility**: You can still manually define schemas when needed
5. **OpenAPI**: Auto-generated schemas work perfectly with FastAPI's OpenAPI generation

### When to Use Manual Schemas

You might still want to manually define schemas when you need:

- Custom validation logic
- Computed fields
- Complex relationships
- Different input/output schemas
- Custom serialization logic

The auto-generation feature is designed to handle the common case while still allowing full customization when needed. 