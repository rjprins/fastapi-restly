"""Test WriteOnly API behavior in endpoints."""

import pytest
import asyncio
from datetime import datetime
from typing import List

import fastapi_ding as fd
from fastapi_ding.schemas import WriteOnly, BaseSchema
from fastapi_ding.testing import DingTestClient
from fastapi_ding._globals import fa_globals
from .conftest import create_tables


class TestWriteOnlyAPIBasicBehavior:
    """Test basic WriteOnly API behavior."""

    @pytest.mark.xfail(reason="WriteOnly fields not yet implemented in API responses")
    def test_writeonly_fields_excluded_from_get_response(self, client):
        """Test that WriteOnly fields are excluded from GET responses."""

        # Define schema with WriteOnly fields
        class UserSchema(fd.IDSchema):
            id: int  # Regular field
            name: str
            email: str
            password: WriteOnly[str]  # WriteOnly field
            secret_token: WriteOnly[str]  # Another WriteOnly field

        # Create a simple model
        class User(fd.IDBase):
            name: str
            email: str
            password: str
            secret_token: str

        @fd.include_view(client.app)
        class UserView(fd.AsyncAlchemyView):
            prefix = "/users"
            model = User
            schema = UserSchema

        create_tables()

        # Create a user first
        response = client.post(
            "/users/",
            json={
                "name": "John Doe",
                "email": "john@example.com",
                "password": "secret123",
                "secret_token": "abc123",
            },
        )
        assert response.status_code == 201
        created_user = response.json()

        # Test GET - should NOT include WriteOnly fields
        response = client.get(f"/users/{created_user['id']}")
        assert response.status_code == 200
        user = response.json()

        # Should include regular fields
        assert "id" in user
        assert "name" in user
        assert "email" in user

        # Should NOT include WriteOnly fields
        assert "password" not in user
        assert "secret_token" not in user

    @pytest.mark.xfail(reason="WriteOnly fields not yet implemented in API responses")
    def test_writeonly_fields_accepted_in_post_request(self, client):
        """Test that WriteOnly fields are accepted in POST requests."""

        class UserSchema(fd.IDSchema):
            id: int
            name: str
            email: str
            password: WriteOnly[str]

        class User(fd.IDBase):
            name: str
            email: str
            password: str

        @fd.include_view(client.app)
        class UserView(fd.AsyncAlchemyView):
            prefix = "/users"
            model = User
            schema = UserSchema

        create_tables()

        # Test POST with WriteOnly field - should succeed
        response = client.post(
            "/users/",
            json={
                "name": "Jane Doe",
                "email": "jane@example.com",
                "password": "secret456",
            },
        )
        assert response.status_code == 201
        created_user = response.json()

        # Should include regular fields in response
        assert "id" in created_user
        assert "name" in created_user
        assert "email" in created_user

        # Should NOT include WriteOnly field in response
        assert "password" not in created_user

    @pytest.mark.xfail(reason="WriteOnly fields not yet implemented in API responses")
    def test_writeonly_fields_accepted_in_put_request(self, client):
        """Test that WriteOnly fields are accepted in PUT requests."""

        class UserSchema(fd.IDSchema):
            id: int
            name: str
            email: str
            password: WriteOnly[str]

        class User(fd.IDBase):
            name: str
            email: str
            password: str

        @fd.include_view(client.app)
        class UserView(fd.AsyncAlchemyView):
            prefix = "/users"
            model = User
            schema = UserSchema

        create_tables()

        # Create a user first
        response = client.post(
            "/users/",
            json={
                "name": "Bob Smith",
                "email": "bob@example.com",
                "password": "oldpassword",
            },
        )
        assert response.status_code == 201
        created_user = response.json()

        # Test PUT with WriteOnly field - should succeed
        response = client.put(
            f"/users/{created_user['id']}",
            json={
                "name": "Bob Smith Updated",
                "email": "bob.updated@example.com",
                "password": "newpassword",
            },
        )
        assert response.status_code == 200
        updated_user = response.json()

        # Should include regular fields in response
        assert "id" in updated_user
        assert "name" in updated_user
        assert "email" in updated_user

        # Should NOT include WriteOnly field in response
        assert "password" not in updated_user

    @pytest.mark.xfail(reason="WriteOnly fields not yet implemented in API responses")
    def test_writeonly_fields_excluded_from_list_response(self, client):
        """Test that WriteOnly fields are excluded from list GET responses."""

        class UserSchema(fd.IDSchema):
            id: int
            name: str
            email: str
            password: WriteOnly[str]

        class User(fd.IDBase):
            name: str
            email: str
            password: str

        @fd.include_view(client.app)
        class UserView(fd.AsyncAlchemyView):
            prefix = "/users"
            model = User
            schema = UserSchema

        create_tables()

        # Create multiple users
        for i in range(3):
            response = client.post(
                "/users/",
                json={
                    "name": f"User {i}",
                    "email": f"user{i}@example.com",
                    "password": f"password{i}",
                },
            )
            assert response.status_code == 201

        # Test GET list - should NOT include WriteOnly fields
        response = client.get("/users/")
        assert response.status_code == 200
        users = response.json()

        assert len(users) == 3
        for user in users:
            # Should include regular fields
            assert "id" in user
            assert "name" in user
            assert "email" in user

            # Should NOT include WriteOnly fields
            assert "password" not in user


