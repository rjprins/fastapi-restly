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

    def test_create_subtask(self, client):
        """Test creating a subtask (self-referential relationship)."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Subtask Test Org", "slug": "subtask-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Subtask Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create parent task
        response = client.post(
            "/tasks/",
            json={"title": "Parent Task", "project_id": project_id},
        )
        parent_id = response.json()["id"]

        # Create subtask
        response = client.post(
            "/tasks/",
            json={
                "title": "Subtask 1",
                "project_id": project_id,
                "parent_id": parent_id,
            },
        )
        subtask = response.json()

        assert subtask["title"] == "Subtask 1"
        assert subtask["parent_id"] == parent_id

    def test_get_subtask_with_parent(self, client):
        """Test retrieving a subtask shows parent_id."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Get Subtask Org", "slug": "get-subtask-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Get Subtask Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create parent task
        response = client.post(
            "/tasks/",
            json={"title": "Parent", "project_id": project_id},
        )
        parent_id = response.json()["id"]

        # Create subtask
        response = client.post(
            "/tasks/",
            json={"title": "Child", "project_id": project_id, "parent_id": parent_id},
        )
        subtask_id = response.json()["id"]

        # Get subtask by id
        response = client.get(f"/tasks/{subtask_id}")
        subtask = response.json()

        assert subtask["parent_id"] == parent_id
        assert subtask["title"] == "Child"


class TestLabelCRUD:
    """Test Label and TaskLabel CRUD operations."""

    def test_create_label(self, client):
        """Test creating a label."""
        # Create org
        response = client.post(
            "/organizations/",
            json={"name": "Label Test Org", "slug": "label-test-org"},
        )
        org_id = response.json()["id"]

        # Create label
        response = client.post(
            "/labels/",
            json={
                "name": "urgent",
                "color": "#ff0000",
                "organization_id": org_id,
            },
        )
        label = response.json()

        assert label["name"] == "urgent"
        assert label["color"] == "#ff0000"

    def test_add_label_to_task(self, client):
        """Test adding a label to a task via TaskLabel."""
        # Create org, project, task, and label
        response = client.post(
            "/organizations/",
            json={"name": "TaskLabel Test Org", "slug": "tasklabel-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={
                "email": "labeler@example.com",
                "name": "Labeler",
                "organization_id": org_id,
            },
        )
        user_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Label Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        response = client.post(
            "/tasks/",
            json={"title": "Labeled Task", "project_id": project_id},
        )
        task_id = response.json()["id"]

        response = client.post(
            "/labels/",
            json={"name": "bug", "color": "#ff0000", "organization_id": org_id},
        )
        label_id = response.json()["id"]

        # Add label to task with metadata (who added it)
        response = client.post(
            "/task-labels/",
            json={
                "task_id": task_id,
                "label_id": label_id,
                "added_by_id": user_id,
            },
        )
        task_label = response.json()

        assert task_label["task_id"] == task_id
        assert task_label["label_id"] == label_id
        assert task_label["added_by_id"] == user_id


