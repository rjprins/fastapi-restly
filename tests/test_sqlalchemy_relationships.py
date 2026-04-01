"""Tests for SQLAlchemy relationships with nested schemas in FastAPI-Restly framework."""

import asyncio
from datetime import datetime
from typing import List

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import Field
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.db import fr_globals
from fastapi_restly.schemas import ReadOnly

from .conftest import create_tables


class TestOneToManyRelationships:
    """Test one-to-many relationships with nested schemas."""

    def test_one_to_many_relationship_basic(self, client):
        """Test basic one-to-many relationship without aliases."""

        # Define SQLAlchemy models with relationship
        class User1(fr.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            email: Mapped[str] = mapped_column(String(100))
            addresses: Mapped[List["Address1"]] = relationship(
                "Address1", back_populates="user", default_factory=list
            )

        class Address1(fr.IDBase):
            street: Mapped[str] = mapped_column(String(200))
            city: Mapped[str] = mapped_column(String(100))
            user_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("user1.id"), init=False
            )
            user: Mapped["User1"] = relationship("User1", back_populates="addresses")

        # Define schemas
        class AddressSchema1(fr.IDSchema):
            street: str
            city: str

        class UserSchema1(fr.IDSchema):
            name: str
            email: str
            addresses: List[AddressSchema1]

        @fr.include_view(client.app)
        class UserView1(fr.AsyncRestView):
            prefix = "/users1"
            model = User1
            schema = UserSchema1

        create_tables()

        async def insert_test_data():
            async with fr.async_session() as session:
                user = User1(name="John Doe", email="john@example.com")
                address1 = Address1(street="123 Main St", city="Anytown", user=user)
                address2 = Address1(street="456 Oak Ave", city="Somewhere", user=user)
                session.add(user)
                session.add(address1)
                session.add(address2)
                await session.commit()

        asyncio.run(insert_test_data())

        response = client.get("/users1/")
        users = response.json()

        assert len(users) == 1
        user = users[0]
        assert user["name"] == "John Doe"
        assert {address["street"] for address in user["addresses"]} == {
            "123 Main St",
            "456 Oak Ave",
        }
        assert {address["city"] for address in user["addresses"]} == {
            "Anytown",
            "Somewhere",
        }

    def test_one_to_many_relationship_with_aliases(self, client):
        """Test one-to-many relationship with aliases in nested schemas."""

        # Define SQLAlchemy models with relationship
        class User2(fr.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            email: Mapped[str] = mapped_column(String(100))
            addresses: Mapped[List["Address2"]] = relationship(
                "Address2", back_populates="user", default_factory=list
            )

        class Address2(fr.IDBase):
            street: Mapped[str] = mapped_column(String(200))
            city: Mapped[str] = mapped_column(String(100))
            user_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("user2.id"), init=False
            )
            user: Mapped["User2"] = relationship("User2", back_populates="addresses")

        # Define schemas with aliases
        class AddressSchema(fr.IDSchema):
            street: str = Field(alias="streetAddress")
            city: str = Field(alias="cityName")

        class UserSchema(fr.IDSchema):
            name: str
            email: str
            addresses: List[AddressSchema]

        @fr.include_view(client.app)
        class UserView2(fr.AsyncRestView):
            prefix = "/users"
            model = User2
            schema = UserSchema

        create_tables()

        # Insert test data to actually test aliases
        async def insert_test_data():
            async with fr.async_session() as session:
                # Create a user first
                user = User2(name="John Doe", email="john@example.com")
                session.add(user)
                await session.flush()  # Get the user ID

                # Create addresses for the user
                address1 = Address2(street="123 Main St", city="Anytown", user=user)
                address2 = Address2(street="456 Oak Ave", city="Somewhere", user=user)
                session.add(address1)
                session.add(address2)
                await session.commit()

        asyncio.run(insert_test_data())

        # Test GET - should return nested addresses with aliases
        response = client.get("/users/")
        assert response.status_code == 200
        users = response.json()

        # Now we actually test the aliases
        assert len(users) > 0, "No users returned"
        user = users[0]
        assert "addresses" in user, "No addresses in user"
        assert len(user["addresses"]) > 0, "No addresses in user"

        address = user["addresses"][0]
        assert "streetAddress" in address, "Expected alias 'streetAddress' not found"
        assert "cityName" in address, "Expected alias 'cityName' not found"

        # Also check that field names are NOT present
        assert "street" not in address, "Field name 'street' should not be present"
        assert "city" not in address, "Field name 'city' should not be present"


