"""Test auto-generated schemas."""

import types
from typing import Union, get_args, get_origin

import pytest
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

import fastapi_restly as fr
import fastapi_restly.schemas as fr_schemas
from fastapi_restly.schemas._base import is_readonly_field

from .conftest import create_tables


def test_derive_schema_is_the_only_public_schema_generator():
    """Manual schema generation is available from the advanced schema module."""

    assert not hasattr(fr, "derive_schema")
    assert "derive_schema" not in fr.__all__
    assert hasattr(fr_schemas, "derive_schema")
    assert "derive_schema" in fr_schemas.__all__


def test_derive_schema_options_are_keyword_only():
    class User(fr.IDBase):
        name: Mapped[str]

    with pytest.raises(TypeError):
        fr_schemas.derive_schema(User, "UserSchema")  # type: ignore[misc]


def test_auto_generated_schema_in_view(client):
    """Test that schemas are auto-generated when not specified in views."""

    # Define a simple model without manually creating a schema
    class User(fr.IDBase):
        name: Mapped[str]
        email: Mapped[str]
        is_active: Mapped[bool] = mapped_column(default=True)

    # Create view WITHOUT specifying a schema - it should be auto-generated
    @fr.include_view(client.app)
    class UserView(fr.AsyncRestView):
        prefix = "/users"
        model = User
        # No schema specified - should be auto-generated!

    assert UserView.schema.__name__ == "UserSchema"
    assert UserView.schema_create.__name__ == "UserCreate"
    assert UserView.schema_update.__name__ == "UserUpdate"

    create_tables()

    # Test that the API is working
    response = client.get("/openapi.json")

    spec = response.json()
    paths = spec["paths"]

    # Check that endpoints exist
    assert "/users" in paths
    assert "/users/" not in paths
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
    class Product(fr.IDBase, fr.TimestampsMixin):
        name: Mapped[str]
        price: Mapped[float]
        description: Mapped[str] = mapped_column(default="")

    # Create view without schema
    @fr.include_view(client.app)
    class ProductView(fr.AsyncRestView):
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
    class Category(fr.IDBase):
        name: Mapped[str]
        description: Mapped[str] = mapped_column(default="No description")
        is_active: Mapped[bool] = mapped_column(default=True)

    # Create view without schema
    @fr.include_view(client.app)
    class CategoryView(fr.AsyncRestView):
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
    class Item(fr.IDBase):
        name: Mapped[str]
        quantity: Mapped[int]

    # Create view without schema
    @fr.include_view(client.app)
    class ItemView(fr.AsyncRestView):
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


def test_derive_schema_includes_nested_relationship_schema():
    """Manual schema generation should resolve relationships to nested schemas."""

    class User(fr.IDBase):
        name: Mapped[str]
        email: Mapped[str]

    class Order(fr.IDBase):
        user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
        user: Mapped[User] = relationship()

    schema = fr_schemas.derive_schema(Order, include_relationships=True)

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

    class User(fr.IDBase):
        name: Mapped[str]

    class Order(fr.IDBase):
        user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
        user: Mapped[User] = relationship()

    @fr.include_view(client.app)
    class OrderView(fr.AsyncRestView):
        prefix = "/orders"
        model = Order

    create_tables()

    assert "user" not in OrderView.schema.model_fields
    assert "user_id" in OrderView.schema.model_fields


def test_derive_schema_returns_the_schema_a_view_generates(client):
    """With its defaults, ``derive_schema`` builds the schema of a view that
    sets none: same name, same fields, no relationships."""

    class Customer(fr.TimestampsMixin, fr.IDBase):
        name: Mapped[str]

    class Invoice(fr.IDBase):
        number: Mapped[str]
        note: Mapped[str | None]
        customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
        customer: Mapped[Customer] = relationship()

    @fr.include_view(client.app)
    class InvoiceView(fr.AsyncRestView):
        prefix = "/invoices"
        model = Invoice

    derived = fr_schemas.derive_schema(Invoice)

    assert derived.__name__ == InvoiceView.schema.__name__
    assert derived.model_json_schema() == InvoiceView.schema.model_json_schema()
    assert "customer" not in derived.model_fields


def test_derive_schema_marks_read_only_fields_but_not_nested_references():
    """The schema's own id and timestamps are ReadOnly. The nested schema of a
    relationship has no ReadOnly marks: it is also in the create and update
    bodies, where its id is required."""

    class Customer(fr.TimestampsMixin, fr.IDBase):
        name: Mapped[str]

    class Invoice(fr.TimestampsMixin, fr.IDBase):
        customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
        customer: Mapped[Customer] = relationship()

    schema = fr_schemas.derive_schema(Invoice, include_relationships=True)
    [nested] = [
        arg
        for arg in get_args(schema.model_fields["customer"].annotation)
        if arg is not type(None)
    ]
    assert nested.__name__ == "CustomerSchema"

    for field in ("id", "created_at", "updated_at"):
        assert is_readonly_field(schema, field)
        assert not is_readonly_field(nested, field)


def test_derive_schema_preserves_json_dict_types():
    class Event(fr.IDBase):
        payload: Mapped[dict] = mapped_column(JSON)

    schema = fr_schemas.derive_schema(Event)

    assert schema.model_fields["payload"].annotation is dict


def test_derive_schema_names_the_class_after_the_model():
    class Report(fr.IDBase):
        title: Mapped[str]

    assert fr_schemas.derive_schema(Report).__name__ == "ReportSchema"
    assert fr_schemas.derive_schema(Report, name="Summary").__name__ == "Summary"
