"""CRUD tests for the User model and the /me endpoints."""


class TestUserCRUD:
    """Test User CRUD operations."""

    def test_create_user(self, client, actor):
        """Test creating a user; it lands in the acting organization."""
        # Create user
        response = client.post(
            "/users", json={"email": "john@example.com", "name": "John Doe"}
        )
        user = response.json()

        assert user["email"] == "john@example.com"
        assert user["name"] == "John Doe"
        assert user["role"] == "member"  # Default role
        assert user["organization_id"] == actor.org_id
        assert user["created_by_id"] == actor.user_id

    def test_create_user_with_role(self, client):
        """Test creating a user with a specific role."""
        # Create admin user
        response = client.post(
            "/users",
            json={"email": "admin@example.com", "name": "Admin User", "role": "admin"},
        )
        user = response.json()

        assert user["role"] == "admin"


class TestMeEndpoints:
    """Test /users/me endpoints for self-service."""

    def test_get_me_is_the_acting_user(self, client, actor):
        """The client acts as Alice, so /users/me is Alice."""
        me = client.get("/users/me").json()

        assert me["id"] == actor.user_id
        assert me["organization_id"] == actor.org_id

    def test_get_me(self, client, auth_context):
        """Test GET /users/me returns current user."""
        # Create user
        response = client.post(
            "/users", json={"email": "current@example.com", "name": "Current User"}
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
        # Create user
        response = client.post(
            "/users", json={"email": "update-me@example.com", "name": "Before Update"}
        )
        user_id = response.json()["id"]

        with auth_context(user_id=user_id):
            response = client.patch("/users/me", json={"name": "Updated Name"})
            updated = response.json()

            assert updated["name"] == "Updated Name"
            assert updated["email"] == "update-me@example.com"
