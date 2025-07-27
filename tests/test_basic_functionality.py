"""Test basic functionality of the FastAPI-Ding framework."""

import asyncio
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.orm import Mapped

import fastapi_ding as fd
from fastapi_ding._globals import fa_globals


def reset_metadata():
    """Reset SQLAlchemy metadata to prevent table redefinition conflicts."""
    fd.SQLBase.metadata.clear()


def test_crud_endpoints_exist(client):
    """Test that all CRUD endpoints are created."""
    reset_metadata()
    fd.setup_async_database_connection("sqlite+aiosqlite:///:memory:")
    
    app = client.app
    
    # Define a simple model
    class User(fd.IDBase):
        name: Mapped[str]
        email: Mapped[str]
    
    # Create a schema
    class UserSchema(fd.IDSchema[User]):
        name: str
        email: str
    
    # Create a view
    @fd.include_view(app)
    class UserView(fd.AsyncAlchemyView):
        prefix = "/users"
        model = User
        schema = UserSchema
    
    # Create tables
    async def create_tables():
        engine = fa_globals.async_make_session.kw["bind"]
        async with engine.begin() as conn:
            await conn.run_sync(fd.SQLBase.metadata.create_all)
    
    asyncio.run(create_tables())
    
    # Test that all CRUD endpoints exist
    response = client.get("/users/")
    
    response = client.post("/users/", json={"name": "Test User", "email": "test@example.com"})
    
    created_user = response.json()
    assert "id" in created_user
    
    user_id = created_user["id"]
    
    response = client.get(f"/users/{user_id}")
    
    response = client.put(f"/users/{user_id}", json={"name": "Updated User", "email": "updated@example.com"})
    
    response = client.delete(f"/users/{user_id}")


def test_basic_crud_operations(client):
    """Test basic CRUD operations."""
    reset_metadata()
    fd.setup_async_database_connection("sqlite+aiosqlite:///:memory:")
    
    app = client.app
    
    # Define a simple model
    class Product(fd.IDBase):
        name: Mapped[str]
        price: Mapped[float]
    
    # Create a schema
    class ProductSchema(fd.IDSchema[Product]):
        name: str
        price: float
    
    # Create a view
    @fd.include_view(app)
    class ProductView(fd.AsyncAlchemyView):
        prefix = "/products"
        model = Product
        schema = ProductSchema
    
    # Create tables
    async def create_tables():
        engine = fa_globals.async_make_session.kw["bind"]
        async with engine.begin() as conn:
            await conn.run_sync(fd.SQLBase.metadata.create_all)
    
    asyncio.run(create_tables())
    
    # Test CREATE
    product_data = {"name": "Test Product", "price": 29.99}
    response = client.post("/products/", json=product_data)
    
    created_product = response.json()
    assert created_product["name"] == "Test Product"
    assert created_product["price"] == 29.99
    assert "id" in created_product
    
    product_id = created_product["id"]
    
    # Test READ (get all)
    response = client.get("/products/")
    products = response.json()
    assert len(products) == 1
    assert products[0]["id"] == product_id
    
    # Test READ (get by id)
    response = client.get(f"/products/{product_id}")
    product = response.json()
    assert product["id"] == product_id
    assert product["name"] == "Test Product"
    
    # Test UPDATE
    update_data = {"name": "Updated Product", "price": 39.99}
    response = client.put(f"/products/{product_id}", json=update_data)
    
    updated_product = response.json()
    assert updated_product["name"] == "Updated Product"
    assert updated_product["price"] == 39.99
    
    # Test DELETE
    response = client.delete(f"/products/{product_id}")
    
    # Verify deletion
    response = client.get("/products/")
    products = response.json()
    assert len(products) == 0 