"""CRUD tests for the Organization model."""


class TestOrganizationCRUD:
    """Test Organization CRUD operations."""

    def test_create_organization(self, client):
        """Test creating an organization."""
        response = client.post(
            "/organizations/", json={"name": "Acme Corp", "slug": "acme-corp"}
        )
        org = response.json()

        assert org["name"] == "Acme Corp"
        assert org["slug"] == "acme-corp"
        assert "id" in org
        assert "created_at" in org

    def test_get_organization(self, client):
        """Test getting an organization by ID."""
        # Create first
        response = client.post(
            "/organizations/", json={"name": "Test Org", "slug": "test-org"}
        )
        org_id = response.json()["id"]

        # Get
        response = client.get(f"/organizations/{org_id}")
        org = response.json()

        assert org["name"] == "Test Org"
        assert org["id"] == org_id

    def test_list_organizations(self, client):
        """Test listing organizations."""
        # Create multiple
        client.post("/organizations/", json={"name": "Org 1", "slug": "org-1"})
        client.post("/organizations/", json={"name": "Org 2", "slug": "org-2"})

        # List
        response = client.get("/organizations/")
        orgs = response.json()

        assert len(orgs) >= 2

    def test_update_organization(self, client):
        """Test updating an organization."""
        # Create
        response = client.post(
            "/organizations/", json={"name": "Old Name", "slug": "old-slug"}
        )
        org_id = response.json()["id"]

        # Update
        response = client.patch(f"/organizations/{org_id}", json={"name": "New Name"})
        org = response.json()

        assert org["name"] == "New Name"
        assert org["slug"] == "old-slug"  # Unchanged

    def test_delete_organization(self, client):
        """Test deleting an organization."""
        # Create
        response = client.post(
            "/organizations/", json={"name": "To Delete", "slug": "to-delete"}
        )
        org_id = response.json()["id"]

        # Delete
        client.delete(f"/organizations/{org_id}")

        # Verify deleted
        response = client.get(f"/organizations/{org_id}", assert_status_code=404)
