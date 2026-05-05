"""Cross-resource and framework-feature tests — reporting, validation patterns, tenant isolation, and row/field-level permissions."""


class TestReportingEndpoints:
    """Test reporting/stats endpoints."""

    def test_project_stats(self, client):
        """Test GET /projects/{id}/stats returns correct counts."""
        # Create org and project
        response = client.post(
            "/organizations/", json={"name": "Stats Test Org", "slug": "stats-test-org"}
        )
        org_id = response.json()["id"]

        response = client.post(
            "/projects/", json={"name": "Stats Project", "organization_id": org_id}
        )
        project_id = response.json()["id"]

        # Add tasks with different statuses
        client.post(
            "/tasks/",
            json={"title": "Todo 1", "status": "todo", "project_id": project_id},
        )
        client.post(
            "/tasks/",
            json={"title": "Todo 2", "status": "todo", "project_id": project_id},
        )
        client.post(
            "/tasks/",
            json={
                "title": "In Progress",
                "status": "in_progress",
                "project_id": project_id,
            },
        )
        client.post(
            "/tasks/",
            json={"title": "Done 1", "status": "done", "project_id": project_id},
        )
        client.post(
            "/tasks/",
            json={"title": "Done 2", "status": "done", "project_id": project_id},
        )
        client.post(
            "/tasks/",
            json={"title": "Done 3", "status": "done", "project_id": project_id},
        )

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
            "/projects/", json={"name": "Org 1 Project", "organization_id": org1_id}
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
            "/organizations/", json={"name": "Same Org Test", "slug": "same-org-test"}
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
            "/projects/", json={"name": "Same Org Project", "organization_id": org_id}
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
            "/tasks/", json={"title": "Unassigned task", "project_id": project_in_org1}
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
            "/organizations/", json={"name": "Valid Org", "slug": "valid-slug-123"}
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
            f"/organizations/{org_id}", json={"name": "Updated Name"}
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
        response = client.patch(f"/organizations/{org_id}", json={"slug": "new-slug"})
        updated = response.json()

        # Slug should be unchanged (field not in update_schema)
        assert updated["slug"] == "original-slug"


class TestTenantIsolation:
    """Test tenant isolation (org scoping) for projects."""

    def test_tenant_isolation_filters_list(self, client, auth_context):
        """Test that handle_listing filters by current org when set."""
        # Create two orgs
        response = client.post(
            "/organizations/", json={"name": "Tenant Org 1", "slug": "tenant-org-1"}
        )
        org1_id = response.json()["id"]

        response = client.post(
            "/organizations/", json={"name": "Tenant Org 2", "slug": "tenant-org-2"}
        )
        org2_id = response.json()["id"]

        # Create project in each org
        response = client.post(
            "/projects/", json={"name": "Org 1 Project", "organization_id": org1_id}
        )
        org1_project_id = response.json()["id"]

        response = client.post(
            "/projects/", json={"name": "Org 2 Project", "organization_id": org2_id}
        )
        org2_project_id = response.json()["id"]

        # Without tenant isolation, both projects visible
        response = client.get("/projects/")
        all_projects = response.json()["items"]
        all_ids = [p["id"] for p in all_projects]
        assert org1_project_id in all_ids
        assert org2_project_id in all_ids

        # With tenant isolation for org1, only org1 projects visible
        with auth_context(org_id=org1_id):
            response = client.get("/projects/")
            filtered_projects = response.json()["items"]
            filtered_ids = [p["id"] for p in filtered_projects]
            assert org1_project_id in filtered_ids
            assert org2_project_id not in filtered_ids

    def test_tenant_isolation_blocks_get_other_org(self, client, auth_context):
        """Test that handle_retrieve returns 404 for other org's resources."""
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
        with auth_context(org_id=org1_id):
            response = client.get(
                f"/projects/{org2_project_id}", assert_status_code=404
            )

    def test_tenant_isolation_allows_own_org(self, client, auth_context):
        """Test that handle_retrieve allows access to own org's resources."""
        # Create org
        response = client.post(
            "/organizations/", json={"name": "Own Tenant Org", "slug": "own-tenant-org"}
        )
        org_id = response.json()["id"]

        # Create project in org
        response = client.post(
            "/projects/", json={"name": "Own Org Project", "organization_id": org_id}
        )
        project_id = response.json()["id"]

        # With tenant isolation for same org, can access project
        with auth_context(org_id=org_id):
            response = client.get(f"/projects/{project_id}")
            project = response.json()
            assert project["id"] == project_id


