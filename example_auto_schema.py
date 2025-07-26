"""
Auto Schema Example

This example shows how you can create FastAPI-Ding views without explicitly
defining Pydantic schemas - they are automatically generated from SQLAlchemy models.
"""

import fastapi_ding as fa
from fastapi import FastAPI
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey
from datetime import datetime, timezone

# Setup database
fa.setup_async_database_connection("sqlite+aiosqlite:///example_auto_schema.db")

app = FastAPI()

# Define SQLAlchemy models - no schemas needed!
class User(fa.IDBase):
    """A user model with auto-generated schema."""
    name: Mapped[str]
    email: Mapped[str]
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))

class Category(fa.IDBase):
    """A category model with auto-generated schema."""
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")

class Product(fa.IDBase, fa.TimestampsMixin):
    """A product model with timestamps and auto-generated schema."""
    name: Mapped[str]
    price: Mapped[float]
    category_id: Mapped[int] = mapped_column(ForeignKey("category.id"))
    description: Mapped[str] = mapped_column(default="")
    in_stock: Mapped[bool] = mapped_column(default=True)

class Order(fa.IDBase):
    """An order model with auto-generated schema."""
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    total_amount: Mapped[float]
    status: Mapped[str] = mapped_column(default="pending")
    notes: Mapped[str] = mapped_column(default="")

# Create views with auto-generated schemas
@fa.include_view(app)
class UserView(fa.AsyncAlchemyView):
    """User view with auto-generated schema."""
    prefix = "/users"
    model = User
    # No schema specified - will be auto-generated!

@fa.include_view(app)
class CategoryView(fa.AsyncAlchemyView):
    """Category view with auto-generated schema."""
    prefix = "/categories"
    model = Category
    # No schema specified - will be auto-generated!

@fa.include_view(app)
class ProductView(fa.AsyncAlchemyView):
    """Product view with auto-generated schema."""
    prefix = "/products"
    model = Product
    # No schema specified - will be auto-generated!

@fa.include_view(app)
class OrderView(fa.AsyncAlchemyView):
    """Order view with auto-generated schema."""
    prefix = "/orders"
    model = Order
    # No schema specified - will be auto-generated!

# You can also manually generate schemas if needed
if __name__ == "__main__":
    # Example of manually generating a schema
    UserSchema = fa.create_schema_from_model(User)
    print(f"Auto-generated UserSchema fields: {list(UserSchema.model_fields.keys())}")
    
    # Example of generating a schema with custom name
    CustomProductSchema = fa.create_schema_from_model(
        Product, 
        schema_name="CustomProductSchema"
    )
    print(f"Custom ProductSchema fields: {list(CustomProductSchema.model_fields.keys())}")
    
    print("\nAuto-generated schemas include:")
    print("- All model fields with appropriate types")
    print("- Read-only fields (id, created_at, updated_at)")
    print("- Proper inheritance (IDSchema, TimestampsSchemaMixin)")
    print("- Optional fields with defaults")
    
    print("\nTo run the server:")
    print("uvicorn example_auto_schema:app --reload")
    print("Then visit http://localhost:8000/docs to see the auto-generated API docs!") 