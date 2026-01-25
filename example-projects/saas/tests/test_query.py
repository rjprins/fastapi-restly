"""Tests for filtering, sorting, and pagination."""

import uuid

import pytest
from fastapi_restly.testing import RestlyTestClient

from app.main import app


@pytest.fixture
def client() -> RestlyTestClient:
    """Create a test client."""
    return RestlyTestClient(app)


def setup_test_data(client):
    """Create test data for query tests."""
    # Use unique suffix to avoid conflicts between tests
    unique = str(uuid.uuid4())[:8]

    # Create org
    response = client.post(
        "/organizations/",
        json={"name": f"Query Test Org {unique}", "slug": f"query-test-org-{unique}"},
    )
    org_id = response.json()["id"]

    # Create users
    users = []
    for i, (name, role) in enumerate([
        ("Alice", "admin"),
        ("Bob", "member"),
        ("Charlie", "member"),
    ]):
        response = client.post(
            "/users/",
            json={
                "email": f"{name.lower()}-{unique}@example.com",
                "name": name,
                "role": role,
                "organization_id": org_id,
            },
        )
        users.append(response.json())

    # Create project
    response = client.post(
        "/projects/",
        json={"name": "Query Project", "organization_id": org_id},
    )
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
            "/tasks/",
            json={
                "title": title,
                "status": status,
                "priority": priority,
                "project_id": project_id,
                "assignee_id": users[assignee_idx]["id"] if assignee_idx is not None else None,
            },
        )
        tasks.append(response.json())

    return {"org_id": org_id, "users": users, "project_id": project_id, "tasks": tasks}


class TestFiltering:
    """Test filtering query parameters."""

    def test_filter_by_status(self, client):
        """Test filtering tasks by status."""
        setup_test_data(client)

        # Filter for todo tasks
        response = client.get("/tasks/?filter[status]=todo")
        tasks = response.json()

        assert all(t["status"] == "todo" for t in tasks)
        assert len(tasks) >= 2  # At least 2 from our test data

    def test_filter_by_priority(self, client):
        """Test filtering tasks by priority."""
        setup_test_data(client)

        # Filter for critical priority
        response = client.get("/tasks/?filter[priority]=1")
        tasks = response.json()

        assert all(t["priority"] == 1 for t in tasks)
        assert len(tasks) >= 2  # At least 2 from our test data

    def test_filter_by_role(self, client):
        """Test filtering users by role."""
        setup_test_data(client)

        # Filter for admins
        response = client.get("/users/?filter[role]=admin")
        users = response.json()

        assert all(u["role"] == "admin" for u in users)
        assert len(users) >= 1


class TestSorting:
    """Test sorting query parameters."""

    def test_sort_by_priority_ascending(self, client):
        """Test sorting tasks by priority ascending."""
        setup_test_data(client)

        response = client.get("/tasks/?sort=priority")
        tasks = response.json()

        priorities = [t["priority"] for t in tasks]
        assert priorities == sorted(priorities)

    def test_sort_by_priority_descending(self, client):
        """Test sorting tasks by priority descending."""
        setup_test_data(client)

        response = client.get("/tasks/?sort=-priority")
        tasks = response.json()

        priorities = [t["priority"] for t in tasks]
        assert priorities == sorted(priorities, reverse=True)

    def test_sort_by_name(self, client):
        """Test sorting users by name."""
        setup_test_data(client)

        response = client.get("/users/?sort=name")
        users = response.json()

        names = [u["name"] for u in users]
        assert names == sorted(names)


class TestPagination:
    """Test pagination query parameters."""

    def test_limit(self, client):
        """Test limiting results."""
        setup_test_data(client)

        response = client.get("/tasks/?limit=2")
        tasks = response.json()

        assert len(tasks) == 2

    def test_offset(self, client):
        """Test offset pagination."""
        setup_test_data(client)

        # Get all tasks sorted
        response = client.get("/tasks/?sort=title")
        all_tasks = response.json()

        # Get with offset
        response = client.get("/tasks/?sort=title&offset=2")
        offset_tasks = response.json()

        assert offset_tasks[0]["title"] == all_tasks[2]["title"]

    def test_limit_and_offset(self, client):
        """Test combined limit and offset."""
        setup_test_data(client)

        # Get page 2 (2 items per page)
        response = client.get("/tasks/?sort=title&limit=2&offset=2")
        tasks = response.json()

        assert len(tasks) == 2


class TestCombinedQueries:
    """Test combining filter, sort, and pagination."""

    def test_filter_and_sort(self, client):
        """Test filtering and sorting together."""
        setup_test_data(client)

        response = client.get("/tasks/?filter[status]=todo&sort=-priority")
        tasks = response.json()

        # All should be todo
        assert all(t["status"] == "todo" for t in tasks)
        # Should be sorted by priority descending
        priorities = [t["priority"] for t in tasks]
        assert priorities == sorted(priorities, reverse=True)

    def test_filter_sort_and_paginate(self, client):
        """Test filter, sort, and pagination together."""
        setup_test_data(client)

        response = client.get("/tasks/?filter[status]=in_progress&sort=title&limit=1")
        tasks = response.json()

        assert len(tasks) == 1
        assert tasks[0]["status"] == "in_progress"
