"""CRUD tests for the Project model — basic CRUD, archive/lifecycle, clone, and nested routes."""


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
        response = client.get(f"/tasks/?project_id={cloned['id']}")
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
        response = client.get(f"/tasks/?project_id={cloned['id']}")
        cloned_tasks = response.json()
        assert len(cloned_tasks) == 0



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
        client.get("/projects/99999/tasks", assert_status_code=404)



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
