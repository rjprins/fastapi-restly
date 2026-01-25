"""CRUD tests for all models."""

import pytest
from fastapi_restly.testing import RestlyTestClient

from app.main import app


@pytest.fixture
def client() -> RestlyTestClient:
    """Create a test client."""
    return RestlyTestClient(app)


class TestOrganizationCRUD:
    """Test Organization CRUD operations."""

    def test_create_organization(self, client):
        """Test creating an organization."""
        response = client.post(
            "/organizations/",
            json={"name": "Acme Corp", "slug": "acme-corp"},
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
            "/organizations/",
            json={"name": "Test Org", "slug": "test-org"},
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
            "/organizations/",
            json={"name": "Old Name", "slug": "old-slug"},
        )
        org_id = response.json()["id"]

        # Update
        response = client.patch(
            f"/organizations/{org_id}",
            json={"name": "New Name"},
        )
        org = response.json()

        assert org["name"] == "New Name"
        assert org["slug"] == "old-slug"  # Unchanged

    def test_delete_organization(self, client):
        """Test deleting an organization."""
        # Create
        response = client.post(
            "/organizations/",
            json={"name": "To Delete", "slug": "to-delete"},
        )
        org_id = response.json()["id"]

        # Delete
        client.delete(f"/organizations/{org_id}")

        # Verify deleted
        response = client.get(f"/organizations/{org_id}", assert_status_code=404)


class TestUserCRUD:
    """Test User CRUD operations."""

    def test_create_user(self, client):
        """Test creating a user."""
        # Create org first
        response = client.post(
            "/organizations/",
            json={"name": "User Test Org", "slug": "user-test-org"},
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
            "/organizations/",
            json={"name": "Role Test Org", "slug": "role-test-org"},
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


class TestProjectCRUD:
    """Test Project CRUD operations."""

    def test_create_project(self, client):
        """Test creating a project."""
        # Create org
        response = client.post(
            "/organizations/",
            json={"name": "Project Test Org", "slug": "project-test-org"},
        )
        org_id = response.json()["id"]

        # Create project
        response = client.post(
            "/projects/",
            json={
                "name": "My Project",
                "description": "A test project",
                "organization_id": org_id,
            },
        )
        project = response.json()

        assert project["name"] == "My Project"
        assert project["status"] == "active"  # Default

    def test_archive_project(self, client):
        """Test archiving a project."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Archive Test Org", "slug": "archive-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "To Archive", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Archive
        response = client.patch(
            f"/projects/{project_id}",
            json={"status": "archived"},
        )
        project = response.json()

        assert project["status"] == "archived"


class TestTaskCRUD:
    """Test Task CRUD operations."""

    def test_create_task(self, client):
        """Test creating a task."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Task Test Org", "slug": "task-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Task Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create task
        response = client.post(
            "/tasks/",
            json={
                "title": "Implement feature",
                "description": "Build the feature",
                "project_id": project_id,
            },
        )
        task = response.json()

        assert task["title"] == "Implement feature"
        assert task["status"] == "todo"  # Default
        assert task["priority"] == 3  # Medium (default)

    def test_assign_task(self, client):
        """Test assigning a task to a user."""
        # Create org, user, project
        response = client.post(
            "/organizations/",
            json={"name": "Assign Test Org", "slug": "assign-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={
                "email": "assignee@example.com",
                "name": "Assignee",
                "organization_id": org_id,
            },
        )
        user_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Assign Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create task with assignee
        response = client.post(
            "/tasks/",
            json={
                "title": "Assigned Task",
                "project_id": project_id,
                "assignee_id": user_id,
            },
        )
        task = response.json()

        assert task["assignee_id"] == user_id

    def test_update_task_status(self, client):
        """Test updating task status."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Status Test Org", "slug": "status-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Status Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create task
        response = client.post(
            "/tasks/",
            json={"title": "Status Task", "project_id": project_id},
        )
        task_id = response.json()["id"]

        # Update to in_progress
        response = client.patch(
            f"/tasks/{task_id}",
            json={"status": "in_progress"},
        )
        assert response.json()["status"] == "in_progress"

        # Update to done
        response = client.patch(
            f"/tasks/{task_id}",
            json={"status": "done"},
        )
        assert response.json()["status"] == "done"

    def test_set_task_priority(self, client):
        """Test setting task priority."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Priority Test Org", "slug": "priority-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Priority Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create high priority task
        response = client.post(
            "/tasks/",
            json={
                "title": "Urgent Task",
                "project_id": project_id,
                "priority": 1,  # Critical
            },
        )
        task = response.json()

        assert task["priority"] == 1
