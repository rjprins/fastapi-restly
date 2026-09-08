"""Tests for filtering, sorting, and pagination."""

import uuid


def setup_test_data(client):
    """Create test data for query tests, in the acting organization."""
    # Use unique suffix to avoid conflicts between tests
    unique = str(uuid.uuid4())[:8]

    # Create users
    users = []
    for name, role in [("Anna", "admin"), ("Bob", "member"), ("Charlie", "member")]:
        response = client.post(
            "/users",
            json={
                "email": f"{name.lower()}-{unique}@example.com",
                "name": name,
                "role": role,
            },
        )
        users.append(response.json())

    # Create project
    response = client.post("/projects", json={"name": "Query Project"})
    project_id = response.json()["id"]

    # Create tasks with different statuses and priorities
    tasks = []
    for title, status, priority, assignee_idx in [
        ("Task A", "todo", 1, 0),
        ("Task B", "in_progress", 2, 1),
        ("Task C", "done", 3, None),
        ("Task D", "todo", 4, 2),
        ("Task E", "in_progress", 1, 0),
    ]:
        response = client.post(
            "/tasks",
            json={
                "title": title,
                "status": status,
                "priority": priority,
                "project_id": project_id,
                "assignee_id": users[assignee_idx]["id"]
                if assignee_idx is not None
                else None,
            },
        )
        tasks.append(response.json())

    return {"users": users, "project_id": project_id, "tasks": tasks}


class TestFiltering:
    """Test filtering query parameters."""

    def test_filter_by_status(self, client):
        """Test filtering tasks by status."""
        setup_test_data(client)

        # Filter for todo tasks
        response = client.get("/tasks?status=todo")
        tasks = response.json()["data"]

        assert all(t["status"] == "todo" for t in tasks)
        assert len(tasks) >= 2  # At least 2 from our test data

    def test_filter_by_priority(self, client):
        """Test filtering tasks by priority."""
        setup_test_data(client)

        # Filter for critical priority
        response = client.get("/tasks?priority=1")
        tasks = response.json()["data"]

        assert all(t["priority"] == 1 for t in tasks)
        assert len(tasks) >= 2  # At least 2 from our test data

    def test_filter_by_role(self, client):
        """Test filtering users by role."""
        setup_test_data(client)

        # Filter for admins
        response = client.get("/users?role=admin")
        users = response.json()["data"]

        assert all(u["role"] == "admin" for u in users)
        assert len(users) >= 1


class TestSorting:
    """Test sorting query parameters."""

    def test_sort_by_priority_ascending(self, client):
        """Test sorting tasks by priority ascending."""
        setup_test_data(client)

        response = client.get("/tasks?sort=priority")
        tasks = response.json()["data"]

        priorities = [t["priority"] for t in tasks]
        assert priorities == sorted(priorities)

    def test_sort_by_priority_descending(self, client):
        """Test sorting tasks by priority descending."""
        setup_test_data(client)

        response = client.get("/tasks?sort=-priority")
        tasks = response.json()["data"]

        priorities = [t["priority"] for t in tasks]
        assert priorities == sorted(priorities, reverse=True)

    def test_sort_by_name(self, client):
        """Test sorting users by name."""
        setup_test_data(client)

        response = client.get("/users?sort=name")
        users = response.json()["data"]

        names = [u["name"] for u in users]
        assert names == sorted(names)


class TestPagination:
    """Test pagination query parameters."""

    def test_page_size_caps_results(self, client):
        """``page_size`` limits the number of results."""
        setup_test_data(client)

        response = client.get("/tasks?page_size=2")
        tasks = response.json()["data"]

        assert len(tasks) == 2

    def test_page_size_and_page(self, client):
        """``page_size`` + ``page`` gives offset-based pagination."""
        setup_test_data(client)

        response = client.get("/tasks?sort=title&page_size=2")
        first_page = response.json()["data"]
        response = client.get("/tasks?sort=title&page_size=2&page=2")
        second_page = response.json()["data"]

        assert len(first_page) == 2
        assert len(second_page) >= 1
        assert first_page[0]["title"] != second_page[0]["title"]


class TestCombinedQueries:
    """Test combining filter, sort, and pagination."""

    def test_filter_and_sort(self, client):
        """Test filtering and sorting together."""
        setup_test_data(client)

        response = client.get("/tasks?status=todo&sort=-priority")
        tasks = response.json()["data"]

        # All should be todo
        assert all(t["status"] == "todo" for t in tasks)
        # Should be sorted by priority descending
        priorities = [t["priority"] for t in tasks]
        assert priorities == sorted(priorities, reverse=True)

    def test_filter_sort_and_paginate(self, client):
        """Test filter, sort, and pagination together."""
        setup_test_data(client)

        response = client.get("/tasks?status=in_progress&sort=title&page_size=1")
        tasks = response.json()["data"]

        assert len(tasks) == 1
        assert tasks[0]["status"] == "in_progress"


class TestLabelFiltering:
    """Filter and sort behaviour against ``LabelView``."""

    def _setup_labels(self, client):
        client.post("/labels", json={"name": "urgent", "color": "#ff0000"})
        client.post("/labels", json={"name": "feature", "color": "#00ff00"})
        client.post("/labels", json={"name": "bug", "color": "#0000ff"})

    def test_filter_by_name(self, client):
        """Filter: ?name=urgent returns only labels named 'urgent'."""
        self._setup_labels(client)

        response = client.get("/labels?name=urgent")
        labels = response.json()["data"]

        assert all(lb["name"] == "urgent" for lb in labels)
        assert len(labels) >= 1

    def test_sort_by_name(self, client):
        """Sort: ?sort=name returns labels sorted alphabetically."""
        self._setup_labels(client)

        response = client.get("/labels?sort=name")
        labels = response.json()["data"]

        names = [lb["name"] for lb in labels]
        assert names == sorted(names)

    def test_pagination(self, client):
        """Pagination: ?page=1&page_size=2 returns at most 2 items."""
        self._setup_labels(client)

        response = client.get("/labels?page_size=2&page=1")
        labels = response.json()["data"]

        assert len(labels) <= 2

    def test_filter_composes_with_tenant_scope(self, client, new_tenant):
        """LabelView's filters compose with the tenant floor."""
        beta = new_tenant("beta")
        label1 = client.post("/labels", json={"name": "shared", "color": "#ff0000"})
        with beta.acting():
            label2 = client.post("/labels", json={"name": "shared", "color": "#00ff00"})

        labels = client.get("/labels?name=shared").json()["data"]
        assert [label["id"] for label in labels] == [label1.json()["id"]]

        with beta.acting():
            labels = client.get("/labels?name=shared").json()["data"]
        assert [label["id"] for label in labels] == [label2.json()["id"]]


class TestPaginationEnvelope:
    """List endpoints paginate by default and wrap rows in the ``data`` envelope."""

    def test_project_list_returns_pagination_envelope(self, client):
        """Project list response wraps rows in the default pagination envelope."""
        for i in range(3):
            client.post("/projects", json={"name": f"Project {i}"})

        response = client.get("/projects")
        data = response.json()

        # Views paginate by default, so the response is the pagination envelope.
        assert "data" in data
        assert "total_count" in data
        assert "page" in data
        assert "page_size" in data
        assert "total_pages" in data
        assert data["total_count"] >= 3
        assert isinstance(data["data"], list)
