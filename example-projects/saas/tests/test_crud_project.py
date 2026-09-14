"""CRUD tests for the Project model — basic CRUD, archive/lifecycle, clone, and nested routes."""

from app.users.roles import UserRole


class TestProjectCRUD:
    """Test Project CRUD operations."""

    def test_create_project(self, client):
        """Test creating a project."""
        # Create project
        response = client.post(
            "/projects", json={"name": "My Project", "description": "A test project"}
        )
        project = response.json()

        assert project["name"] == "My Project"
        assert project["status"] == "active"  # Default

    def test_archive_project(self, client):
        """Test archiving a project."""
        # Create project
        response = client.post("/projects", json={"name": "To Archive"})
        project_id = response.json()["id"]

        # Archive
        response = client.patch(f"/projects/{project_id}", json={"status": "archived"})
        project = response.json()

        assert project["status"] == "archived"


class TestProjectClone:
    """Test project cloning functionality."""

    def test_clone_project_with_tasks(self, client):
        """Test cloning a project including all tasks."""
        # Create project with tasks
        response = client.post(
            "/projects",
            json={"name": "Original Project", "description": "Original description"},
        )
        project_id = response.json()["id"]

        # Add tasks
        client.post("/tasks", json={"title": "Task 1", "project_id": project_id})
        client.post("/tasks", json={"title": "Task 2", "project_id": project_id})
        client.post("/tasks", json={"title": "Task 3", "project_id": project_id})

        # Clone project
        response = client.post(
            f"/projects/{project_id}/clone", json={"new_name": "Cloned Project"}
        )
        cloned = response.json()

        assert cloned["name"] == "Cloned Project"
        assert cloned["description"] == "Original description"
        assert cloned["status"] == "active"
        assert cloned["id"] != project_id

        # Verify tasks were cloned
        response = client.get(f"/tasks?project_id={cloned['id']}")
        cloned_tasks = response.json()["data"]
        assert len(cloned_tasks) == 3

    def test_clone_project_default_name(self, client):
        """Test cloning with default name appends (Copy)."""
        # Create project
        response = client.post("/projects", json={"name": "My Project"})
        project_id = response.json()["id"]

        # Clone without specifying name
        response = client.post(f"/projects/{project_id}/clone", json={})
        cloned = response.json()

        assert cloned["name"] == "My Project (Copy)"

    def test_clone_project_without_tasks(self, client):
        """Test cloning without including tasks."""
        # Create project with tasks
        response = client.post("/projects", json={"name": "Project With Tasks"})
        project_id = response.json()["id"]

        client.post("/tasks", json={"title": "Task 1", "project_id": project_id})

        # Clone without tasks
        response = client.post(
            f"/projects/{project_id}/clone", json={"include_tasks": False}
        )
        cloned = response.json()

        # Verify no tasks were cloned
        response = client.get(f"/tasks?project_id={cloned['id']}")
        cloned_tasks = response.json()["data"]
        assert len(cloned_tasks) == 0


class TestNestedRoutes:
    """Test nested routes for tasks within projects."""

    def test_list_project_tasks(self, client):
        """Test GET /projects/{id}/tasks lists only that project's tasks."""
        # Create two projects
        response = client.post("/projects", json={"name": "Project 1"})
        project1_id = response.json()["id"]

        response = client.post("/projects", json={"name": "Project 2"})
        project2_id = response.json()["id"]

        # Add tasks to each project
        client.post("/tasks", json={"title": "P1 Task 1", "project_id": project1_id})
        client.post("/tasks", json={"title": "P1 Task 2", "project_id": project1_id})
        client.post("/tasks", json={"title": "P2 Task 1", "project_id": project2_id})

        # List tasks for project 1
        response = client.get(f"/projects/{project1_id}/tasks")
        tasks = response.json()

        assert len(tasks) == 2
        assert all(t["project_id"] == project1_id for t in tasks)

    def test_list_project_tasks_follows_the_task_view_scope(self, client, auth_context):
        """The nested listing and ``GET /tasks`` answer with the same rows.

        ``list_project_tasks`` applies ``fr.resolve_scope(TaskView)``, so a
        member sees the tasks assigned to them on both routes. Re-spelling
        the clause here is what would let the two drift apart.
        """
        project_id = client.post("/projects", json={"name": "Shared"}).json()["id"]
        member = client.post(
            "/users", json={"email": "mem@acme.test", "name": "Mem", "role": "member"}
        ).json()

        mine = client.post(
            "/tasks",
            json={
                "title": "Mine",
                "project_id": project_id,
                "assignee_id": member["id"],
            },
        ).json()
        client.post("/tasks", json={"title": "Theirs", "project_id": project_id})

        # the owner sees the whole project on the nested route
        nested = client.get(f"/projects/{project_id}/tasks").json()
        assert {task["title"] for task in nested} == {"Mine", "Theirs"}

        # the member sees their own assignment, and both routes agree
        with auth_context(user_id=member["id"], role=UserRole.MEMBER):
            nested = client.get(f"/projects/{project_id}/tasks").json()
            listed = client.get(f"/tasks?project_id={project_id}").json()["data"]

        assert [task["id"] for task in nested] == [mine["id"]]
        assert [task["id"] for task in listed] == [mine["id"]]

    def test_create_task_via_nested_route(self, client):
        """Test POST /projects/{id}/tasks creates task with correct project_id."""
        # Create project
        response = client.post("/projects", json={"name": "Nested Project"})
        project_id = response.json()["id"]

        # Create task via nested route (no project_id in body)
        response = client.post(
            f"/projects/{project_id}/tasks", json={"title": "Nested Task"}
        )
        task = response.json()

        assert task["title"] == "Nested Task"
        assert task["project_id"] == project_id

    def test_nested_route_with_nonexistent_project(self, client):
        """Test that nested routes return 404 for nonexistent project."""
        client.get("/projects/99999/tasks", assert_status_code=404)


