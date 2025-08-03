"""Test to demonstrate the nested schema limitation in FastAPI-Ding framework."""

import asyncio
import pytest
from datetime import datetime
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import Field
from sqlalchemy.orm import Mapped

import fastapi_ding as fd
from fastapi_ding._globals import fa_globals
from fastapi_ding._schemas import ReadOnly, BaseSchema
from .conftest import create_tables


@pytest.mark.xfail(reason="Nested schemas not supported for input")
def test_nested_schema_limitation_demonstration(client):
    """
    This test demonstrates why nested schemas don't work in the current framework.

    The issue is in the async_view.py make_new_object method:
    ```
    obj = self.model(**data)
    ```

    When you have a nested schema like:
    ```python
    class UserSchema(fd.IDSchema):
        name: str
        email: str
        address: AddressSchema  # ← Nested schema
    ```

    The `data` dictionary becomes:
    ```python
    {
        'name': 'John Doe',
        'email': 'john@example.com',
        'address': AddressSchema(street='123 Main St', city='Anytown')  # ← Pydantic object
    }
    ```

    But the SQLAlchemy model expects:
    ```python
    {
        'name': 'John Doe',
        'email': 'john@example.com',
        'street': '123 Main St',  # ← Flattened fields
        'city': 'Anytown'         # ← Flattened fields
    }
    ```
    """

    # Define schemas with nested structure (this will FAIL)
    class AddressSchema(BaseSchema):
        street: str = Field(alias="streetAddress")
        city: str = Field(alias="cityName")
        postal_code: str = Field(alias="postalCode")

    class UserSchema(fd.IDSchema):
        name: str
        email: str
        address: AddressSchema  # ← This nested schema causes the problem

    # Create a simple model with flattened fields
    class User(fd.IDBase):
        name: Mapped[str]
        email: Mapped[str]
        street: Mapped[str]
        city: Mapped[str]
        postal_code: Mapped[str]

    @fd.include_view(client.app)
    class UserView(fd.AsyncAlchemyView):
        prefix = "/users"
        model = User
        schema = UserSchema

    create_tables()

    # This POST request will FAIL because of nested schema
    response = client.post(
        "/users/",
        json={
            "name": "John Doe",
            "email": "john@example.com",
            "address": {
                "streetAddress": "123 Main St",
                "cityName": "Anytown",
                "postalCode": "12345",
            },
        },
    )

    # This will fail with: TypeError: __init__() got an unexpected keyword argument 'address'
    assert response.status_code == 201


def test_working_flattened_approach(client):
    """
    This test shows the WORKING approach using flattened schemas.
    """

    # Define a flattened schema (this is what WORKS)
    class UserSchema(fd.IDSchema):
        name: str
        email: str
        street: str = Field(alias="streetAddress")  # ← Flattened
        city: str = Field(alias="cityName")  # ← Flattened
        postal_code: str = Field(alias="postalCode")  # ← Flattened

    # Create a simple model
    class User(fd.IDBase):
        name: Mapped[str]
        email: Mapped[str]
        street: Mapped[str]
        city: Mapped[str]
        postal_code: Mapped[str]

    @fd.include_view(client.app)
    class UserView(fd.AsyncAlchemyView):
        prefix = "/users"
        model = User
        schema = UserSchema

    create_tables()

    # This POST request will WORK because it uses flattened fields
    response = client.post(
        "/users/",
        json={
            "name": "John Doe",
            "email": "john@example.com",
            "streetAddress": "123 Main St",  # ← Direct field
            "cityName": "Anytown",  # ← Direct field
            "postalCode": "12345",  # ← Direct field
        },
    )

    # This will succeed
    assert response.status_code == 201
    created_user = response.json()

    # Check that response uses aliases
    assert "streetAddress" in created_user
    assert "cityName" in created_user
    assert "postalCode" in created_user


@pytest.mark.xfail(reason="Deeply nested schemas not supported for input")
def test_deeply_nested_schema_limitation(client):
    """
    This test shows how deeply nested schemas would fail.
    """

    # Define deeply nested schemas (this would FAIL)
    class ContactInfoSchema(BaseSchema):
        phone: str = Field(alias="phoneNumber")
        email: str = Field(alias="emailAddress")

    class AddressSchema(BaseSchema):
        street: str = Field(alias="streetAddress")
        city: str = Field(alias="cityName")
        contact: ContactInfoSchema  # ← Nested schema

    class CompanySchema(BaseSchema):
        name: str = Field(alias="companyName")
        industry: str = Field(alias="industryType")

    class EmployeeSchema(fd.IDSchema):
        name: str
        address: AddressSchema  # ← Nested schema
        company: CompanySchema  # ← Nested schema

    # Create a simple model
    class Employee(fd.IDBase):
        name: Mapped[str]
        phone: Mapped[str]
        email: Mapped[str]
        street: Mapped[str]
        city: Mapped[str]
        company_name: Mapped[str]
        industry: Mapped[str]

    @fd.include_view(client.app)
    class EmployeeView(fd.AsyncAlchemyView):
        prefix = "/employees"
        model = Employee
        schema = EmployeeSchema

    create_tables()
    # This POST request would FAIL
    response = client.post(
        "/employees/",
        json={
            "name": "Jane Smith",
            "address": {
                "streetAddress": "456 Oak Ave",
                "cityName": "Somewhere",
                "contact": {
                    "phoneNumber": "555-1234",
                    "emailAddress": "jane@example.com",
                },
            },
            "company": {"companyName": "Tech Corp", "industryType": "Technology"},
        },
    )

    # This will fail with: TypeError: __init__() got an unexpected keyword argument 'address'
    assert response.status_code == 201  # This will fail
