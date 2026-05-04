"""CRUD tests for the User model and the /me endpoints."""


class TestUserCRUD:
    """Test User CRUD operations."""

    def test_create_user(self, client):
        """Test creating a user."""
        # Create org first
        response = client.post(
            "/organizations/", json={"name": "User Test Org", "slug": "user-test-org"}
        )
        org_id = response.json()["id"]

        # Create user
        response = client.post(
            "/users/",
            json={
                "email": "john@example.com",
                "name": "John Doe",
                "organization_id": org_id,
            },
        )
        user = response.json()

        assert user["email"] == "john@example.com"
        assert user["name"] == "John Doe"
        assert user["role"] == "member"  # Default role

    def test_create_user_with_role(self, client):
        """Test creating a user with a specific role."""
        # Create org
        response = client.post(
            "/organizations/", json={"name": "Role Test Org", "slug": "role-test-org"}
        )
        org_id = response.json()["id"]

        # Create admin user
        response = client.post(
            "/users/",
            json={
                "email": "admin@example.com",
                "name": "Admin User",
                "role": "admin",
                "organization_id": org_id,
            },
        )
        user = response.json()

        assert user["role"] == "admin"


class TestMeEndpoints:
    """Test /users/me endpoints for self-service."""

    def test_get_me(self, client, auth_context):
        """Test GET /users/me returns current user."""
        # Create org and user
        response = client.post(
            "/organizations/", json={"name": "Me Test Org", "slug": "me-test-org"}
        )
        org_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={
                "email": "current@example.com",
                "name": "Current User",
                "organization_id": org_id,
            },
        )
        created_user_id = response.json()["id"]

        with auth_context(user_id=created_user_id):
            response = client.get("/users/me")
            me = response.json()

            assert me["id"] == created_user_id
            assert "email" in me
            assert "name" in me

    def test_update_me(self, client, auth_context):
        """Test PATCH /users/me updates current user's profile."""
        # Create org and user
        response = client.post(
            "/organizations/", json={"name": "Me Update Org", "slug": "me-update-org"}
        )
        org_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={
                "email": "update-me@example.com",
                "name": "Before Update",
                "organization_id": org_id,
            },
        )
        user_id = response.json()["id"]

        with auth_context(user_id=user_id):
            response = client.patch("/users/me", json={"name": "Updated Name"})
            updated = response.json()

            assert updated["name"] == "Updated Name"
            assert updated["email"] == "update-me@example.com"