class TestWriteOnlyWithMixedFields:
    """Test WriteOnly fields mixed with ReadOnly and regular fields."""

    @pytest.mark.xfail(reason="WriteOnly fields not yet implemented in API responses")
    def test_mixed_readonly_writeonly_regular_fields(self, client):
        """Test API behavior with ReadOnly, WriteOnly, and regular fields."""

        from fastapi_ding.schemas import ReadOnly

        class UserSchema(fd.IDSchema):
            id: ReadOnly[int]  # ReadOnly field
            name: str  # Regular field
            email: str  # Regular field
            password: WriteOnly[str]  # WriteOnly field
            created_at: ReadOnly[datetime]  # ReadOnly field
            secret_token: WriteOnly[str]  # WriteOnly field

        class User(fd.IDBase):
            name: str
            email: str
            password: str
            secret_token: str

        @fd.include_view(client.app)
        class UserView(fd.AsyncAlchemyView):
            prefix = "/users"
            model = User
            schema = UserSchema

        create_tables()

        # Test POST - should accept WriteOnly fields, ignore ReadOnly fields
        response = client.post(
            "/users/",
            json={
                "name": "Mixed User",
                "email": "mixed@example.com",
                "password": "secret123",
                "secret_token": "abc123",
                # Note: id and created_at are ReadOnly, so not included
            },
        )
        assert response.status_code == 201
        created_user = response.json()

        # Response should include ReadOnly fields (id, created_at)
        assert "id" in created_user
        assert "created_at" in created_user

        # Response should include regular fields
        assert "name" in created_user
        assert "email" in created_user

        # Response should NOT include WriteOnly fields
        assert "password" not in created_user
        assert "secret_token" not in created_user

        # Test GET - should include ReadOnly and regular fields, exclude WriteOnly
        response = client.get(f"/users/{created_user['id']}")
        assert response.status_code == 200
        user = response.json()

        # Should include ReadOnly fields
        assert "id" in user
        assert "created_at" in user

        # Should include regular fields
        assert "name" in user
        assert "email" in user

        # Should NOT include WriteOnly fields
        assert "password" not in user
        assert "secret_token" not in user


