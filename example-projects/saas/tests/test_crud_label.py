"""CRUD tests for Label and the TaskLabel association."""


class TestLabelCRUD:
    """Test Label and TaskLabel CRUD operations."""

    def test_create_label(self, client, actor):
        """Test creating a label; it lands in the acting organization."""
        response = client.post("/labels", json={"name": "urgent", "color": "#ff0000"})
        label = response.json()

        assert label["name"] == "urgent"
        assert label["color"] == "#ff0000"
        assert label["organization_id"] == actor.org_id

    def test_add_label_to_task(self, client, actor):
        """Test adding a label to a task via TaskLabel."""
        # Create project, task, and label
        response = client.post("/projects", json={"name": "Label Project"})
        project_id = response.json()["id"]

        response = client.post(
            "/tasks", json={"title": "Labeled Task", "project_id": project_id}
        )
        task_id = response.json()["id"]

        response = client.post("/labels", json={"name": "bug", "color": "#ff0000"})
        label_id = response.json()["id"]

        # Add label to task using IDRef[T] scalar wire format. The framework
        # still validates the referenced rows exist and resolves them to FK
        # values automatically. ``added_by_id`` is the column's stamp.
        response = client.post(
            "/task-labels", json={"task_id": task_id, "label_id": label_id}
        )
        task_label = response.json()

        assert task_label["task_id"] == task_id
        assert task_label["label_id"] == label_id
        assert task_label["added_by_id"] == actor.user_id