class TestOneToOneRelationships:
    """Test one-to-one relationships with nested schemas."""

    def test_one_to_one_relationship_basic(self, client):
        """Test basic one-to-one relationship without aliases."""

        # Define SQLAlchemy models with relationship
        class User3(fr.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            email: Mapped[str] = mapped_column(String(100))
            profile: Mapped["Profile3"] = relationship(
                "Profile3", back_populates="user", uselist=False, default=None
            )

        class Profile3(fr.IDBase):
            bio: Mapped[str] = mapped_column(Text)
            website: Mapped[str] = mapped_column(String(200))
            user_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("user3.id"), unique=True, init=False
            )
            user: Mapped["User3"] = relationship("User3", back_populates="profile")

        # Define schemas
        class ProfileSchema3(fr.IDSchema):
            bio: str
            website: str

        class UserSchema3(fr.IDSchema):
            name: str
            email: str
            profile: ProfileSchema3

        @fr.include_view(client.app)
        class UserView3(fr.AsyncRestView):
            prefix = "/users3"
            model = User3
            schema = UserSchema3

        create_tables()

        async def insert_test_data():
            async with fr.async_session() as session:
                user = User3(name="John Doe", email="john@example.com")
                profile = Profile3(
                    bio="Backend engineer", website="https://example.com", user=user
                )
                session.add(user)
                session.add(profile)
                await session.commit()

        asyncio.run(insert_test_data())

        response = client.get("/users3/")
        users = response.json()

        assert len(users) == 1
        user = users[0]
        assert user["name"] == "John Doe"
        assert user["email"] == "john@example.com"
        assert user["profile"]["bio"] == "Backend engineer"
        assert user["profile"]["website"] == "https://example.com"

    def test_one_to_one_relationship_with_aliases(self, client):
        """Test one-to-one relationship with aliases in nested schemas."""

        # Define SQLAlchemy models with relationship
        class User4(fr.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            email: Mapped[str] = mapped_column(String(100))
            profile: Mapped["Profile4"] = relationship(
                "Profile4", back_populates="user", uselist=False, default=None
            )

        class Profile4(fr.IDBase):
            bio: Mapped[str] = mapped_column(Text)
            website: Mapped[str] = mapped_column(String(200))
            user_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("user4.id"), unique=True, init=False
            )
            user: Mapped["User4"] = relationship("User4", back_populates="profile")

        # Define schemas with aliases
        class ProfileSchema4(fr.IDSchema):
            bio: str = Field(alias="userBio")
            website: str = Field(alias="userWebsite")

        class UserSchema4(fr.IDSchema):
            name: str
            email: str
            profile: ProfileSchema4

        @fr.include_view(client.app)
        class UserView4(fr.AsyncRestView):
            prefix = "/users4"
            model = User4
            schema = UserSchema4

        create_tables()

        async def insert_test_data():
            async with fr.async_session() as session:
                user = User4(name="Jane Doe", email="jane@example.com")
                profile = Profile4(
                    bio="Alias bio", website="https://alias.example.com", user=user
                )
                session.add(user)
                session.add(profile)
                await session.commit()

        asyncio.run(insert_test_data())

        response = client.get("/users4/")
        users = response.json()

        assert len(users) == 1
        user = users[0]
        profile = user["profile"]
        assert profile["userBio"] == "Alias bio"
        assert profile["userWebsite"] == "https://alias.example.com"
        assert "bio" not in profile
        assert "website" not in profile


class TestManyToManyRelationships:
    """Test many-to-many relationships with nested schemas."""

    def test_many_to_many_relationship_basic(self, client):
        """Test basic many-to-many relationship without aliases."""

        # Define SQLAlchemy models with many-to-many relationship
        class User5(fr.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            email: Mapped[str] = mapped_column(String(100))
            groups: Mapped[List["Group5"]] = relationship(
                "Group5",
                secondary="user5_group5",
                back_populates="users",
                default_factory=list,
            )

        class Group5(fr.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            description: Mapped[str] = mapped_column(Text)
            users: Mapped[List["User5"]] = relationship(
                "User5",
                secondary="user5_group5",
                back_populates="groups",
                default_factory=list,
            )

        # Association table
        class UserGroup5(fr.DataclassBase):
            __tablename__ = "user5_group5"
            user_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("user5.id"), primary_key=True, init=False
            )
            group_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("group5.id"), primary_key=True, init=False
            )

        # Define schemas
        class GroupSchema5(fr.IDSchema):
            name: str
            description: str

        class UserSchema5(fr.IDSchema):
            name: str
            email: str
            groups: List[GroupSchema5]

        @fr.include_view(client.app)
        class UserView5(fr.AsyncRestView):
            prefix = "/users5"
            model = User5
            schema = UserSchema5

        create_tables()

        async def insert_test_data():
            async with fr.async_session() as session:
                user = User5(name="John Doe", email="john@example.com")
                group1 = Group5(name="Admins", description="System administrators")
                group2 = Group5(name="Editors", description="Content editors")
                user.groups.extend([group1, group2])
                session.add(user)
                session.add(group1)
                session.add(group2)
                await session.commit()

        asyncio.run(insert_test_data())

        response = client.get("/users5/")
        users = response.json()

        assert len(users) == 1
        user = users[0]
        assert user["name"] == "John Doe"
        assert {group["name"] for group in user["groups"]} == {"Admins", "Editors"}
        assert {
            group["description"] for group in user["groups"]
        } == {"System administrators", "Content editors"}