class TestWriteOnlyWithAliases:
    """Test WriteOnly fields with field aliases."""

    @pytest.mark.xfail(reason="WriteOnly fields with aliases not yet tested")
    def test_writeonly_fields_with_aliases(self, client):
        """Test that WriteOnly fields work correctly with aliases."""

        from pydantic import Field

        class UserSchema(fd.IDSchema):
            id: int
            name: str
            email: str
            password: WriteOnly[str] = Field(alias="userPassword")
            secret_token: WriteOnly[str] = Field(alias="secretKey")

        class User(fd.IDBase):
            name: str
            email: str
            password: str
            secret_token: str

        @fd.include_view(client.app)
        class UserView(fd.AsyncAlchemyView):
            prefix = "/users"
            model = User
            schema = UserSchema

        create_tables()

        # Test POST with aliases - should succeed
        response = client.post(
            "/users/",
            json={
                "name": "Alias User",
                "email": "alias@example.com",
                "userPassword": "secret123",
                "secretKey": "abc123",
            },
        )
        assert response.status_code == 201
        created_user = response.json()

        # Should include regular fields
        assert "id" in created_user
        assert "name" in created_user
        assert "email" in created_user

        # Should NOT include WriteOnly fields (neither with aliases nor field names)
        assert "userPassword" not in created_user
        assert "secretKey" not in created_user
        assert "password" not in created_user
        assert "secret_token" not in created_user

        # Test GET - should exclude WriteOnly fields
        response = client.get(f"/users/{created_user['id']}")
        assert response.status_code == 200
        user = response.json()

        # Should include regular fields
        assert "id" in user
        assert "name" in user
        assert "email" in user

        # Should NOT include WriteOnly fields
        assert "userPassword" not in user
        assert "secretKey" not in user
        assert "password" not in user
        assert "secret_token" not in user


class TestWriteOnlyInNestedSchemas:
    """Test WriteOnly fields in nested schemas."""

    @pytest.mark.xfail(reason="WriteOnly fields in nested schemas not yet tested")
    def test_writeonly_fields_in_nested_schemas(self, client):
        """Test WriteOnly fields in nested schema relationships."""

        from sqlalchemy.orm import Mapped, mapped_column, relationship
        from sqlalchemy import String, Integer, ForeignKey

        # Define SQLAlchemy models with relationship
        class User(fd.IDBase):
            name: Mapped[str] = mapped_column(String(100))
            email: Mapped[str] = mapped_column(String(100))
            password: Mapped[str] = mapped_column(String(100))
            profiles: Mapped[List["Profile"]] = relationship(
                "Profile", back_populates="user"
            )

        class Profile(fd.IDBase):
            bio: Mapped[str] = mapped_column(String(500))
            website: Mapped[str] = mapped_column(String(200))
            secret_key: Mapped[str] = mapped_column(String(100))
            user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"))
            user: Mapped["User"] = relationship("User", back_populates="profiles")

        # Define schemas with WriteOnly fields
        class ProfileSchema(fd.IDSchema[Profile]):
            id: int
            bio: str
            website: str
            secret_key: WriteOnly[str]

        class UserSchema(fd.IDSchema[User]):
            id: int
            name: str
            email: str
            password: WriteOnly[str]
            profiles: List[ProfileSchema]

        @fd.include_view(client.app)
        class UserView(fd.AsyncAlchemyView):
            prefix = "/users"
            model = User
            schema = UserSchema

        create_tables()

        # Test POST with nested WriteOnly fields - should succeed
        response = client.post(
            "/users/",
            json={
                "name": "Nested User",
                "email": "nested@example.com",
                "password": "secret123",
                "profiles": [
                    {
                        "bio": "My bio",
                        "website": "https://example.com",
                        "secret_key": "profile_secret",
                    }
                ],
            },
        )
        assert response.status_code == 201
        created_user = response.json()

        # Should include regular fields
        assert "id" in created_user
        assert "name" in created_user
        assert "email" in created_user

        # Should NOT include WriteOnly fields
        assert "password" not in created_user

        # Should include nested profiles but exclude their WriteOnly fields
        assert "profiles" in created_user
        profiles = created_user["profiles"]
        assert len(profiles) == 1
        profile = profiles[0]

        # Profile should include regular fields
        assert "id" in profile
        assert "bio" in profile
        assert "website" in profile

        # Profile should NOT include WriteOnly fields
        assert "secret_key" not in profile

        # Test GET - should exclude WriteOnly fields from nested objects
        response = client.get(f"/users/{created_user['id']}")
        assert response.status_code == 200
        user = response.json()

        # Should include regular fields
        assert "id" in user
        assert "name" in user
        assert "email" in user

        # Should NOT include WriteOnly fields
        assert "password" not in user

        # Should include nested profiles but exclude their WriteOnly fields
        assert "profiles" in user
        profiles = user["profiles"]
        assert len(profiles) == 1
        profile = profiles[0]

        # Profile should include regular fields
        assert "id" in profile
        assert "bio" in profile
        assert "website" in profile

        # Profile should NOT include WriteOnly fields
        assert "secret_key" not in profile
