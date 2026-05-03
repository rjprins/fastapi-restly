"""CRUD tests for the Task model — basic CRUD, polymorphism, bulk ops, soft-delete, optimistic locking, computed fields, and workflow."""


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
        response = client.get("/tasks/?task_type=bug")
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
        projects = response.json()["items"]
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
        projects = response.json()["items"]
        project_ids = [p["id"] for p in projects]
        assert active_id in project_ids
        assert deleted_id not in project_ids

        # List with include_deleted=true
        response = client.get("/projects/?include_deleted=true")
        projects = response.json()["items"]
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
        projects = response.json()["items"]
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

    def test_project_response_includes_task_rollups(self, client):
        """Test that ProjectSchema response-only rollups are populated."""
        response = client.post(
            "/organizations/",
            json={"name": "Response Rollup Org", "slug": "response-rollup-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/",
            json={"name": "Response Rollup Project", "organization_id": org_id},
        )
        project_id = response.json()["id"]

        client.post(
            "/tasks/",
            json={"title": "Rollup Todo", "status": "todo", "project_id": project_id},
        )
        client.post(
            "/tasks/",
            json={"title": "Rollup Done", "status": "done", "project_id": project_id},
        )

        project = client.get(f"/projects/{project_id}").json()

        assert project["task_count"] == 2
        assert project["completed_task_count"] == 1
        assert project["completion_percent"] == 50.0

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
