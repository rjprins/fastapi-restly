"""CRUD tests for Label and the TaskLabel association."""


class TestLabelCRUD:
    """Test Label and TaskLabel CRUD operations."""

    def test_create_label(self, client):
        """Test creating a label."""
        # Create org
        response = client.post(
            "/organizations/", json={"name": "Label Test Org", "slug": "label-test-org"}
        )
        org_id = response.json()["id"]

        # Create label
        response = client.post(
            "/labels/",
            json={"name": "urgent", "color": "#ff0000", "organization_id": org_id},
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
            "/projects/", json={"name": "Label Project", "organization_id": org_id}
        )
        project_id = response.json()["id"]

        response = client.post(
            "/tasks/", json={"title": "Labeled Task", "project_id": project_id}
        )
        task_id = response.json()["id"]

        response = client.post(
            "/labels/",
            json={"name": "bug", "color": "#ff0000", "organization_id": org_id},
        )
        label_id = response.json()["id"]

        # Add label to task using IDRef[T] scalar wire format. The framework
        # still validates the referenced rows exist and resolves them to FK
        # values automatically.
        response = client.post(
            "/task-labels/",
            json={"task_id": task_id, "label_id": label_id, "added_by_id": user_id},
        )
        task_label = response.json()

        assert task_label["task_id"] == task_id
        assert task_label["label_id"] == label_id
        assert task_label["added_by_id"] == user_id