class TestNaturalKeyRoutes:
    """The by-slug routes: retrieve and update under the natural key."""

    def test_get_by_slug(self, client):
        """``GET /projects/by-slug/{slug}`` is ``GET /{id}`` under the other key."""
        created = client.post("/projects", json={"name": "Apollo Program"}).json()
        assert created["slug"] == "apollo-program"

        found = client.get("/projects/by-slug/apollo-program").json()
        assert found["id"] == created["id"]
        # the decorated fields come from get_one, as they do on GET /{id}
        assert found["task_count"] == 0

        client.get("/projects/by-slug/nope", assert_status_code=404)

    def test_by_slug_carries_the_organization(self, client, new_tenant):
        """A slug is unique inside an organization, so the key holds both."""
        beta = new_tenant("beta")
        client.post("/projects", json={"name": "Shared Name"})

        with beta.acting():
            theirs = client.post("/projects", json={"name": "Shared Name"}).json()
            found = client.get("/projects/by-slug/shared-name").json()
            assert found["id"] == theirs["id"]

        ours = client.get("/projects/by-slug/shared-name").json()
        assert ours["id"] != theirs["id"]

    def test_update_by_slug(self, client):
        """``PATCH`` by slug runs the same commit bracket as ``PATCH /{id}``."""
        created = client.post("/projects", json={"name": "Gemini"}).json()

        updated = client.patch(
            "/projects/by-slug/gemini", json={"description": "Two seats"}
        ).json()
        assert updated["id"] == created["id"]
        assert updated["description"] == "Two seats"

        # the write is committed, not just reflected in the response
        stored = client.get(f"/projects/{created['id']}").json()
        assert stored["description"] == "Two seats"

    def test_update_by_slug_on_a_missing_slug_is_404(self, client):
        client.patch(
            "/projects/by-slug/nope", json={"description": "x"}, assert_status_code=404
        )


class TestProjectLifecycle:
    """Test project lifecycle (archive) functionality."""

    def test_archive_project(self, client):
        """Test POST /projects/{id}/archive archives the project."""
        # Create project
        response = client.post("/projects", json={"name": "Project to Archive"})
        project = response.json()
        project_id = project["id"]
        assert project["status"] == "active"

        # Archive the project
        response = client.post(f"/projects/{project_id}/archive")
        archived = response.json()

        assert archived["status"] == "archived"

    def test_archive_already_archived_fails(self, client):
        """Test that archiving an already archived project fails."""
        # Create project
        response = client.post("/projects", json={"name": "Already Archived"})
        project_id = response.json()["id"]

        # Archive once
        client.post(f"/projects/{project_id}/archive")

        # Try to archive again
        response = client.post(
            f"/projects/{project_id}/archive", assert_status_code=400
        )
        error = response.json()
        assert "already archived" in error["detail"]

    def test_create_task_in_archived_project_fails(self, client):
        """Test that creating a task in an archived project fails."""
        # Create project
        response = client.post("/projects", json={"name": "Archived for Tasks"})
        project_id = response.json()["id"]

        # Archive the project
        client.post(f"/projects/{project_id}/archive")

        # Try to create a task - should fail
        response = client.post(
            "/tasks",
            json={"title": "Should Fail", "project_id": project_id},
            assert_status_code=400,
        )
        error = response.json()
        assert "archived" in error["detail"]

    def test_create_task_via_nested_route_in_archived_fails(self, client):
        """Test that nested route task creation fails for archived projects."""
        # Create project
        response = client.post("/projects", json={"name": "Nested Archived Project"})
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
