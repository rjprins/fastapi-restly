"""Test auto-generated schemas."""

import asyncio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy.orm import Mapped

import fastapi_ding as fd
from fastapi_ding._globals import fa_globals


def reset_metadata():
    """Reset SQLAlchemy metadata to prevent table redefinition conflicts."""
    if hasattr(fa_globals, 'metadata'):
        fa_globals.metadata.clear()
    fd.SQLBase.metadata.clear()


def test_auto_generated_schema_in_view():
    """Test that schemas are auto-generated when not specified in views."""
    reset_metadata()
    fd.setup_async_database_connection("sqlite+aiosqlite:///:memory:")
    
    app = FastAPI()
    
    # Define a simple model without manually creating a schema
    class User(fd.IDBase):
        name: Mapped[str]
        email: Mapped[str]
        is_active: Mapped[bool] = fd.mapped_column(default=True)
    
    # Create view WITHOUT specifying a schema - it should be auto-generated
    @fd.include_view(app)
    class UserView(fd.AsyncAlchemyView):
        prefix = "/users"
        model = User
        # No schema specified - should be auto-generated!
    
    # Create tables
    async def create_tables():
        engine = fa_globals.async_make_session.kw["bind"]
        async with engine.begin() as conn:
            await conn.run_sync(fd.SQLBase.metadata.create_all)
    
    asyncio.run(create_tables())
    
    client = TestClient(app)
    
    # Test that the API is working
    response = client.get("/openapi.json")
    assert response.status_code == 200
    
    spec = response.json()
    paths = spec["paths"]
    
    # Check that endpoints exist
    assert "/users/" in paths
    assert "/users/{id}" in paths
    
    # Test creating a user
    user_data = {"name": "Test User", "email": "test@example.com"}
    response = client.post("/users/", json=user_data)
    assert response.status_code == 201
    
    created_user = response.json()
    assert created_user["name"] == "Test User"
    assert created_user["email"] == "test@example.com"
    assert "id" in created_user


def test_auto_generated_schema_with_timestamps():
    """Test auto-generated schemas with timestamp fields."""
    reset_metadata()
    fd.setup_async_database_connection("sqlite+aiosqlite:///:memory:")
    
    app = FastAPI()
    
    # Define a model with timestamps
    class Product(fd.IDBase, fd.TimestampsMixin):
        name: Mapped[str]
        price: Mapped[float]
        description: Mapped[str] = fd.mapped_column(default="")
    
    # Create view without schema
    @fd.include_view(app)
    class ProductView(fd.AsyncAlchemyView):
        prefix = "/products"
        model = Product
        # No schema specified - should be auto-generated!
    
    # Create tables
    async def create_tables():
        engine = fa_globals.async_make_session.kw["bind"]
        async with engine.begin() as conn:
            await conn.run_sync(fd.SQLBase.metadata.create_all)
    
    asyncio.run(create_tables())
    
    client = TestClient(app)
    
    # Test creating a product
    product_data = {"name": "Test Product", "price": 29.99}
    response = client.post("/products/", json=product_data)
    assert response.status_code == 201
    
    created_product = response.json()
    assert created_product["name"] == "Test Product"
    assert created_product["price"] == 29.99
    assert "id" in created_product
    assert "created_at" in created_product
    assert "updated_at" in created_product


def test_auto_generated_schema_with_defaults():
    """Test auto-generated schemas with field defaults."""
    reset_metadata()
    fd.setup_async_database_connection("sqlite+aiosqlite:///:memory:")
    
    app = FastAPI()
    
    # Define a model with defaults
    class Category(fd.IDBase):
        name: Mapped[str]
        description: Mapped[str] = fd.mapped_column(default="No description")
        is_active: Mapped[bool] = fd.mapped_column(default=True)
    
    # Create view without schema
    @fd.include_view(app)
    class CategoryView(fd.AsyncAlchemyView):
        prefix = "/categories"
        model = Category
        # No schema specified - should be auto-generated!
    
    # Create tables
    async def create_tables():
        engine = fa_globals.async_make_session.kw["bind"]
        async with engine.begin() as conn:
            await conn.run_sync(fd.SQLBase.metadata.create_all)
    
    asyncio.run(create_tables())
    
    client = TestClient(app)
    
    # Test creating a category with minimal data
    category_data = {"name": "Electronics"}
    response = client.post("/categories/", json=category_data)
    assert response.status_code == 201
    
    created_category = response.json()
    assert created_category["name"] == "Electronics"
    assert "id" in created_category


def test_auto_generated_schema_crud_operations():
    """Test full CRUD operations with auto-generated schemas."""
    reset_metadata()
    fd.setup_async_database_connection("sqlite+aiosqlite:///:memory:")
    
    app = FastAPI()
    
    # Define a simple model
    class Item(fd.IDBase):
        name: Mapped[str]
        quantity: Mapped[int]
    
    # Create view without schema
    @fd.include_view(app)
    class ItemView(fd.AsyncAlchemyView):
        prefix = "/items"
        model = Item
        # No schema specified - should be auto-generated!
    
    # Create tables
    async def create_tables():
        engine = fa_globals.async_make_session.kw["bind"]
        async with engine.begin() as conn:
            await conn.run_sync(fd.SQLBase.metadata.create_all)
    
    asyncio.run(create_tables())
    
    client = TestClient(app)
    
    # Test CREATE
    item_data = {"name": "Test Item", "quantity": 10}
    response = client.post("/items/", json=item_data)
    assert response.status_code == 201
    
    created_item = response.json()
    item_id = created_item["id"]
    
    # Test READ
    response = client.get(f"/items/{item_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Test Item"
    
    # Test UPDATE
    update_data = {"name": "Updated Item", "quantity": 20}
    response = client.put(f"/items/{item_id}", json=update_data)
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Item"
    assert response.json()["quantity"] == 20
    
    # Test DELETE
    response = client.delete(f"/items/{item_id}")
    assert response.status_code == 204
    
    # Verify deletion
    response = client.get(f"/items/{item_id}")
    assert response.status_code == 404 