class TestRowLevelPermissions:
    """Test row-level permissions (filter results by user permissions)."""

    def test_row_level_filters_task_list(self, client, auth_context):
        """Test that handle_listing filters tasks by current user."""
        # Create org, users, and project
        response = client.post(
            "/organizations/", json={"name": "Row Level Org", "slug": "row-level-org"}
        )
        org_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={
                "email": "user1@row.com",
                "name": "User 1",
                "organization_id": org_id,
            },
        )
        user1_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={
                "email": "user2@row.com",
                "name": "User 2",
                "organization_id": org_id,
            },
        )
        user2_id = response.json()["id"]

        response = client.post(
            "/projects/", json={"name": "Row Level Project", "organization_id": org_id}
        )
        project_id = response.json()["id"]

        # Create tasks assigned to different users
        response = client.post(
            "/tasks/",
            json={
                "title": "User 1 Task",
                "project_id": project_id,
                "assignee_id": user1_id,
            },
        )
        user1_task_id = response.json()["id"]

        response = client.post(
            "/tasks/",
            json={
                "title": "User 2 Task",
                "project_id": project_id,
                "assignee_id": user2_id,
            },
        )
        user2_task_id = response.json()["id"]

        # Without row-level permissions, all tasks visible
        response = client.get("/tasks/")
        all_tasks = response.json()
        all_ids = [t["id"] for t in all_tasks]
        assert user1_task_id in all_ids
        assert user2_task_id in all_ids

        # With row-level permissions for user1, only user1's tasks visible
        with auth_context(user_id=user1_id):
            response = client.get("/tasks/")
            filtered_tasks = response.json()
            filtered_ids = [t["id"] for t in filtered_tasks]
            assert user1_task_id in filtered_ids
            assert user2_task_id not in filtered_ids

    def test_row_level_blocks_get_other_user_task(self, client, auth_context):
        """Test that handle_retrieve returns 404 for other user's tasks."""
        # Create org, users, and project
        response = client.post(
            "/organizations/",
            json={"name": "Get Row Level Org", "slug": "get-row-level-org"},
        )
        org_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={
                "email": "getuser1@row.com",
                "name": "Get User 1",
                "organization_id": org_id,
            },
        )
        user1_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={
                "email": "getuser2@row.com",
                "name": "Get User 2",
                "organization_id": org_id,
            },
        )
        user2_id = response.json()["id"]

        response = client.post(
            "/projects/", json={"name": "Get Row Project", "organization_id": org_id}
        )
        project_id = response.json()["id"]

        # Create task assigned to user2
        response = client.post(
            "/tasks/",
            json={
                "title": "User 2 Only Task",
                "project_id": project_id,
                "assignee_id": user2_id,
            },
        )
        user2_task_id = response.json()["id"]

        # User1 cannot access user2's task
        with auth_context(user_id=user1_id):
            response = client.get(f"/tasks/{user2_task_id}", assert_status_code=404)

    def test_row_level_allows_own_task(self, client, auth_context):
        """Test that handle_retrieve allows access to user's own tasks."""
        # Create org, user, and project
        response = client.post(
            "/organizations/", json={"name": "Own Task Org", "slug": "own-task-org"}
        )
        org_id = response.json()["id"]

        response = client.post(
            "/users/",
            json={
                "email": "ownuser@row.com",
                "name": "Own User",
                "organization_id": org_id,
            },
        )
        user_id = response.json()["id"]

        response = client.post(
            "/projects/", json={"name": "Own Task Project", "organization_id": org_id}
        )
        project_id = response.json()["id"]

        # Create task assigned to user
        response = client.post(
            "/tasks/",
            json={
                "title": "My Own Task",
                "project_id": project_id,
                "assignee_id": user_id,
            },
        )
        task_id = response.json()["id"]

        # User can access own task
        with auth_context(user_id=user_id):
            response = client.get(f"/tasks/{task_id}")
            task = response.json()
            assert task["id"] == task_id


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
        user_view._TEST_USER_ROLE = UserRole.HR
        try:
            response = client.get(f"/users/{user_id}/with-permissions")
            user = response.json()

            # HR can see salary
            assert "salary" in user
            assert user["salary"] == 75000
        finally:
            user_view._TEST_USER_ROLE = None  # Reset

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
        user_view._TEST_USER_ROLE = UserRole.MEMBER
        try:
            response = client.get(f"/users/{user_id}/with-permissions")
            user = response.json()

            # Member cannot see salary (not in public schema)
            assert "salary" not in user
        finally:
            user_view._TEST_USER_ROLE = None  # Reset

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
        user_view._TEST_USER_ROLE = UserRole.OWNER
        try:
            response = client.get(f"/users/{user_id}/with-permissions")
            user = response.json()

            # Owner can see salary
            assert "salary" in user
            assert user["salary"] == 100000
        finally:
            user_view._TEST_USER_ROLE = None  # Reset

    def test_no_role_cannot_see_salary(self, client):
        """Test that without a role set, salary is hidden."""
        import app.views.user as user_view

        # Create org and user with salary
        response = client.post(
            "/organizations/", json={"name": "No Role Org", "slug": "no-role-org"}
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
        user_view._TEST_USER_ROLE = None
        try:
            response = client.get(f"/users/{user_id}/with-permissions")
            user = response.json()

            # No role means no salary access
            assert "salary" not in user
        finally:
            user_view._TEST_USER_ROLE = None  # Reset
