"""Cross-resource and framework-feature tests — reporting, validation patterns, tenant isolation, and row/field-level permissions."""

from app.users.roles import UserRole


class TestReportingEndpoints:
    """Test reporting/stats endpoints."""

    def test_project_stats(self, client):
        """Test GET /projects/{id}/stats returns correct counts."""
        # Create project
        response = client.post("/projects", json={"name": "Stats Project"})
        project_id = response.json()["id"]

        # Add tasks with different statuses
        client.post(
            "/tasks",
            json={"title": "Todo 1", "status": "todo", "project_id": project_id},
        )
        client.post(
            "/tasks",
            json={"title": "Todo 2", "status": "todo", "project_id": project_id},
        )
        client.post(
            "/tasks",
            json={
                "title": "In Progress",
                "status": "in_progress",
                "project_id": project_id,
            },
        )
        client.post(
            "/tasks",
            json={"title": "Done 1", "status": "done", "project_id": project_id},
        )
        client.post(
            "/tasks",
            json={"title": "Done 2", "status": "done", "project_id": project_id},
        )
        client.post(
            "/tasks",
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
        # Create project
        response = client.post("/projects", json={"name": "Empty Stats Project"})
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
        # Create project
        response = client.post("/projects", json={"name": "Conditional Project"})
        project_id = response.json()["id"]

        # Try to create bug without severity - should fail
        response = client.post(
            "/tasks",
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
        # Create project
        response = client.post("/projects", json={"name": "Bug Severity Project"})
        project_id = response.json()["id"]

        # Create bug with severity - should succeed
        response = client.post(
            "/tasks",
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
        # Create project
        response = client.post("/projects", json={"name": "Feature No Sev Project"})
        project_id = response.json()["id"]

        # Create feature without severity - should succeed (severity is bug-only)
        response = client.post(
            "/tasks",
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
        # Create project
        response = client.post("/projects", json={"name": "Task No Sev Project"})
        project_id = response.json()["id"]

        # Create regular task without severity - should succeed
        response = client.post(
            "/tasks",
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
    """Cross-resource validation: the assignee must be in the project's org.

    For a tenant's user the tenant restriction settles it: another
    organization's user is not a row that exists, so the reference check
    answers 404 and the rule in ``TaskView._validate_cross_resource``
    never sees the user. The rule is what stops an admin, whose reads
    cross tenants.
    """

    def test_assignee_from_another_org_does_not_exist_for_a_tenant(
        self, client, new_tenant
    ):
        """The other organization's user is not a reference that exists."""
        beta = new_tenant("beta")
        project_id = client.post("/projects", json={"name": "Acme Project"}).json()[
            "id"
        ]

        client.post(
            "/tasks",
            json={
                "title": "Cross-org assignment",
                "project_id": project_id,
                "assignee_id": beta.user_id,
            },
            assert_status_code=404,
        )

    def test_admin_cannot_assign_across_organizations(
        self, client, new_tenant, as_admin, actor
    ):
        """The admin sees both users; the rule still refuses the assignment."""
        beta = new_tenant("beta")
        project_id = client.post("/projects", json={"name": "Acme Project"}).json()[
            "id"
        ]

        with as_admin(actor.org_id):
            response = client.post(
                "/tasks",
                json={
                    "title": "Cross-org assignment",
                    "project_id": project_id,
                    "assignee_id": beta.user_id,
                },
                assert_status_code=422,
            )
        assert "same organization" in response.json()["detail"]

    def test_create_task_with_assignee_from_same_org_succeeds(self, client):
        """Test that creating a task with assignee from same org succeeds."""
        # Create user in org
        response = client.post(
            "/users", json={"email": "user@sameorg.com", "name": "Same Org User"}
        )
        user_id = response.json()["id"]

        # Create project in org
        response = client.post("/projects", json={"name": "Same Org Project"})
        project_id = response.json()["id"]

        # Create task with same-org assignee - should succeed
        response = client.post(
            "/tasks",
            json={
                "title": "Same-org assignment",
                "project_id": project_id,
                "assignee_id": user_id,
            },
        )
        task = response.json()

        assert task["assignee_id"] == user_id

    def test_update_task_assignee_to_another_org_is_not_found(self, client, new_tenant):
        """On update too, the other organization's user does not exist."""
        beta = new_tenant("beta")
        project_id = client.post("/projects", json={"name": "Acme Project"}).json()[
            "id"
        ]
        task_id = client.post(
            "/tasks", json={"title": "Unassigned task", "project_id": project_id}
        ).json()["id"]

        client.patch(
            f"/tasks/{task_id}",
            json={"assignee_id": beta.user_id},
            assert_status_code=404,
        )


class TestDifferentSchemasPerOperation:
    """Test different schemas per operation (schema_create, schema_update)."""

    def test_create_org_with_invalid_slug_fails(self, client):
        """Test that schema_create validates slug format."""
        # Try to create with uppercase slug - should fail
        response = client.post(
            "/organizations",
            json={"name": "Test Org", "slug": "UPPERCASE"},
            assert_status_code=422,
        )
        error = response.json()
        # Check validation error mentions slug format
        assert any("slug" in str(e).lower() for e in error.get("detail", []))

    def test_create_org_with_spaces_in_slug_fails(self, client):
        """Test that schema_create rejects slugs with spaces."""
        response = client.post(
            "/organizations",
            json={"name": "Test Org", "slug": "has spaces"},
            assert_status_code=422,
        )
        error = response.json()
        assert any("slug" in str(e).lower() for e in error.get("detail", []))

    def test_create_org_with_valid_slug_succeeds(self, client):
        """Test that schema_create accepts valid slugs."""
        response = client.post(
            "/organizations", json={"name": "Valid Org", "slug": "valid-slug-123"}
        )
        org = response.json()

        assert org["slug"] == "valid-slug-123"
        assert org["name"] == "Valid Org"

    def test_create_org_with_short_name_fails(self, client):
        """Test that schema_create requires minimum name length."""
        response = client.post(
            "/organizations",
            json={"name": "X", "slug": "short-name"},
            assert_status_code=422,
        )
        error = response.json()
        assert any("name" in str(e).lower() for e in error.get("detail", []))

    def test_update_org_name_only(self, client):
        """Test that schema_update only allows name updates."""
        # Create org
        response = client.post(
            "/organizations",
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
        """Test that schema_update ignores slug changes (not in schema)."""
        # Create org
        response = client.post(
            "/organizations", json={"name": "Slug Update Test", "slug": "original-slug"}
        )
        org_id = response.json()["id"]

        # Try to update slug - should be ignored (not in schema_update)
        response = client.patch(f"/organizations/{org_id}", json={"slug": "new-slug"})
        updated = response.json()

        # Slug should be unchanged (field not in schema_update)
        assert updated["slug"] == "original-slug"


class TestTenantIsolation:
    """Tenant isolation: a request reads the organization it acts in."""

    def test_tenant_isolation_filters_list(self, client, new_tenant, auth_context):
        """The list (get_many) read scope filters by the acting organization."""
        beta = new_tenant("beta")
        acme_project_id = client.post(
            "/projects", json={"name": "Acme Project"}
        ).json()["id"]
        with beta.acting():
            beta_project_id = client.post(
                "/projects", json={"name": "Beta Project"}
            ).json()["id"]

        # Acme's user sees Acme's project only
        ids = [p["id"] for p in client.get("/projects").json()["data"]]
        assert acme_project_id in ids
        assert beta_project_id not in ids

        # Beta's user, Beta's
        with beta.acting():
            ids = [p["id"] for p in client.get("/projects").json()["data"]]
        assert beta_project_id in ids
        assert acme_project_id not in ids

        # An admin's reads cross tenants
        with auth_context(is_admin=True):
            ids = [p["id"] for p in client.get("/projects").json()["data"]]
        assert {acme_project_id, beta_project_id} <= set(ids)

    def test_tenant_isolation_blocks_get_other_org(self, client, new_tenant):
        """Test that get_one returns 404 for other org's resources."""
        beta = new_tenant("beta")
        with beta.acting():
            beta_project_id = client.post(
                "/projects", json={"name": "Beta Secret Project"}
            ).json()["id"]

        client.get(f"/projects/{beta_project_id}", assert_status_code=404)

    def test_tenant_isolation_allows_own_org(self, client):
        """Test that get_one allows access to own org's resources."""
        project_id = client.post("/projects", json={"name": "Own Org Project"}).json()[
            "id"
        ]

        project = client.get(f"/projects/{project_id}").json()
        assert project["id"] == project_id

    def test_rows_land_in_the_acting_organization(self, client, actor, new_tenant):
        """``organization_id`` is a stamp: a body naming another tenant is ignored."""
        beta = new_tenant("beta")

        project = client.post(
            "/projects", json={"name": "Stamped", "organization_id": beta.org_id}
        ).json()
        user = client.post(
            "/users",
            json={
                "email": "new@acme.test",
                "name": "New",
                "organization_id": beta.org_id,
            },
        ).json()
        label = client.post(
            "/labels", json={"name": "stamped", "organization_id": beta.org_id}
        ).json()

        assert project["organization_id"] == actor.org_id
        assert user["organization_id"] == actor.org_id
        assert label["organization_id"] == actor.org_id


class TestRowLevelPermissions:
    """Row-level permissions: a member sees the tasks assigned to them.

    The acting owner sees the organization's tasks; these tests act as the
    members they create to exercise the restriction.
    """

    def _two_members_two_tasks(self, client):
        user1_id = client.post(
            "/users", json={"email": "user1@row.com", "name": "User 1"}
        ).json()["id"]
        user2_id = client.post(
            "/users", json={"email": "user2@row.com", "name": "User 2"}
        ).json()["id"]
        project_id = client.post(
            "/projects", json={"name": "Row Level Project"}
        ).json()["id"]
        user1_task_id = client.post(
            "/tasks",
            json={
                "title": "User 1 Task",
                "project_id": project_id,
                "assignee_id": user1_id,
            },
        ).json()["id"]
        user2_task_id = client.post(
            "/tasks",
            json={
                "title": "User 2 Task",
                "project_id": project_id,
                "assignee_id": user2_id,
            },
        ).json()["id"]
        return user1_id, user2_id, user1_task_id, user2_task_id

    def test_row_level_filters_task_list(self, client, auth_context):
        """The list (get_many) read scope filters tasks by the acting member."""
        user1_id, _user2_id, user1_task_id, user2_task_id = self._two_members_two_tasks(
            client
        )

        # The owner sees every task of the organization
        all_ids = [t["id"] for t in client.get("/tasks").json()["data"]]
        assert user1_task_id in all_ids
        assert user2_task_id in all_ids

        # A member sees only the tasks assigned to them
        with auth_context(user_id=user1_id, role=UserRole.MEMBER):
            filtered_ids = [t["id"] for t in client.get("/tasks").json()["data"]]
        assert user1_task_id in filtered_ids
        assert user2_task_id not in filtered_ids

    def test_row_level_blocks_get_other_user_task(self, client, auth_context):
        """Test that get_one returns 404 for other user's tasks."""
        user1_id, _user2_id, _user1_task_id, user2_task_id = (
            self._two_members_two_tasks(client)
        )

        with auth_context(user_id=user1_id, role=UserRole.MEMBER):
            client.get(f"/tasks/{user2_task_id}", assert_status_code=404)

    def test_row_level_allows_own_task(self, client, auth_context):
        """Test that get_one allows access to user's own tasks."""
        user1_id, _user2_id, user1_task_id, _user2_task_id = (
            self._two_members_two_tasks(client)
        )

        with auth_context(user_id=user1_id, role=UserRole.MEMBER):
            task = client.get(f"/tasks/{user1_task_id}").json()
        assert task["id"] == user1_task_id

    def test_other_roles_see_the_organizations_tasks(self, client, auth_context):
        """The restriction is the member's: HR, like the owner, sees them all."""
        _user1_id, _user2_id, user1_task_id, user2_task_id = (
            self._two_members_two_tasks(client)
        )
        hr_id = client.post(
            "/users", json={"email": "hr@row.com", "name": "HR", "role": "hr"}
        ).json()["id"]

        with auth_context(user_id=hr_id, role=UserRole.HR):
            ids = {t["id"] for t in client.get("/tasks").json()["data"]}
        assert {user1_task_id, user2_task_id} <= ids


class TestFieldLevelPermissions:
    """Field-level permissions: the response schema follows ``Current.role``."""

    def _user_with_salary(self, client, salary: int) -> int:
        response = client.post(
            "/users",
            json={
                "email": f"paid{salary}@field.com",
                "name": "Employee",
                "salary": salary,
            },
        )
        return response.json()["id"]

    def test_hr_can_see_salary(self, client, auth_context):
        """Test that HR role can see salary field."""
        user_id = self._user_with_salary(client, 75000)

        with auth_context(role=UserRole.HR):
            user = client.get(f"/users/{user_id}/with-permissions").json()

        assert user["salary"] == 75000

    def test_member_cannot_see_salary(self, client, auth_context):
        """Test that member role cannot see salary field."""
        user_id = self._user_with_salary(client, 90000)

        with auth_context(role=UserRole.MEMBER):
            user = client.get(f"/users/{user_id}/with-permissions").json()

        # Member cannot see salary (not in public schema)
        assert "salary" not in user

    def test_owner_can_see_salary(self, client):
        """Test that the owner role can also see salary field; the client acts as one."""
        user_id = self._user_with_salary(client, 100000)

        user = client.get(f"/users/{user_id}/with-permissions").json()

        assert user["salary"] == 100000

    def test_org_admin_cannot_see_salary(self, client, auth_context):
        """Test that an organization admin does not see salary; that is HR's."""
        user_id = self._user_with_salary(client, 80000)

        with auth_context(role=UserRole.ADMIN):
            user = client.get(f"/users/{user_id}/with-permissions").json()

        assert "salary" not in user
