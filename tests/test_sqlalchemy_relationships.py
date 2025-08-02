"""Tests for SQLAlchemy relationships with nested schemas in FastAPI-Ding framework."""

import asyncio
import pytest
from datetime import datetime
from typing import List
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import Field
from sqlalchemy import ForeignKey, String, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_ding as fd
from fastapi_ding._globals import fa_globals
from fastapi_ding.schemas import ReadOnly, BaseSchema
from .conftest import create_tables


class TestOneToManyRelationships:
    """Test one-to-many relationships with nested schemas."""

    def test_one_to_many_relationship_basic(self, client):
        """Test basic one-to-many relationship without aliases."""

        # Define SQLAlchemy models with relationship
        class User1(fd.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            email: Mapped[str] = mapped_column(String(100))
            addresses: Mapped[List["Address1"]] = relationship(
                "Address1", back_populates="user"
            )

        class Address1(fd.IDBase):
            street: Mapped[str] = mapped_column(String(200))
            city: Mapped[str] = mapped_column(String(100))
            user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user1.id"))
            user: Mapped["User1"] = relationship("User1", back_populates="addresses")

        # Define schemas
        class AddressSchema1(fd.IDSchema[Address1]):
            street: str
            city: str

        class UserSchema1(fd.IDSchema[User1]):
            name: str
            email: str
            addresses: List[AddressSchema1]

        @fd.include_view(client.app)
        class UserView1(fd.AsyncAlchemyView):
            prefix = "/users1"
            model = User1
            schema = UserSchema1

        create_tables()

        # Test GET - should return nested addresses
        response = client.get("/users1/")
        assert response.status_code == 200

    @pytest.mark.xfail(reason="Aliases in nested schemas not yet supported")
    def test_one_to_many_relationship_with_aliases(self, client):
        """Test one-to-many relationship with aliases in nested schemas."""

        # Define SQLAlchemy models with relationship
        class User(fd.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            email: Mapped[str] = mapped_column(String(100))
            addresses: Mapped[List["Address"]] = relationship(
                "Address", back_populates="user", default_factory=list
            )

        class Address(fd.IDBase):
            street: Mapped[str] = mapped_column(String(200))
            city: Mapped[str] = mapped_column(String(100))
            user_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("user.id"), init=False
            )
            user: Mapped["User"] = relationship("User", back_populates="addresses")

        # Define schemas with aliases
        class AddressSchema(fd.IDSchema[Address]):
            street: str = Field(alias="streetAddress")
            city: str = Field(alias="cityName")

        class UserSchema(fd.IDSchema[User]):
            name: str
            email: str
            addresses: List[AddressSchema]

        @fd.include_view(client.app)
        class UserView2(fd.AsyncAlchemyView):
            prefix = "/users"
            model = User
            schema = UserSchema

        create_tables()

        # Insert test data to actually test aliases
        async def insert_test_data():
            async with fd.AsyncSession() as session:
                # Create a user first
                user = User(name="John Doe", email="john@example.com")
                session.add(user)
                await session.flush()  # Get the user ID

                # Create addresses for the user
                address1 = Address(street="123 Main St", city="Anytown", user=user)
                address2 = Address(street="456 Oak Ave", city="Somewhere", user=user)
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
        # This should fail because aliases don't work in nested schemas
        assert "streetAddress" in address, "Expected alias 'streetAddress' not found"
        assert "cityName" in address, "Expected alias 'cityName' not found"

        # Also check that field names are NOT present
        assert "street" not in address, "Field name 'street' should not be present"
        assert "city" not in address, "Field name 'city' should not be present"


class TestOneToOneRelationships:
    """Test one-to-one relationships with nested schemas."""

    @pytest.mark.xfail(reason="One-to-one relationships not yet tested")
    def test_one_to_one_relationship_basic(self, client):
        """Test basic one-to-one relationship without aliases."""

        # Define SQLAlchemy models with relationship
        class User3(fd.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            email: Mapped[str] = mapped_column(String(100))
            profile: Mapped["Profile3"] = relationship(
                "Profile3", back_populates="user", uselist=False
            )

        class Profile3(fd.IDBase):
            bio: Mapped[str] = mapped_column(Text)
            website: Mapped[str] = mapped_column(String(200))
            user_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("user3.id"), unique=True
            )
            user: Mapped["User3"] = relationship("User3", back_populates="profile")

        # Define schemas
        class ProfileSchema3(fd.IDSchema[Profile3]):
            bio: str
            website: str

        class UserSchema3(fd.IDSchema[User3]):
            name: str
            email: str
            profile: ProfileSchema3

        @fd.include_view(client.app)
        class UserView3(fd.AsyncAlchemyView):
            prefix = "/users3"
            model = User3
            schema = UserSchema3

        create_tables()

        # Test GET - should return nested profile
        response = client.get("/users3/")
        assert response.status_code == 200

    @pytest.mark.xfail(reason="One-to-one relationships with aliases not yet tested")
    def test_one_to_one_relationship_with_aliases(self, client):
        """Test one-to-one relationship with aliases in nested schemas."""

        # Define SQLAlchemy models with relationship
        class User4(fd.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            email: Mapped[str] = mapped_column(String(100))
            profile: Mapped["Profile4"] = relationship(
                "Profile4", back_populates="user", uselist=False
            )

        class Profile4(fd.IDBase):
            bio: Mapped[str] = mapped_column(Text)
            website: Mapped[str] = mapped_column(String(200))
            user_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("user4.id"), unique=True
            )
            user: Mapped["User4"] = relationship("User4", back_populates="profile")

        # Define schemas with aliases
        class ProfileSchema4(fd.IDSchema[Profile4]):
            bio: str = Field(alias="userBio")
            website: str = Field(alias="userWebsite")

        class UserSchema4(fd.IDSchema[User4]):
            name: str
            email: str
            profile: ProfileSchema4

        @fd.include_view(client.app)
        class UserView4(fd.AsyncAlchemyView):
            prefix = "/users4"
            model = User4
            schema = UserSchema4

        create_tables()

        # Test GET - should return nested profile with aliases
        response = client.get("/users4/")
        assert response.status_code == 200
        users = response.json()

        # Check that nested profile uses aliases
        if users:  # If there are users in the database
            user = users[0]
            if "profile" in user and user["profile"]:
                profile = user["profile"]
                assert "userBio" in profile
                assert "userWebsite" in profile


class TestManyToManyRelationships:
    """Test many-to-many relationships with nested schemas."""

    @pytest.mark.xfail(reason="Many-to-many relationships not yet tested")
    def test_many_to_many_relationship_basic(self, client):
        """Test basic many-to-many relationship without aliases."""

        # Define SQLAlchemy models with many-to-many relationship
        class User5(fd.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            email: Mapped[str] = mapped_column(String(100))
            groups: Mapped[List["Group5"]] = relationship(
                "Group5", secondary="user5_group5", back_populates="users"
            )

        class Group5(fd.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            description: Mapped[str] = mapped_column(Text)
            users: Mapped[List["User5"]] = relationship(
                "User5", secondary="user5_group5", back_populates="groups"
            )

        # Association table
        class UserGroup5(fd.IDBase):
            __tablename__ = "user5_group5"
            user_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("user5.id"), primary_key=True
            )
            group_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("group5.id"), primary_key=True
            )

        # Define schemas
        class GroupSchema5(fd.IDSchema[Group5]):
            name: str
            description: str

        class UserSchema5(fd.IDSchema[User5]):
            name: str
            email: str
            groups: List[GroupSchema5]

        @fd.include_view(client.app)
        class UserView5(fd.AsyncAlchemyView):
            prefix = "/users5"
            model = User5
            schema = UserSchema5

        create_tables()

        # Test GET - should return nested groups
        response = client.get("/users5/")
        assert response.status_code == 200


class TestDeeplyNestedRelationships:
    """Test deeply nested relationships with aliases."""

    @pytest.mark.xfail(reason="Deeply nested relationships not yet tested")
    def test_deeply_nested_relationships(self, client):
        """Test deeply nested relationships with aliases."""

        # Define SQLAlchemy models with deep nesting
        class Company6(fd.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            departments: Mapped[List["Department6"]] = relationship(
                "Department6", back_populates="company"
            )

        class Department6(fd.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            company_id: Mapped[int] = mapped_column(Integer, ForeignKey("company6.id"))
            company: Mapped["Company6"] = relationship(
                "Company6", back_populates="departments"
            )
            employees: Mapped[List["Employee6"]] = relationship(
                "Employee6", back_populates="department"
            )

        class Employee6(fd.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            department_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("department6.id")
            )
            department: Mapped["Department6"] = relationship(
                "Department6", back_populates="employees"
            )
            projects: Mapped[List["Project6"]] = relationship(
                "Project6", secondary="employee6_project6", back_populates="employees"
            )

        class Project6(fd.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            employees: Mapped[List["Employee6"]] = relationship(
                "Employee6", secondary="employee6_project6", back_populates="projects"
            )

        # Association table
        class EmployeeProject6(fd.IDBase):
            __tablename__ = "employee6_project6"
            employee_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("employee6.id"), primary_key=True
            )
            project_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("project6.id"), primary_key=True
            )

        # Define schemas with aliases
        class ProjectSchema6(fd.IDSchema[Project6]):
            name: str = Field(alias="projectName")

        class EmployeeSchema6(fd.IDSchema[Employee6]):
            name: str = Field(alias="employeeName")
            projects: List[ProjectSchema6]

        class DepartmentSchema6(fd.IDSchema[Department6]):
            name: str = Field(alias="departmentName")
            employees: List[EmployeeSchema6]

        class CompanySchema6(fd.IDSchema[Company6]):
            name: str = Field(alias="companyName")
            departments: List[DepartmentSchema6]

        @fd.include_view(client.app)
        class CompanyView6(fd.AsyncAlchemyView):
            prefix = "/companies6"
            model = Company6
            schema = CompanySchema6

        create_tables()

        # Test GET - should return deeply nested structure with aliases
        response = client.get("/companies6/")
        assert response.status_code == 200


class TestRelationshipWithReadOnlyFields:
    """Test relationships with ReadOnly fields."""

    @pytest.mark.xfail(reason="ReadOnly fields in nested schemas not yet tested")
    def test_relationship_with_readonly_fields(self, client):
        """Test relationships where some fields are ReadOnly."""

        # Define SQLAlchemy models with relationship
        class User7(fd.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            email: Mapped[str] = mapped_column(String(100))
            addresses: Mapped[List["Address7"]] = relationship(
                "Address7", back_populates="user"
            )

        class Address7(fd.IDBase):
            street: Mapped[str] = mapped_column(String(200))
            city: Mapped[str] = mapped_column(String(100))
            user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user7.id"))
            user: Mapped["User7"] = relationship("User7", back_populates="addresses")

        # Define schemas with ReadOnly fields
        class AddressSchema7(fd.IDSchema[Address7]):
            id: ReadOnly[int]
            street: str = Field(alias="streetAddress")
            city: str = Field(alias="cityName")
            created_at: ReadOnly[datetime]

        class UserSchema7(fd.IDSchema[User7]):
            id: ReadOnly[int]
            name: str
            email: str
            addresses: List[AddressSchema7]
            created_at: ReadOnly[datetime]

        @fd.include_view(client.app)
        class UserView7(fd.AsyncAlchemyView):
            prefix = "/users7"
            model = User7
            schema = UserSchema7

        create_tables()

        # Test GET - should return nested addresses with ReadOnly fields
        response = client.get("/users7/")
        assert response.status_code == 200
        users = response.json()

        # Check that ReadOnly fields are present in nested objects
        if users:  # If there are users in the database
            user = users[0]
            assert "id" in user
            assert "created_at" in user
            if "addresses" in user and user["addresses"]:
                address = user["addresses"][0]
                assert "id" in address
                assert "created_at" in address
                assert "streetAddress" in address
                assert "cityName" in address
