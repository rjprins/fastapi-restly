"""CRUD tests for the Organization model."""

from dataclasses import asdict

import pytest
from app.current import Current
from app.organizations.views import OrganizationView
from app.users.roles import UserRole

import fastapi_restly as fr


class TestOrganizationCRUD:
    """Test Organization CRUD operations."""

    def test_create_organization(self, client):
        """Test creating an organization."""
        response = client.post(
            "/organizations", json={"name": "Acme Corp", "slug": "acme-corp"}
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
            "/organizations", json={"name": "Test Org", "slug": "test-org"}
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
        client.post("/organizations", json={"name": "Org 1", "slug": "org-1"})
        client.post("/organizations", json={"name": "Org 2", "slug": "org-2"})

        # List
        response = client.get("/organizations")
        orgs = response.json()["data"]

        assert len(orgs) >= 2

    def test_update_organization(self, client):
        """Test updating an organization."""
        # Create
        response = client.post(
            "/organizations", json={"name": "Old Name", "slug": "old-slug"}
        )
        org_id = response.json()["id"]

        # Update
        response = client.patch(f"/organizations/{org_id}", json={"name": "New Name"})
        org = response.json()

        assert org["name"] == "New Name"
        assert org["slug"] == "old-slug"  # Unchanged

    def test_delete_organization(self, client, actor, as_admin):
        """A platform admin can delete an empty organization."""
        # Create
        response = client.post(
            "/organizations", json={"name": "To Delete", "slug": "to-delete"}
        )
        org_id = response.json()["id"]

        # Delete
        with as_admin(actor.org_id):
            deleted = client.delete(f"/organizations/{org_id}")
        assert deleted.status_code == 204
        assert deleted.content == b""

        # Verify deleted
        response = client.get(f"/organizations/{org_id}", assert_status_code=404)

    def test_delete_requires_authentication(self, client, actor, anonymous):
        with anonymous():
            client.delete(f"/organizations/{actor.org_id}", assert_status_code=401)
        assert client.get(f"/organizations/{actor.org_id}").json()["id"] == actor.org_id

    @pytest.mark.parametrize("role", list(UserRole))
    @pytest.mark.parametrize("foreign", [False, True])
    def test_delete_rejects_non_platform_admins(
        self, client, actor, auth_context, new_tenant, role, foreign
    ):
        target = new_tenant("beta") if foreign else actor
        with auth_context(role=role, is_admin=False):
            client.delete(f"/organizations/{target.org_id}", assert_status_code=403)
        assert (
            client.get(f"/organizations/{target.org_id}").json()["id"] == target.org_id
        )

    def test_admin_delete_missing_organization(self, client, actor, as_admin):
        with as_admin(actor.org_id):
            client.delete("/organizations/999999", assert_status_code=404)

    def test_admin_delete_cascades_in_another_organization(
        self, client, actor, as_admin, new_tenant
    ):
        own_project = client.post("/projects", json={"name": "Keep"}).json()
        beta = new_tenant("beta")
        with beta.acting():
            project = client.post("/projects", json={"name": "Delete"}).json()
            task = client.post(
                "/tasks", json={"title": "Delete", "project_id": project["id"]}
            ).json()
            label = client.post("/labels", json={"name": "Delete"}).json()

        with as_admin(actor.org_id):
            client.delete(f"/organizations/{beta.org_id}")
            for path in (
                f"/users/{beta.user_id}",
                f"/projects/{project['id']}",
                f"/tasks/{task['id']}",
                f"/labels/{label['id']}",
            ):
                client.get(path, assert_status_code=404)

        client.get(f"/organizations/{beta.org_id}", assert_status_code=404)
        assert client.get(f"/organizations/{actor.org_id}").json()["id"] == actor.org_id
        assert (
            client.get(f"/projects/{own_project['id']}").json()["id"]
            == own_project["id"]
        )

    def test_other_organization_routes_remain_public(self, restly_client):
        org = restly_client.post(
            "/organizations", json={"name": "Public", "slug": "public"}
        ).json()
        restly_client.get("/organizations")
        restly_client.get(f"/organizations/{org['id']}")
        updated = restly_client.patch(
            f"/organizations/{org['id']}", json={"name": "Renamed"}
        ).json()
        assert updated["name"] == "Renamed"


async def test_delete_handler_rejects_non_admin(async_client, async_actor):
    with Current.bind(**asdict(async_actor)):
        async with fr.open_async_session() as session:
            view = OrganizationView(request=None, session=session)
            with pytest.raises(fr.exc.Forbidden):
                await view.handle_delete(async_actor.org_id)
    await async_client.get(f"/organizations/{async_actor.org_id}")
