"""Test to demonstrate the nested schema limitation in FastAPI-Restly framework."""

import pytest
from pydantic import Field
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly.schemas import BaseSchema

from .conftest import create_tables


@pytest.mark.xfail(reason="Nested schemas not supported for input")
def test_nested_schema_limitation_demonstration(client):
    """Nested schemas fail because build_from_schema passes a Pydantic object to the
    SQLAlchemy model constructor instead of flattened fields. Use flattened schemas
    with aliases as a workaround (see test_working_flattened_approach)."""

    class AddressSchema(BaseSchema):
        street: str = Field(alias="streetAddress")
        city: str = Field(alias="cityName")
        postal_code: str = Field(alias="postalCode")

    class UserSchema(fr.IDSchema):
        name: str
        email: str
        address: AddressSchema  # ← This nested schema causes the problem

    # Create a simple model with flattened fields
    class User(fr.IDBase):
        name: Mapped[str]
        email: Mapped[str]
        street: Mapped[str]
        city: Mapped[str]
        postal_code: Mapped[str]

    @fr.include_view(client.app)
    class UserView(fr.AsyncRestView):
        prefix = "/users"
        model = User
        schema = UserSchema

    create_tables()

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
    assert response.status_code == 201


def test_working_flattened_approach(client):
    """
    This test shows the WORKING approach using flattened schemas.
    """

    # Define a flattened schema (this is what WORKS)
    class UserSchema(fr.IDSchema):
        name: str
        email: str
        street: str = Field(alias="streetAddress")  # ← Flattened
        city: str = Field(alias="cityName")  # ← Flattened
        postal_code: str = Field(alias="postalCode")  # ← Flattened

    # Create a simple model
    class User(fr.IDBase):
        name: Mapped[str]
        email: Mapped[str]
        street: Mapped[str]
        city: Mapped[str]
        postal_code: Mapped[str]

    @fr.include_view(client.app)
    class UserView(fr.AsyncRestView):
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
    """Same limitation as above, with multiple levels of nesting."""

    class ContactInfoSchema(BaseSchema):
        phone: str = Field(alias="phoneNumber")
        email: str = Field(alias="emailAddress")

    class AddressSchema(BaseSchema):
        street: str = Field(alias="streetAddress")
        city: str = Field(alias="cityName")
        contact: ContactInfoSchema

    class CompanySchema(BaseSchema):
        name: str = Field(alias="companyName")
        industry: str = Field(alias="industryType")

    class EmployeeSchema(fr.IDSchema):
        name: str
        address: AddressSchema
        company: CompanySchema

    class Employee(fr.IDBase):
        name: Mapped[str]
        phone: Mapped[str]
        email: Mapped[str]
        street: Mapped[str]
        city: Mapped[str]
        company_name: Mapped[str]
        industry: Mapped[str]

    @fr.include_view(client.app)
    class EmployeeView(fr.AsyncRestView):
        prefix = "/employees"
        model = Employee
        schema = EmployeeSchema

    create_tables()
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

    assert response.status_code == 201
