"""Test basic functionality of the FastAPI-Ding framework."""

import asyncio
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.orm import Mapped

import fastapi_ding as fd
from fastapi_ding._globals import fa_globals
from .conftest import create_tables


def test_crud_endpoints_exist(client):
    """Test that all CRUD endpoints are created."""

    # Define a simple model
    class User(fd.IDBase):
        name: Mapped[str]
        email: Mapped[str]

    # Create a schema
    class UserSchema(fd.IDSchema[User]):
        name: str
        email: str

    # Create a view
    @fd.include_view(client.app)
    class UserView(fd.AsyncAlchemyView):
        prefix = "/users"
        model = User
        schema = UserSchema

    create_tables()

    # Test that all CRUD endpoints exist
    response = client.get("/users/")

    response = client.post(
        "/users/", json={"name": "Test User", "email": "test@example.com"}
    )

    created_user = response.json()
    assert "id" in created_user

    user_id = created_user["id"]

    response = client.get(f"/users/{user_id}")

    response = client.put(
        f"/users/{user_id}",
        json={"name": "Updated User", "email": "updated@example.com"},
    )

    response = client.delete(f"/users/{user_id}")


def test_basic_crud_operations(client):
    """Test basic CRUD operations."""

    # Define a simple model
    class Product(fd.IDBase):
        name: Mapped[str]
        price: Mapped[float]

    # Create a schema
    class ProductSchema(fd.IDSchema[Product]):
        name: str
        price: float

    # Create a view
    @fd.include_view(client.app)
    class ProductView(fd.AsyncAlchemyView):
        prefix = "/products"
        model = Product
        schema = ProductSchema

    create_tables()

    # Test CREATE
    product_data = {"name": "Test Product", "price": 29.99}
    response = client.post("/products/", json=product_data)

    created_product = response.json()
    assert created_product["name"] == "Test Product"
    assert created_product["price"] == 29.99
    assert "id" in created_product

    product_id = created_product["id"]

    # Test READ
    response = client.get(f"/products/{product_id}")
    retrieved_product = response.json()
    assert retrieved_product["name"] == "Test Product"
    assert retrieved_product["price"] == 29.99

    # Test UPDATE
    update_data = {"name": "Updated Product", "price": 39.99}
    response = client.put(f"/products/{product_id}", json=update_data)
    updated_product = response.json()
    assert updated_product["name"] == "Updated Product"
    assert updated_product["price"] == 39.99

    # Test DELETE
    response = client.delete(f"/products/{product_id}")
    assert response.status_code == 204

    # Verify deletion
    client.get(f"/products/{product_id}", assert_status_code=404)


def test_list_endpoint(client):
    """Test the list endpoint functionality."""

    # Define a simple model
    class Category(fd.IDBase):
        name: Mapped[str]
        description: Mapped[str]

    # Create a schema
    class CategorySchema(fd.IDSchema[Category]):
        name: str
        description: str

    # Create a view
    @fd.include_view(client.app)
    class CategoryView(fd.AsyncAlchemyView):
        prefix = "/categories"
        model = Category
        schema = CategorySchema

    create_tables()

    # Create multiple categories
    categories_data = [
        {"name": "Electronics", "description": "Electronic devices"},
        {"name": "Books", "description": "Books and literature"},
        {"name": "Clothing", "description": "Apparel and accessories"},
    ]

    created_categories = []
    for category_data in categories_data:
        response = client.post("/categories/", json=category_data)
        created_categories.append(response.json())

    # Test list endpoint
    response = client.get("/categories/")
    categories_list = response.json()

    assert len(categories_list) == 3
    assert all("id" in category for category in categories_list)
    assert all("name" in category for category in categories_list)
    assert all("description" in category for category in categories_list)