class TestDeeplyNestedRelationships:
    """Test deeply nested relationships with aliases."""

    def test_deeply_nested_relationships(self, client):
        """Test deeply nested relationships with aliases."""

        # Define SQLAlchemy models with deep nesting
        class Company6(fr.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            departments: Mapped[List["Department6"]] = relationship(
                "Department6", back_populates="company", default_factory=list
            )

        class Department6(fr.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            company_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("company6.id"), init=False
            )
            company: Mapped["Company6"] = relationship(
                "Company6", back_populates="departments", default=None
            )
            employees: Mapped[List["Employee6"]] = relationship(
                "Employee6", back_populates="department", default_factory=list
            )

        class Employee6(fr.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            department_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("department6.id"), init=False
            )
            department: Mapped["Department6"] = relationship(
                "Department6", back_populates="employees", default=None
            )
            projects: Mapped[List["Project6"]] = relationship(
                "Project6",
                secondary="employee6_project6",
                back_populates="employees",
                default_factory=list,
            )

        class Project6(fr.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            employees: Mapped[List["Employee6"]] = relationship(
                "Employee6",
                secondary="employee6_project6",
                back_populates="projects",
                default_factory=list,
            )

        # Association table
        class EmployeeProject6(fr.DataclassBase):
            __tablename__ = "employee6_project6"
            employee_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("employee6.id"), primary_key=True, init=False
            )
            project_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("project6.id"), primary_key=True, init=False
            )

        # Define schemas with aliases
        class ProjectSchema6(fr.IDSchema):
            name: str = Field(alias="projectName")

        class EmployeeSchema6(fr.IDSchema):
            name: str = Field(alias="employeeName")
            projects: List[ProjectSchema6]

        class DepartmentSchema6(fr.IDSchema):
            name: str = Field(alias="departmentName")
            employees: List[EmployeeSchema6]

        class CompanySchema6(fr.IDSchema):
            name: str = Field(alias="companyName")
            departments: List[DepartmentSchema6]

        @fr.include_view(client.app)
        class CompanyView6(fr.AsyncRestView):
            prefix = "/companies6"
            model = Company6
            schema = CompanySchema6

        create_tables()

        async def insert_test_data():
            async with fr.async_session() as session:
                company = Company6(name="Acme Corp")
                department = Department6(name="Engineering", company=company)
                employee = Employee6(name="Alice", department=department)
                project = Project6(name="Platform")
                employee.projects.append(project)
                session.add(company)
                session.add(department)
                session.add(employee)
                session.add(project)
                await session.commit()

        asyncio.run(insert_test_data())

        response = client.get("/companies6/")
        companies = response.json()

        assert len(companies) == 1
        company = companies[0]
        assert company["companyName"] == "Acme Corp"
        assert "name" not in company

        department = company["departments"][0]
        assert department["departmentName"] == "Engineering"
        assert "name" not in department

        employee = department["employees"][0]
        assert employee["employeeName"] == "Alice"
        assert "name" not in employee

        project = employee["projects"][0]
        assert project["projectName"] == "Platform"
        assert "name" not in project


class TestRelationshipWithReadOnlyFields:
    """Test relationships with ReadOnly fields."""

    def test_relationship_with_readonly_fields(self, client):
        """Test relationships where some fields are ReadOnly."""

        # Define SQLAlchemy models with relationship
        class User7(fr.IDStampsBase):
            name: Mapped[str] = mapped_column(String(100))
            email: Mapped[str] = mapped_column(String(100))
            addresses: Mapped[List["Address7"]] = relationship(
                "Address7", back_populates="user", default_factory=list
            )

        class Address7(fr.IDStampsBase):
            street: Mapped[str] = mapped_column(String(200))
            city: Mapped[str] = mapped_column(String(100))
            user_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("user7.id"), init=False
            )
            user: Mapped["User7"] = relationship("User7", back_populates="addresses")

        # Define schemas with ReadOnly fields
        class AddressSchema7(fr.IDSchema):
            id: ReadOnly[int]
            street: str = Field(alias="streetAddress")
            city: str = Field(alias="cityName")
            created_at: ReadOnly[datetime]

        class UserSchema7(fr.IDSchema):
            id: ReadOnly[int]
            name: str
            email: str
            addresses: List[AddressSchema7]
            created_at: ReadOnly[datetime]

        @fr.include_view(client.app)
        class UserView7(fr.AsyncRestView):
            prefix = "/users7"
            model = User7
            schema = UserSchema7

        create_tables()

        async def insert_test_data():
            async with fr.async_session() as session:
                user = User7(name="John Doe", email="john@example.com")
                address = Address7(
                    street="123 Main St", city="Anytown", user=user
                )
                session.add(user)
                session.add(address)
                await session.commit()

        asyncio.run(insert_test_data())

        response = client.get("/users7/")
        users = response.json()

        assert len(users) == 1
        user = users[0]
        assert isinstance(user["id"], int)
        assert user["created_at"]

        address = user["addresses"][0]
        assert isinstance(address["id"], int)
        assert address["created_at"]
        assert address["streetAddress"] == "123 Main St"
        assert address["cityName"] == "Anytown"
        assert "street" not in address
        assert "city" not in address
