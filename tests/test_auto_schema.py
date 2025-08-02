"""Test auto-generated schemas."""

import asyncio
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.orm import Mapped

import fastapi_ding as fd
from fastapi_ding._globals import fa_globals


def test_auto_generated_schema_in_view(client):
    """Test that schemas are auto-generated when not specified in views."""
    fd.setup_async_database_connection("sqlite+aiosqlite:///:memory:")
    
    app = client.app
    
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
    
    # Test that the API is working
    response = client.get("/openapi.json")
    
    spec = response.json()
    paths = spec["paths"]
    
    # Check that endpoints exist
    assert "/users/" in paths
    assert "/users/{id}" in paths
    
    # Test creating a user
    user_data = {"name": "Test User", "email": "test@example.com"}
    response = client.post("/users/", json=user_data)
    
    created_user = response.json()
    assert created_user["name"] == "Test User"
    assert created_user["email"] == "test@example.com"
    assert "id" in created_user


def test_auto_generated_schema_with_timestamps(client):
    """Test auto-generated schemas with timestamp fields."""
    fd.setup_async_database_connection("sqlite+aiosqlite:///:memory:")
    
    app = client.app
    
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
    
    # Test creating a product
    product_data = {"name": "Test Product", "price": 29.99}
    response = client.post("/products/", json=product_data)
    
    created_product = response.json()
    assert created_product["name"] == "Test Product"
    assert created_product["price"] == 29.99
    assert "id" in created_product
    assert "created_at" in created_product
    assert "updated_at" in created_product


def test_auto_generated_schema_with_defaults(client):
    """Test auto-generated schemas with field defaults."""
    fd.setup_async_database_connection("sqlite+aiosqlite:///:memory:")
    
    app = client.app
    
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
    
    # Test creating a category with minimal data
    category_data = {"name": "Test Category"}
    response = client.post("/categories/", json=category_data)
    
    created_category = response.json()
    assert created_category["name"] == "Test Category"
    assert created_category["description"] == "No description"
    assert created_category["is_active"] is True
    assert "id" in created_category


def test_auto_generated_schema_crud_operations(client):
    """Test that auto-generated schemas work with full CRUD operations."""
    fd.setup_async_database_connection("sqlite+aiosqlite:///:memory:")
    
    app = client.app
    
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
    
    # Test CREATE
    item_data = {"name": "Test Item", "quantity": 10}
    response = client.post("/items/", json=item_data)
    
    created_item = response.json()
    assert created_item["name"] == "Test Item"
    assert created_item["quantity"] == 10
    assert "id" in created_item
    
    item_id = created_item["id"]
    
    # Test READ
    response = client.get(f"/items/{item_id}")
    retrieved_item = response.json()
    assert retrieved_item["name"] == "Test Item"
    assert retrieved_item["quantity"] == 10
    
    # Test UPDATE
    update_data = {"name": "Updated Item", "quantity": 20}
    response = client.put(f"/items/{item_id}", json=update_data)
    updated_item = response.json()
    assert updated_item["name"] == "Updated Item"
    assert updated_item["quantity"] == 20
    
    # Test DELETE
    response = client.delete(f"/items/{item_id}")
    assert response.status_code == 204
    
    # Verify deletion
    response = client.get(f"/items/{item_id}")
    assert response.status_code == 404 