class TestPolymorphicTasks:
    """Test polymorphic task types (bug, feature, task)."""

    def test_create_bug_task(self, client):
        """Test creating a bug with bug-specific fields."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Bug Test Org", "slug": "bug-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Bug Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create bug
        response = client.post(
            "/tasks/",
            json={
                "title": "Login button broken",
                "task_type": "bug",
                "severity": 2,
                "steps_to_reproduce": "1. Click login\n2. Nothing happens",
                "project_id": project_id,
            },
        )
        bug = response.json()

        assert bug["task_type"] == "bug"
        assert bug["severity"] == 2
        assert bug["steps_to_reproduce"] == "1. Click login\n2. Nothing happens"
        assert bug["story_points"] is None  # Feature field not set

    def test_create_feature_task(self, client):
        """Test creating a feature with feature-specific fields."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Feature Test Org", "slug": "feature-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Feature Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create feature
        response = client.post(
            "/tasks/",
            json={
                "title": "Add dark mode",
                "task_type": "feature",
                "story_points": 5,
                "acceptance_criteria": "- Toggle in settings\n- Persists across sessions",
                "project_id": project_id,
            },
        )
        feature = response.json()

        assert feature["task_type"] == "feature"
        assert feature["story_points"] == 5
        assert feature["acceptance_criteria"] == "- Toggle in settings\n- Persists across sessions"
        assert feature["severity"] is None  # Bug field not set

    def test_filter_by_task_type(self, client):
        """Test filtering tasks by type."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Type Filter Org", "slug": "type-filter-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Type Filter Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create tasks of different types (bugs require severity for create, but not for filtering)
        client.post("/tasks/", json={"title": "Bug 1", "task_type": "bug", "severity": 2, "project_id": project_id})
        client.post("/tasks/", json={"title": "Feature 1", "task_type": "feature", "project_id": project_id})
        client.post("/tasks/", json={"title": "Task 1", "task_type": "task", "project_id": project_id})

        # Filter by bug type
        response = client.get("/tasks/?filter[task_type]=bug")
        bugs = response.json()

        # All returned should be bugs
        for task in bugs:
            assert task["task_type"] == "bug"


class TestBulkOperations:
    """Test bulk create/delete endpoints."""

    def test_bulk_create_tasks(self, client):
        """Test creating multiple tasks at once."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Bulk Create Org", "slug": "bulk-create-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Bulk Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Bulk create tasks
        response = client.post(
            "/tasks/bulk",
            json={
                "items": [
                    {"title": "Task 1", "project_id": project_id},
                    {"title": "Task 2", "project_id": project_id},
                    {"title": "Task 3", "project_id": project_id},
                ]
            },
        )
        result = response.json()

        assert result["success"] == 3
        assert result["failed"] == 0

    def test_bulk_delete_tasks(self, client):
        """Test deleting multiple tasks at once."""
        # Create org, project, and tasks
        response = client.post(
            "/organizations/",
            json={"name": "Bulk Delete Org", "slug": "bulk-delete-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Bulk Delete Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create tasks individually
        task_ids = []
        for i in range(3):
            response = client.post(
                "/tasks/",
                json={"title": f"To Delete {i}", "project_id": project_id},
            )
            task_ids.append(response.json()["id"])

        # Bulk delete
        response = client.post(
            "/tasks/bulk-delete",
            json={"ids": task_ids},
        )
        result = response.json()

        assert result["success"] == 3
        assert result["failed"] == 0

        # Verify deleted
        for task_id in task_ids:
            client.get(f"/tasks/{task_id}", assert_status_code=404)

    def test_bulk_delete_partial_failure(self, client):
        """Test bulk delete with some invalid IDs."""
        # Create org, project, and one task
        response = client.post(
            "/organizations/",
            json={"name": "Bulk Partial Org", "slug": "bulk-partial-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Bulk Partial Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        response = client.post(
            "/tasks/",
            json={"title": "Real Task", "project_id": project_id},
        )
        real_id = response.json()["id"]

        # Bulk delete with mix of valid and invalid IDs
        response = client.post(
            "/tasks/bulk-delete",
            json={"ids": [real_id, 99999, 99998]},
        )
        result = response.json()

        assert result["success"] == 1
        assert result["failed"] == 2
        assert len(result["errors"]) == 2


class TestProjectClone:
    """Test project cloning functionality."""

    def test_clone_project_with_tasks(self, client):
        """Test cloning a project including all tasks."""
        # Create org and project with tasks
        response = client.post(
            "/organizations/",
            json={"name": "Clone Test Org", "slug": "clone-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={
                "name": "Original Project",
                "description": "Original description",
                "organization_id": org_id,
            },
        )
        project_id = response.json()["id"]

        # Add tasks
        client.post("/tasks/", json={"title": "Task 1", "project_id": project_id})
        client.post("/tasks/", json={"title": "Task 2", "project_id": project_id})
        client.post("/tasks/", json={"title": "Task 3", "project_id": project_id})

        # Clone project
        response = client.post(
            f"/projects/{project_id}/clone",
            json={"new_name": "Cloned Project"},
        )
        cloned = response.json()

        assert cloned["name"] == "Cloned Project"
        assert cloned["description"] == "Original description"
        assert cloned["status"] == "active"
        assert cloned["id"] != project_id

        # Verify tasks were cloned
        response = client.get(f"/tasks/?filter[project_id]={cloned['id']}")
        cloned_tasks = response.json()
        assert len(cloned_tasks) == 3

    def test_clone_project_default_name(self, client):
        """Test cloning with default name appends (Copy)."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Clone Name Org", "slug": "clone-name-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "My Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Clone without specifying name
        response = client.post(
            f"/projects/{project_id}/clone",
            json={},
        )
        cloned = response.json()

        assert cloned["name"] == "My Project (Copy)"

    def test_clone_project_without_tasks(self, client):
        """Test cloning without including tasks."""
        # Create org and project with tasks
        response = client.post(
            "/organizations/",
            json={"name": "Clone No Tasks Org", "slug": "clone-no-tasks-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Project With Tasks", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        client.post("/tasks/", json={"title": "Task 1", "project_id": project_id})

        # Clone without tasks
        response = client.post(
            f"/projects/{project_id}/clone",
            json={"include_tasks": False},
        )
        cloned = response.json()

        # Verify no tasks were cloned
        response = client.get(f"/tasks/?filter[project_id]={cloned['id']}")
        cloned_tasks = response.json()
        assert len(cloned_tasks) == 0


class TestSoftDelete:
    """Test soft delete functionality for projects."""

    def test_soft_delete_project(self, client):
        """Test that DELETE soft-deletes instead of hard delete."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Soft Delete Org", "slug": "soft-delete-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "To Soft Delete", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Soft delete (returns 200 with body, not 204)
        response = client.delete(f"/projects/{project_id}", assert_status_code=200)
        deleted = response.json()

        assert deleted["deleted_at"] is not None

        # Should not appear in list by default
        response = client.get("/projects/")
        projects = response.json()
        project_ids = [p["id"] for p in projects]
        assert project_id not in project_ids

    def test_include_deleted_projects(self, client):
        """Test that include_deleted=true shows deleted projects."""
        # Create org and projects
        response = client.post(
            "/organizations/",
            json={"name": "Include Deleted Org", "slug": "include-deleted-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Active Project", "organization_id": org_id},
        )
        active_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Deleted Project", "organization_id": org_id},
        )
        deleted_id = response.json()["id"]

        # Soft delete one
        client.delete(f"/projects/{deleted_id}", assert_status_code=200)

        # List without include_deleted
        response = client.get("/projects/")
        projects = response.json()
        project_ids = [p["id"] for p in projects]
        assert active_id in project_ids
        assert deleted_id not in project_ids

        # List with include_deleted=true
        response = client.get("/projects/?include_deleted=true")
        projects = response.json()
        project_ids = [p["id"] for p in projects]
        assert active_id in project_ids
        assert deleted_id in project_ids

    def test_restore_deleted_project(self, client):
        """Test restoring a soft-deleted project."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Restore Org", "slug": "restore-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "To Restore", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Soft delete
        client.delete(f"/projects/{project_id}", assert_status_code=200)

        # Restore
        response = client.post(f"/projects/{project_id}/restore")
        restored = response.json()

        assert restored["deleted_at"] is None

        # Should appear in list again
        response = client.get("/projects/")
        projects = response.json()
        project_ids = [p["id"] for p in projects]
        assert project_id in project_ids

    def test_restore_non_deleted_project_fails(self, client):
        """Test that restoring a non-deleted project fails."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Restore Fail Org", "slug": "restore-fail-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Not Deleted", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Try to restore non-deleted project
        response = client.post(
            f"/projects/{project_id}/restore",
            assert_status_code=400,
        )


class TestOptimisticLocking:
    """Test optimistic locking via version field."""

    def test_update_with_correct_version(self, client):
        """Test that update works with correct version."""
        # Create org, project, and task
        response = client.post(
            "/organizations/",
            json={"name": "Version Test Org", "slug": "version-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Version Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        response = client.post(
            "/tasks/",
            json={"title": "Versioned Task", "project_id": project_id},
        )
        task = response.json()
        task_id = task["id"]
        assert task["version"] == 1

        # Update with correct version
        response = client.patch(
            f"/tasks/{task_id}",
            json={"title": "Updated Title", "version": 1},
        )
        updated = response.json()

        assert updated["title"] == "Updated Title"
        assert updated["version"] == 2

    def test_update_with_wrong_version_fails(self, client):
        """Test that update fails with wrong version (409 Conflict)."""
        # Create org, project, and task
        response = client.post(
            "/organizations/",
            json={"name": "Conflict Test Org", "slug": "conflict-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Conflict Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        response = client.post(
            "/tasks/",
            json={"title": "Conflict Task", "project_id": project_id},
        )
        task_id = response.json()["id"]

        # Try to update with wrong version
        response = client.patch(
            f"/tasks/{task_id}",
            json={"title": "Should Fail", "version": 5},
            assert_status_code=409,
        )
        error = response.json()
        assert "Conflict" in error["detail"]

    def test_update_without_version_works(self, client):
        """Test that update without version skips optimistic locking check."""
        # Create org, project, and task
        response = client.post(
            "/organizations/",
            json={"name": "No Version Org", "slug": "no-version-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "No Version Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        response = client.post(
            "/tasks/",
            json={"title": "No Version Task", "project_id": project_id},
        )
        task_id = response.json()["id"]

        # Update without version - should still work and increment version
        response = client.patch(
            f"/tasks/{task_id}",
            json={"title": "Updated Without Version"},
        )
        updated = response.json()

        assert updated["title"] == "Updated Without Version"
        assert updated["version"] == 2


class TestComputedFields:
    """Test computed fields in schemas (via /stats endpoint)."""

    def test_project_stats_computed_fields(self, client):
        """Test that /projects/{id}/stats returns computed metrics."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Computed Test Org", "slug": "computed-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Computed Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Add tasks with different statuses
        client.post("/tasks/", json={"title": "Task 1", "status": "todo", "project_id": project_id})
        client.post("/tasks/", json={"title": "Task 2", "status": "in_progress", "project_id": project_id})
        client.post("/tasks/", json={"title": "Task 3", "status": "done", "project_id": project_id})
        client.post("/tasks/", json={"title": "Task 4", "status": "done", "project_id": project_id})

        # Get stats - should have computed counts
        response = client.get(f"/projects/{project_id}/stats")
        stats = response.json()

        assert stats["total_tasks"] == 4
        assert stats["done_count"] == 2
        assert stats["completion_percent"] == 50.0

    def test_project_stats_empty(self, client):
        """Test computed fields with no tasks."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Empty Computed Org", "slug": "empty-computed-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Empty Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Get stats - should have zero counts
        response = client.get(f"/projects/{project_id}/stats")
        stats = response.json()

        assert stats["total_tasks"] == 0
        assert stats["completion_percent"] == 0.0


class TestNestedRoutes:
    """Test nested routes for tasks within projects."""

    def test_list_project_tasks(self, client):
        """Test GET /projects/{id}/tasks lists only that project's tasks."""
        # Create org and two projects
        response = client.post(
            "/organizations/",
            json={"name": "Nested Test Org", "slug": "nested-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Project 1", "organization_id": org_id},
        )
        project1_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Project 2", "organization_id": org_id},
        )
        project2_id = response.json()["id"]

        # Add tasks to each project
        client.post("/tasks/", json={"title": "P1 Task 1", "project_id": project1_id})
        client.post("/tasks/", json={"title": "P1 Task 2", "project_id": project1_id})
        client.post("/tasks/", json={"title": "P2 Task 1", "project_id": project2_id})

        # List tasks for project 1
        response = client.get(f"/projects/{project1_id}/tasks")
        tasks = response.json()

        assert len(tasks) == 2
        assert all(t["project_id"] == project1_id for t in tasks)

    def test_create_task_via_nested_route(self, client):
        """Test POST /projects/{id}/tasks creates task with correct project_id."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Nested Create Org", "slug": "nested-create-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Nested Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create task via nested route (no project_id in body)
        response = client.post(
            f"/projects/{project_id}/tasks",
            json={"title": "Nested Task"},
        )
        task = response.json()

        assert task["title"] == "Nested Task"
        assert task["project_id"] == project_id

    def test_nested_route_with_nonexistent_project(self, client):
        """Test that nested routes return 404 for nonexistent project."""
        response = client.get("/projects/99999/tasks", assert_status_code=404)


class TestMeEndpoints:
    """Test /users/me endpoints for self-service."""

    def test_get_me(self, client):
        """Test GET /users/me returns current user (user ID 1)."""
        # Create org and user
        response = client.post(
            "/organizations/",
            json={"name": "Me Test Org", "slug": "me-test-org"},
        )
        org_id = response.json()["id"]

        # Create the user that will be ID 1 (simulated "current user")
        response = client.post(
            "/users/",
            json={
                "email": "current@example.com",
                "name": "Current User",
                "organization_id": org_id,
            },
        )
        created_user_id = response.json()["id"]

        # Get /me - returns user ID 1 (which may or may not be the one we just created)
        response = client.get("/users/me")
        me = response.json()

        # Just verify we get a valid user response
        assert "id" in me
        assert "email" in me
        assert "name" in me

    def test_update_me(self, client):
        """Test PATCH /users/me updates current user's profile."""
        # First, get current user (created by previous test or this one)
        response = client.get("/users/me", assert_status_code=200)
        original = response.json()

        # Update via /me
        response = client.patch(
            "/users/me",
            json={"name": "Updated Name"},
        )
        updated = response.json()

        assert updated["name"] == "Updated Name"
        assert updated["email"] == original["email"]  # Email unchanged


class TestReportingEndpoints:
    """Test reporting/stats endpoints."""

    def test_project_stats(self, client):
        """Test GET /projects/{id}/stats returns correct counts."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Stats Test Org", "slug": "stats-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Stats Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Add tasks with different statuses
        client.post("/tasks/", json={"title": "Todo 1", "status": "todo", "project_id": project_id})
        client.post("/tasks/", json={"title": "Todo 2", "status": "todo", "project_id": project_id})
        client.post("/tasks/", json={"title": "In Progress", "status": "in_progress", "project_id": project_id})
        client.post("/tasks/", json={"title": "Done 1", "status": "done", "project_id": project_id})
        client.post("/tasks/", json={"title": "Done 2", "status": "done", "project_id": project_id})
        client.post("/tasks/", json={"title": "Done 3", "status": "done", "project_id": project_id})

        # Get stats
        response = client.get(f"/projects/{project_id}/stats")
        stats = response.json()

        assert stats["total_tasks"] == 6
        assert stats["todo_count"] == 2
        assert stats["in_progress_count"] == 1
        assert stats["done_count"] == 3
        assert stats["completion_percent"] == 50.0  # 3/6 = 50%

    def test_project_stats_empty(self, client):
        """Test stats for project with no tasks."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Empty Stats Org", "slug": "empty-stats-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Empty Stats Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Get stats
        response = client.get(f"/projects/{project_id}/stats")
        stats = response.json()

        assert stats["total_tasks"] == 0
        assert stats["completion_percent"] == 0.0


class TestTaskWorkflow:
    """Test task workflow state machine transitions."""

    def test_start_task(self, client):
        """Test POST /tasks/{id}/start moves task from TODO to IN_PROGRESS."""
        # Create org, project, and task
        response = client.post(
            "/organizations/",
            json={"name": "Workflow Test Org", "slug": "workflow-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Workflow Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        response = client.post(
            "/tasks/",
            json={"title": "Workflow Task", "project_id": project_id},
        )
        task = response.json()
        task_id = task["id"]
        assert task["status"] == "todo"
        original_version = task["version"]

        # Start task
        response = client.post(f"/tasks/{task_id}/start")
        started = response.json()

        assert started["status"] == "in_progress"
        assert started["version"] == original_version + 1

    def test_complete_task(self, client):
        """Test POST /tasks/{id}/complete moves task from IN_PROGRESS to DONE."""
        # Create org, project, and task
        response = client.post(
            "/organizations/",
            json={"name": "Complete Test Org", "slug": "complete-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Complete Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create task and start it
        response = client.post(
            "/tasks/",
            json={"title": "Task to Complete", "project_id": project_id},
        )
        task_id = response.json()["id"]

        # Start task first (todo -> in_progress)
        client.post(f"/tasks/{task_id}/start")

        # Complete task (in_progress -> done)
        response = client.post(f"/tasks/{task_id}/complete")
        completed = response.json()

        assert completed["status"] == "done"
        assert completed["version"] == 3  # created=1, started=2, completed=3

    def test_reopen_task(self, client):
        """Test POST /tasks/{id}/reopen moves task from DONE to IN_PROGRESS."""
        # Create org, project, and task
        response = client.post(
            "/organizations/",
            json={"name": "Reopen Test Org", "slug": "reopen-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Reopen Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create task, start it, and complete it
        response = client.post(
            "/tasks/",
            json={"title": "Task to Reopen", "project_id": project_id},
        )
        task_id = response.json()["id"]

        client.post(f"/tasks/{task_id}/start")
        client.post(f"/tasks/{task_id}/complete")

        # Reopen task (done -> in_progress)
        response = client.post(f"/tasks/{task_id}/reopen")
        reopened = response.json()

        assert reopened["status"] == "in_progress"
        assert reopened["version"] == 4  # created=1, started=2, completed=3, reopened=4

    def test_start_task_invalid_status_fails(self, client):
        """Test that starting a task not in TODO status fails."""
        # Create org, project, and task
        response = client.post(
            "/organizations/",
            json={"name": "Invalid Start Org", "slug": "invalid-start-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Invalid Start Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        response = client.post(
            "/tasks/",
            json={"title": "Already Started", "project_id": project_id},
        )
        task_id = response.json()["id"]

        # Start task once
        client.post(f"/tasks/{task_id}/start")

        # Try to start again - should fail
        response = client.post(f"/tasks/{task_id}/start", assert_status_code=400)
        error = response.json()
        assert "in_progress" in error["detail"]

    def test_complete_task_invalid_status_fails(self, client):
        """Test that completing a task not in IN_PROGRESS status fails."""
        # Create org, project, and task
        response = client.post(
            "/organizations/",
            json={"name": "Invalid Complete Org", "slug": "invalid-complete-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Invalid Complete Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        response = client.post(
            "/tasks/",
            json={"title": "Not Started", "project_id": project_id},
        )
        task_id = response.json()["id"]

        # Try to complete without starting - should fail
        response = client.post(f"/tasks/{task_id}/complete", assert_status_code=400)
        error = response.json()
        assert "todo" in error["detail"]

    def test_reopen_task_invalid_status_fails(self, client):
        """Test that reopening a task not in DONE status fails."""
        # Create org, project, and task
        response = client.post(
            "/organizations/",
            json={"name": "Invalid Reopen Org", "slug": "invalid-reopen-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Invalid Reopen Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        response = client.post(
            "/tasks/",
            json={"title": "Not Done", "project_id": project_id},
        )
        task_id = response.json()["id"]

        # Try to reopen a task that's still todo - should fail
        response = client.post(f"/tasks/{task_id}/reopen", assert_status_code=400)
        error = response.json()
        assert "todo" in error["detail"]


class TestProjectLifecycle:
    """Test project lifecycle (archive) functionality."""

    def test_archive_project(self, client):
        """Test POST /projects/{id}/archive archives the project."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Archive Lifecycle Org", "slug": "archive-lifecycle-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Project to Archive", "organization_id": org_id},
        )
        project = response.json()
        project_id = project["id"]
        assert project["status"] == "active"

        # Archive the project
        response = client.post(f"/projects/{project_id}/archive")
        archived = response.json()

        assert archived["status"] == "archived"

    def test_archive_already_archived_fails(self, client):
        """Test that archiving an already archived project fails."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Double Archive Org", "slug": "double-archive-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Already Archived", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Archive once
        client.post(f"/projects/{project_id}/archive")

        # Try to archive again
        response = client.post(f"/projects/{project_id}/archive", assert_status_code=400)
        error = response.json()
        assert "already archived" in error["detail"]

    def test_create_task_in_archived_project_fails(self, client):
        """Test that creating a task in an archived project fails."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Archived Task Org", "slug": "archived-task-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Archived for Tasks", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Archive the project
        client.post(f"/projects/{project_id}/archive")

        # Try to create a task - should fail
        response = client.post(
            "/tasks/",
            json={"title": "Should Fail", "project_id": project_id},
            assert_status_code=400,
        )
        error = response.json()
        assert "archived" in error["detail"]

    def test_create_task_via_nested_route_in_archived_fails(self, client):
        """Test that nested route task creation fails for archived projects."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Nested Archived Org", "slug": "nested-archived-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Nested Archived Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Archive the project
        client.post(f"/projects/{project_id}/archive")

        # Try to create via nested route - should fail
        response = client.post(
            f"/projects/{project_id}/tasks",
            json={"title": "Should Fail"},
            assert_status_code=400,
        )
        error = response.json()
        assert "archived" in error["detail"]


class TestConditionalValidation:
    """Test conditional required field validation."""

    def test_bug_without_severity_fails(self, client):
        """Test that creating a bug without severity fails validation."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Conditional Val Org", "slug": "conditional-val-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Conditional Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Try to create bug without severity - should fail
        response = client.post(
            "/tasks/",
            json={
                "title": "Bug without severity",
                "task_type": "bug",
                "project_id": project_id,
            },
            assert_status_code=422,
        )
        error = response.json()
        # Check validation error message (can be string or list)
        detail = error.get("detail", "")
        if isinstance(detail, list):
            assert any("severity" in str(e).lower() for e in detail)
        else:
            assert "severity" in detail.lower()

    def test_bug_with_severity_succeeds(self, client):
        """Test that creating a bug with severity succeeds."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Bug Severity Org", "slug": "bug-severity-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Bug Severity Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create bug with severity - should succeed
        response = client.post(
            "/tasks/",
            json={
                "title": "Bug with severity",
                "task_type": "bug",
                "severity": 3,
                "project_id": project_id,
            },
        )
        bug = response.json()

        assert bug["task_type"] == "bug"
        assert bug["severity"] == 3

    def test_feature_without_severity_succeeds(self, client):
        """Test that features don't require severity."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Feature No Sev Org", "slug": "feature-no-sev-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Feature No Sev Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create feature without severity - should succeed (severity is bug-only)
        response = client.post(
            "/tasks/",
            json={
                "title": "Feature task",
                "task_type": "feature",
                "project_id": project_id,
            },
        )
        feature = response.json()

        assert feature["task_type"] == "feature"
        assert feature["severity"] is None

    def test_regular_task_without_severity_succeeds(self, client):
        """Test that regular tasks don't require severity."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Task No Sev Org", "slug": "task-no-sev-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Task No Sev Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create regular task without severity - should succeed
        response = client.post(
            "/tasks/",
            json={
                "title": "Regular task",
                "task_type": "task",
                "project_id": project_id,
            },
        )
        task = response.json()

        assert task["task_type"] == "task"
        assert task["severity"] is None


class TestCrossResourceValidation:
    """Test cross-resource validation (assignee must be in same org as project)."""

    def test_create_task_with_assignee_from_different_org_fails(self, client):
        """Test that creating a task with assignee from different org fails."""
        # Create two organizations
        response = client.post(
            "/organizations/",
            json={"name": "Cross Res Org 1", "slug": "cross-res-org-1"},
        )
        org1_id = response.json()["id"]

        response = client.post(
            "/organizations/",
            json={"name": "Cross Res Org 2", "slug": "cross-res-org-2"},
        )
        org2_id = response.json()["id"]

        # Create user in org 2
        response = client.post(
            "/users/",
            json={
                "email": "user@org2.com",
                "name": "Org 2 User",
                "organization_id": org2_id,
            },
        )
        user_from_org2 = response.json()["id"]

        # Create project in org 1
        response = client.post(
            "/projects/",
            json={"name": "Org 1 Project", "organization_id": org1_id},
        )
        project_in_org1 = response.json()["id"]

        # Try to create task in org1 project with assignee from org2 - should fail
        response = client.post(
            "/tasks/",
            json={
                "title": "Cross-org assignment",
                "project_id": project_in_org1,
                "assignee_id": user_from_org2,
            },
            assert_status_code=422,
        )
        error = response.json()
        assert "same organization" in error["detail"]

    def test_create_task_with_assignee_from_same_org_succeeds(self, client):
        """Test that creating a task with assignee from same org succeeds."""
        # Create organization
        response = client.post(
            "/organizations/",
            json={"name": "Same Org Test", "slug": "same-org-test"},
        )
        org_id = response.json()["id"]

        # Create user in org
        response = client.post(
            "/users/",
            json={
                "email": "user@sameorg.com",
                "name": "Same Org User",
                "organization_id": org_id,
            },
        )
        user_id = response.json()["id"]

        # Create project in org
        response = client.post(
            "/projects/",
            json={"name": "Same Org Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create task with same-org assignee - should succeed
        response = client.post(
            "/tasks/",
            json={
                "title": "Same-org assignment",
                "project_id": project_id,
                "assignee_id": user_id,
            },
        )
        task = response.json()

        assert task["assignee_id"] == user_id

    def test_update_task_assignee_to_different_org_fails(self, client):
        """Test that updating assignee to user from different org fails."""
        # Create two organizations
        response = client.post(
            "/organizations/",
            json={"name": "Update Cross Org 1", "slug": "update-cross-org-1"},
        )
        org1_id = response.json()["id"]

        response = client.post(
            "/organizations/",
            json={"name": "Update Cross Org 2", "slug": "update-cross-org-2"},
        )
        org2_id = response.json()["id"]

        # Create user in org 2
        response = client.post(
            "/users/",
            json={
                "email": "other@org2.com",
                "name": "Other Org User",
                "organization_id": org2_id,
            },
        )
        user_from_org2 = response.json()["id"]

        # Create project in org 1
        response = client.post(
            "/projects/",
            json={"name": "Update Org 1 Project", "organization_id": org1_id},
        )
        project_in_org1 = response.json()["id"]

        # Create task without assignee
        response = client.post(
            "/tasks/",
            json={"title": "Unassigned task", "project_id": project_in_org1},
        )
        task_id = response.json()["id"]

        # Try to update assignee to user from different org - should fail
        response = client.patch(
            f"/tasks/{task_id}",
            json={"assignee_id": user_from_org2},
            assert_status_code=422,
        )
        error = response.json()
        assert "same organization" in error["detail"]


class TestDifferentSchemasPerOperation:
    """Test different schemas per operation (creation_schema, update_schema)."""

    def test_create_org_with_invalid_slug_fails(self, client):
        """Test that creation_schema validates slug format."""
        # Try to create with uppercase slug - should fail
        response = client.post(
            "/organizations/",
            json={"name": "Test Org", "slug": "UPPERCASE"},
            assert_status_code=422,
        )
        error = response.json()
        # Check validation error mentions slug format
        assert any("slug" in str(e).lower() for e in error.get("detail", []))

    def test_create_org_with_spaces_in_slug_fails(self, client):
        """Test that creation_schema rejects slugs with spaces."""
        response = client.post(
            "/organizations/",
            json={"name": "Test Org", "slug": "has spaces"},
            assert_status_code=422,
        )
        error = response.json()
        assert any("slug" in str(e).lower() for e in error.get("detail", []))

    def test_create_org_with_valid_slug_succeeds(self, client):
        """Test that creation_schema accepts valid slugs."""
        response = client.post(
            "/organizations/",
            json={"name": "Valid Org", "slug": "valid-slug-123"},
        )
        org = response.json()

        assert org["slug"] == "valid-slug-123"
        assert org["name"] == "Valid Org"

    def test_create_org_with_short_name_fails(self, client):
        """Test that creation_schema requires minimum name length."""
        response = client.post(
            "/organizations/",
            json={"name": "X", "slug": "short-name"},
            assert_status_code=422,
        )
        error = response.json()
        assert any("name" in str(e).lower() for e in error.get("detail", []))

    def test_update_org_name_only(self, client):
        """Test that update_schema only allows name updates."""
        # Create org
        response = client.post(
            "/organizations/",
            json={"name": "Original Name", "slug": "update-schema-test"},
        )
        org_id = response.json()["id"]

        # Update name only - should succeed
        response = client.patch(
            f"/organizations/{org_id}",
            json={"name": "Updated Name"},
        )
        updated = response.json()

        assert updated["name"] == "Updated Name"
        assert updated["slug"] == "update-schema-test"  # Slug unchanged

    def test_update_org_slug_ignored(self, client):
        """Test that update_schema ignores slug changes (not in schema)."""
        # Create org
        response = client.post(
            "/organizations/",
            json={"name": "Slug Update Test", "slug": "original-slug"},
        )
        org_id = response.json()["id"]

        # Try to update slug - should be ignored (not in update_schema)
        response = client.patch(
            f"/organizations/{org_id}",
            json={"slug": "new-slug"},
        )
        updated = response.json()

        # Slug should be unchanged (field not in update_schema)
        assert updated["slug"] == "original-slug"


class TestTenantIsolation:
    """Test tenant isolation (org scoping) for projects."""

    def test_tenant_isolation_filters_list(self, client):
        """Test that process_index filters by current org when set."""
        import app.views.project as project_view

        # Create two orgs
        response = client.post(
            "/organizations/",
            json={"name": "Tenant Org 1", "slug": "tenant-org-1"},
        )
        org1_id = response.json()["id"]

        response = client.post(
            "/organizations/",
            json={"name": "Tenant Org 2", "slug": "tenant-org-2"},
        )
        org2_id = response.json()["id"]

        # Create project in each org
        response = client.post(
            "/projects/",
            json={"name": "Org 1 Project", "organization_id": org1_id},
        )
        org1_project_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Org 2 Project", "organization_id": org2_id},
        )
        org2_project_id = response.json()["id"]

        # Without tenant isolation, both projects visible
        project_view.CURRENT_ORG_ID = None
        response = client.get("/projects/")
        all_projects = response.json()
        all_ids = [p["id"] for p in all_projects]
        assert org1_project_id in all_ids
        assert org2_project_id in all_ids

        # With tenant isolation for org1, only org1 projects visible
        project_view.CURRENT_ORG_ID = org1_id
        try:
            response = client.get("/projects/")
            filtered_projects = response.json()
            filtered_ids = [p["id"] for p in filtered_projects]
            assert org1_project_id in filtered_ids
            assert org2_project_id not in filtered_ids
        finally:
            project_view.CURRENT_ORG_ID = None  # Reset

    def test_tenant_isolation_blocks_get_other_org(self, client):
        """Test that process_get returns 404 for other org's resources."""
        import app.views.project as project_view

        # Create two orgs
        response = client.post(
            "/organizations/",
            json={"name": "Get Tenant Org 1", "slug": "get-tenant-org-1"},
        )
        org1_id = response.json()["id"]

        response = client.post(
            "/organizations/",
            json={"name": "Get Tenant Org 2", "slug": "get-tenant-org-2"},
        )
        org2_id = response.json()["id"]

        # Create project in org2
        response = client.post(
            "/projects/",
            json={"name": "Org 2 Secret Project", "organization_id": org2_id},
        )
        org2_project_id = response.json()["id"]

        # With tenant isolation for org1, can't access org2's project
        project_view.CURRENT_ORG_ID = org1_id
        try:
            response = client.get(
                f"/projects/{org2_project_id}",
                assert_status_code=404,
            )
        finally:
            project_view.CURRENT_ORG_ID = None  # Reset

    def test_tenant_isolation_allows_own_org(self, client):
        """Test that process_get allows access to own org's resources."""
        import app.views.project as project_view

        # Create org
        response = client.post(
            "/organizations/",
            json={"name": "Own Tenant Org", "slug": "own-tenant-org"},
        )
        org_id = response.json()["id"]

        # Create project in org
        response = client.post(
            "/projects/",
            json={"name": "Own Org Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # With tenant isolation for same org, can access project
        project_view.CURRENT_ORG_ID = org_id
        try:
            response = client.get(f"/projects/{project_id}")
            project = response.json()
            assert project["id"] == project_id
        finally:
            project_view.CURRENT_ORG_ID = None  # Reset


class TestSparseFieldsets:
    """Test sparse fieldsets (?fields=id,name) for limiting response fields."""

    def test_sparse_fields_basic(self, client):
        """Test that ?fields limits response to specified fields."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Sparse Test Org", "slug": "sparse-test-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={
                "name": "Sparse Project",
                "description": "A description",
                "organization_id": org_id,
            },
        )
        project_id = response.json()["id"]

        # Request only id and name fields
        response = client.get("/projects/sparse?fields=id,name")
        projects = response.json()

        # Find our project
        our_project = next((p for p in projects if p.get("id") == project_id), None)
        assert our_project is not None

        # Should only have id and name
        assert set(our_project.keys()) == {"id", "name"}
        assert our_project["name"] == "Sparse Project"

    def test_sparse_fields_multiple(self, client):
        """Test requesting multiple specific fields."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "Multi Sparse Org", "slug": "multi-sparse-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={
                "name": "Multi Sparse Project",
                "description": "Description here",
                "organization_id": org_id,
            },
        )
        project_id = response.json()["id"]

        # Request id, name, description, status
        response = client.get("/projects/sparse?fields=id,name,description,status")
        projects = response.json()

        our_project = next((p for p in projects if p.get("id") == project_id), None)
        assert our_project is not None

        # Should have exactly these fields
        assert set(our_project.keys()) == {"id", "name", "description", "status"}
        assert our_project["description"] == "Description here"
        assert our_project["status"] == "active"

    def test_sparse_fields_without_param_returns_all(self, client):
        """Test that without ?fields param, all fields are returned."""
        # Create org and project
        response = client.post(
            "/organizations/",
            json={"name": "All Fields Org", "slug": "all-fields-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "All Fields Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Request without fields param
        response = client.get("/projects/sparse")
        projects = response.json()

        our_project = next((p for p in projects if p.get("id") == project_id), None)
        assert our_project is not None

        # Should have all standard fields
        assert "id" in our_project
        assert "name" in our_project
        assert "description" in our_project
        assert "status" in our_project
        assert "organization_id" in our_project
        assert "created_at" in our_project


class TestRowLevelPermissions:
    """Test row-level permissions (filter results by user permissions)."""

    def test_row_level_filters_task_list(self, client):
        """Test that process_index filters tasks by current user."""
        import app.views.task as task_view

        # Create org, users, and project
        response = client.post(
            "/organizations/",
            json={"name": "Row Level Org", "slug": "row-level-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={"email": "user1@row.com", "name": "User 1", "organization_id": org_id},
        )
        user1_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={"email": "user2@row.com", "name": "User 2", "organization_id": org_id},
        )
        user2_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Row Level Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create tasks assigned to different users
        response = client.post(
            "/tasks/",
            json={"title": "User 1 Task", "project_id": project_id, "assignee_id": user1_id},
        )
        user1_task_id = response.json()["id"]

        response = client.post(
            "/tasks/",
            json={"title": "User 2 Task", "project_id": project_id, "assignee_id": user2_id},
        )
        user2_task_id = response.json()["id"]

        # Without row-level permissions, all tasks visible
        task_view.CURRENT_USER_ID = None
        response = client.get("/tasks/")
        all_tasks = response.json()
        all_ids = [t["id"] for t in all_tasks]
        assert user1_task_id in all_ids
        assert user2_task_id in all_ids

        # With row-level permissions for user1, only user1's tasks visible
        task_view.CURRENT_USER_ID = user1_id
        try:
            response = client.get("/tasks/")
            filtered_tasks = response.json()
            filtered_ids = [t["id"] for t in filtered_tasks]
            assert user1_task_id in filtered_ids
            assert user2_task_id not in filtered_ids
        finally:
            task_view.CURRENT_USER_ID = None  # Reset

    def test_row_level_blocks_get_other_user_task(self, client):
        """Test that process_get returns 404 for other user's tasks."""
        import app.views.task as task_view

        # Create org, users, and project
        response = client.post(
            "/organizations/",
            json={"name": "Get Row Level Org", "slug": "get-row-level-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={"email": "getuser1@row.com", "name": "Get User 1", "organization_id": org_id},
        )
        user1_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={"email": "getuser2@row.com", "name": "Get User 2", "organization_id": org_id},
        )
        user2_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Get Row Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create task assigned to user2
        response = client.post(
            "/tasks/",
            json={"title": "User 2 Only Task", "project_id": project_id, "assignee_id": user2_id},
        )
        user2_task_id = response.json()["id"]

        # User1 cannot access user2's task
        task_view.CURRENT_USER_ID = user1_id
        try:
            response = client.get(
                f"/tasks/{user2_task_id}",
                assert_status_code=404,
            )
        finally:
            task_view.CURRENT_USER_ID = None  # Reset

    def test_row_level_allows_own_task(self, client):
        """Test that process_get allows access to user's own tasks."""
        import app.views.task as task_view

        # Create org, user, and project
        response = client.post(
            "/organizations/",
            json={"name": "Own Task Org", "slug": "own-task-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={"email": "ownuser@row.com", "name": "Own User", "organization_id": org_id},
        )
        user_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Own Task Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        # Create task assigned to user
        response = client.post(
            "/tasks/",
            json={"title": "My Own Task", "project_id": project_id, "assignee_id": user_id},
        )
        task_id = response.json()["id"]

        # User can access own task
        task_view.CURRENT_USER_ID = user_id
        try:
            response = client.get(f"/tasks/{task_id}")
            task = response.json()
            assert task["id"] == task_id
        finally:
            task_view.CURRENT_USER_ID = None  # Reset


class TestFieldLevelPermissions:
    """Test field-level permissions (different response fields based on role)."""

    def test_hr_can_see_salary(self, client):
        """Test that HR role can see salary field."""
        import app.views.user as user_view
        from app.models import UserRole

        # Create org and user with salary
        response = client.post(
            "/organizations/",
            json={"name": "Field Level Org", "slug": "field-level-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={
                "email": "employee@field.com",
                "name": "Employee",
                "organization_id": org_id,
                "salary": 75000,
            },
        )
        user_id = response.json()["id"]

        # Set current role to HR
        user_view.CURRENT_USER_ROLE = UserRole.HR
        try:
            response = client.get(f"/users/{user_id}/with-permissions")
            user = response.json()

            # HR can see salary
            assert "salary" in user
            assert user["salary"] == 75000
        finally:
            user_view.CURRENT_USER_ROLE = None  # Reset

    def test_member_cannot_see_salary(self, client):
        """Test that member role cannot see salary field."""
        import app.views.user as user_view
        from app.models import UserRole

        # Create org and user with salary
        response = client.post(
            "/organizations/",
            json={"name": "Member Field Org", "slug": "member-field-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={
                "email": "salary@field.com",
                "name": "Salary User",
                "organization_id": org_id,
                "salary": 90000,
            },
        )
        user_id = response.json()["id"]

        # Set current role to MEMBER
        user_view.CURRENT_USER_ROLE = UserRole.MEMBER
        try:
            response = client.get(f"/users/{user_id}/with-permissions")
            user = response.json()

            # Member cannot see salary (not in public schema)
            assert "salary" not in user
        finally:
            user_view.CURRENT_USER_ROLE = None  # Reset

    def test_owner_can_see_salary(self, client):
        """Test that owner role can also see salary field."""
        import app.views.user as user_view
        from app.models import UserRole

        # Create org and user with salary
        response = client.post(
            "/organizations/",
            json={"name": "Owner Field Org", "slug": "owner-field-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={
                "email": "owner-view@field.com",
                "name": "Owner View User",
                "organization_id": org_id,
                "salary": 100000,
            },
        )
        user_id = response.json()["id"]

        # Set current role to OWNER
        user_view.CURRENT_USER_ROLE = UserRole.OWNER
        try:
            response = client.get(f"/users/{user_id}/with-permissions")
            user = response.json()

            # Owner can see salary
            assert "salary" in user
            assert user["salary"] == 100000
        finally:
            user_view.CURRENT_USER_ROLE = None  # Reset

    def test_no_role_cannot_see_salary(self, client):
        """Test that without a role set, salary is hidden."""
        import app.views.user as user_view

        # Create org and user with salary
        response = client.post(
            "/organizations/",
            json={"name": "No Role Org", "slug": "no-role-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={
                "email": "norole@field.com",
                "name": "No Role User",
                "organization_id": org_id,
                "salary": 80000,
            },
        )
        user_id = response.json()["id"]

        # Ensure no role is set
        user_view.CURRENT_USER_ROLE = None
        try:
            response = client.get(f"/users/{user_id}/with-permissions")
            user = response.json()

            # No role means no salary access
            assert "salary" not in user
        finally:
            user_view.CURRENT_USER_ROLE = None  # Reset
