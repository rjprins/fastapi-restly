"""Test auto-generated schemas."""

import asyncio
import types
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, relationship
from typing import Union, get_args, get_origin

import fastapi_restly as fd
from fastapi_restly.db import fr_globals
from .conftest import create_tables


def test_auto_generated_schema_in_view(client):
    """Test that schemas are auto-generated when not specified in views."""

    # Define a simple model without manually creating a schema
    class User(fd.IDBase):
        name: Mapped[str]
        email: Mapped[str]
        is_active: Mapped[bool] = fd.mapped_column(default=True)

    # Create view WITHOUT specifying a schema - it should be auto-generated
    @fd.include_view(client.app)
    class UserView(fd.AsyncAlchemyView):
        prefix = "/users"
        model = User
        # No schema specified - should be auto-generated!

    create_tables()

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

    # Define a model with timestamps
    class Product(fd.IDBase, fd.TimestampsMixin):
        name: Mapped[str]
        price: Mapped[float]
        description: Mapped[str] = fd.mapped_column(default="")

    # Create view without schema
    @fd.include_view(client.app)
    class ProductView(fd.AsyncAlchemyView):
        prefix = "/products"
        model = Product
        # No schema specified - should be auto-generated!

    create_tables()

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

    # Define a model with defaults
    class Category(fd.IDBase):
        name: Mapped[str]
        description: Mapped[str] = fd.mapped_column(default="No description")
        is_active: Mapped[bool] = fd.mapped_column(default=True)

    # Create view without schema
    @fd.include_view(client.app)
    class CategoryView(fd.AsyncAlchemyView):
        prefix = "/categories"
        model = Category
        # No schema specified - should be auto-generated!

    create_tables()

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

    # Define a simple model
    class Item(fd.IDBase):
        name: Mapped[str]
        quantity: Mapped[int]

    # Create view without schema
    @fd.include_view(client.app)
    class ItemView(fd.AsyncAlchemyView):
        prefix = "/items"
        model = Item
        # No schema specified - should be auto-generated!

    create_tables()

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
    response = client.patch(f"/items/{item_id}", json=update_data)
    updated_item = response.json()
    assert updated_item["name"] == "Updated Item"
    assert updated_item["quantity"] == 20

    # Test DELETE
    response = client.delete(f"/items/{item_id}")
    assert response.status_code == 204

    # Verify deletion
    client.get(f"/items/{item_id}", assert_status_code=404)


def test_create_schema_from_model_includes_nested_relationship_schema():
    """Manual schema generation should resolve relationships to nested schemas."""

    class User(fd.IDBase):
        name: Mapped[str]
        email: Mapped[str]

    class Order(fd.IDBase):
        user_id: Mapped[int] = fd.mapped_column(ForeignKey("user.id"))
        user: Mapped[User] = relationship()

    schema = fd.create_schema_from_model(Order, include_relationships=True)

    assert "user" in schema.model_fields
    user_annotation = schema.model_fields["user"].annotation
    if get_origin(user_annotation) in (types.UnionType, Union):
        nested_annotation = next(
            arg for arg in get_args(user_annotation) if arg is not type(None)
        )
    else:
        nested_annotation = user_annotation
    assert nested_annotation is not str
    assert hasattr(nested_annotation, "model_fields")
    assert "name" in nested_annotation.model_fields
    assert "email" in nested_annotation.model_fields


def test_view_auto_schema_excludes_relationship_fields_by_default(client):
    """View auto-schema should stay focused on scalar/FK fields unless opted in explicitly."""

    class User(fd.IDBase):
        name: Mapped[str]

    class Order(fd.IDBase):
        user_id: Mapped[int] = fd.mapped_column(ForeignKey("user.id"))
        user: Mapped[User] = relationship()

    @fd.include_view(client.app)
    class OrderView(fd.AsyncAlchemyView):
        prefix = "/orders"
        model = Order

    create_tables()

    assert "user" not in OrderView.schema.model_fields
    assert "user_id" in OrderView.schema.model_fields
