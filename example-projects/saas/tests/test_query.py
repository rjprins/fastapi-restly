"""Tests for filtering, sorting, and pagination."""

import uuid

import pytest
from app.main import app

from fastapi_restly.testing import RestlyTestClient


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


class TestV2QueryModifiers:
    """Test V2 (direct field name) query parameter style on LabelView.

    LabelView sets query_modifier_version = QueryModifierVersion.V2, so it
    accepts ``name=urgent`` instead of the V1 ``filter[name]=urgent``.
    """

    def _setup_labels(self, client):
        unique = str(uuid.uuid4())[:8]
        response = client.post(
            "/organizations/",
            json={"name": f"Label Org {unique}", "slug": f"label-org-{unique}"},
        )
        org_id = response.json()["id"]

        client.post("/labels/", json={"name": "urgent", "color": "#ff0000", "organization_id": org_id})
        client.post("/labels/", json={"name": "feature", "color": "#00ff00", "organization_id": org_id})
        client.post("/labels/", json={"name": "bug", "color": "#0000ff", "organization_id": org_id})
        return org_id

    def test_v2_filter_by_name(self, client):
        """V2 filter: ?name=urgent returns only labels named 'urgent'."""
        self._setup_labels(client)

        response = client.get("/labels/?name=urgent")
        labels = response.json()

        assert all(lb["name"] == "urgent" for lb in labels)
        assert len(labels) >= 1

    def test_v2_sort_by_name(self, client):
        """V2 sort: ?order_by=name returns labels sorted alphabetically."""
        self._setup_labels(client)

        response = client.get("/labels/?order_by=name")
        labels = response.json()

        names = [lb["name"] for lb in labels]
        assert names == sorted(names)

    def test_v2_pagination(self, client):
        """V2 pagination: ?page=1&page_size=2 returns at most 2 items."""
        self._setup_labels(client)

        response = client.get("/labels/?page_size=2&page=1")
        labels = response.json()

        assert len(labels) <= 2

    def test_v2_filter_composes_with_tenant_scope(self, client):
        """LabelView's V2 filters compose with TenantScopedMixin."""
        import app.views._base as base_view

        org1 = client.post(
            "/organizations/",
            json={"name": "Scoped Labels 1", "slug": "scoped-labels-1"},
        ).json()
        org2 = client.post(
            "/organizations/",
            json={"name": "Scoped Labels 2", "slug": "scoped-labels-2"},
        ).json()

        label1 = client.post(
            "/labels/",
            json={
                "name": "shared",
                "color": "#ff0000",
                "organization_id": org1["id"],
            },
        ).json()
        label2 = client.post(
            "/labels/",
            json={
                "name": "shared",
                "color": "#00ff00",
                "organization_id": org2["id"],
            },
        ).json()

        base_view._TEST_ORG_ID = org1["id"]
        try:
            response = client.get("/labels/?name=shared")
            labels = response.json()
        finally:
            base_view._TEST_ORG_ID = None

        assert [label["id"] for label in labels] == [label1["id"]]
        assert label2["id"] not in {label["id"] for label in labels}


class TestPaginationMetadata:
    """Test include_pagination_metadata on ProjectView."""

    def test_project_list_returns_pagination_envelope(self, client):
        """Project list response wraps items in a pagination envelope."""
        unique = str(uuid.uuid4())[:8]
        response = client.post(
            "/organizations/",
            json={"name": f"Pag Org {unique}", "slug": f"pag-org-{unique}"},
        )
        org_id = response.json()["id"]

        for i in range(3):
            client.post("/projects/", json={"name": f"Project {i}", "organization_id": org_id})

        response = client.get("/projects/")
        data = response.json()

        # With include_pagination_metadata = True, the response is an envelope
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert "total_pages" in data
        assert data["total"] >= 3
        assert isinstance(data["items"], list)
