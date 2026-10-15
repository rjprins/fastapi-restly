"""Tests for the use-case-matrix patterns from rut-notes/discussion_save_object.md.

These exercise every row of the matrix that the SaaS example covers,
locking the expected behavior down so future helper/handler design experiments
have a benchmark to refactor against.

Uses ``fr.open_async_session()`` (savepoint-isolated, on the same connection
as the async test client) for direct DB inspection inside each test.
"""

import io
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone

from app.auth import verify_password
from app.current import Current
from app.users.models import User
from app.users.roles import UserRole
from sqlalchemy import delete, select

import fastapi_restly as fr


@asynccontextmanager
async def _async_session(actor):
    """A session bound to an explicit identity for direct database checks.

    ``fr.open_async_session()`` resolves the same source the request path does, so
    a row a route just wrote is visible here without reaching into Restly's
    internals.
    """
    with Current.bind(**asdict(actor)):
        async with fr.open_async_session() as session:
            yield session


# ---------------------------------------------------------------------------
# Password hashing on create + change-password action
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    """Use-case: hash password on user create — UserView.create (bare verb)."""

    def test_password_is_hashed_at_rest(self, client):
        """The plaintext from the body must never reach the database column."""
        response = client.post(
            "/users",
            json={
                "email": "anna@example.com",
                "name": "Anna",
                "password": "supersecret",
            },
        )
        assert response.status_code == 201
        # The plaintext must not appear in the response under any key.
        body = response.json()
        assert "supersecret" not in str(body)

    async def test_stored_value_is_a_real_hash(self, async_client, async_actor):
        """Round-trip: pull the row from the DB, verify hash matches plaintext."""
        from app.users.models import User

        await async_client.post(
            "/users",
            json={"email": "bob@example.com", "name": "Bob", "password": "trustno1"},
        )

        async with _async_session(async_actor) as session:
            user = (
                await session.scalars(
                    select(User).where(User.email == "bob@example.com")
                )
            ).one()
        assert user.password != "trustno1"  # not plaintext
        assert verify_password("trustno1", user.password)
        assert not verify_password("wrong", user.password)

    async def test_patch_does_not_change_the_password(self, async_client, async_actor):
        """PATCH has no password field: only change-password sets one."""
        from app.users.models import User

        created = await async_client.post(
            "/users",
            json={"email": "cleo@example.com", "name": "Cleo", "password": "trustno1"},
        )
        user_id = created.json()["id"]

        response = await async_client.patch(
            f"/users/{user_id}", json={"name": "Cleo B", "password": "plain-new"}
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Cleo B"

        async with _async_session(async_actor) as session:
            user = (await session.scalars(select(User).where(User.id == user_id))).one()
        assert user.password != "plain-new"
        assert verify_password("trustno1", user.password)

    def test_change_password_requires_current(self, client):
        """The action route rejects a wrong current password with 403."""
        u = client.post(
            "/users",
            json={"email": "carol@example.com", "name": "Carol", "password": "old-pw"},
        ).json()

        bad = client.post(
            f"/users/{u['id']}/change-password",
            json={"current_password": "wrong", "new_password": "x"},
            assert_status_code=403,
        )
        assert "incorrect" in bad.json()["detail"].lower()

    async def test_change_password_swaps_hash(self, async_client, async_actor):
        """Successful change replaces the stored digest."""
        from app.users.models import User

        u = (
            await async_client.post(
                "/users",
                json={
                    "email": "dave@example.com",
                    "name": "Dave",
                    "password": "old-pw",
                },
            )
        ).json()
        await async_client.post(
            f"/users/{u['id']}/change-password",
            json={"current_password": "old-pw", "new_password": "new-pw"},
        )
        async with _async_session(async_actor) as session:
            user = await session.get(User, u["id"])
        assert user is not None
        assert verify_password("new-pw", user.password)
        assert not verify_password("old-pw", user.password)


# ---------------------------------------------------------------------------
# Slug + audit stamps + computed_field on Project
# ---------------------------------------------------------------------------


class TestProjectMeta:
    def test_slug_generated_from_name(self, client):
        p = client.post("/projects", json={"name": "My Cool Project"}).json()
        assert p["slug"] == "my-cool-project"

    def test_slug_uniqueness_within_tenant(self, client, new_tenant):
        a = client.post("/projects", json={"name": "Same Name"}).json()
        b = client.post("/projects", json={"name": "Same Name"}).json()
        assert a["slug"] == "same-name"
        assert b["slug"] == "same-name-2"

        # The probe is scoped to the acting organization: another tenant is
        # free to use the same slug.
        beta = new_tenant("beta")
        with beta.acting():
            c = client.post("/projects", json={"name": "Same Name"}).json()
        assert c["slug"] == "same-name"

    async def test_a_script_gets_a_slug_too(self, async_client, async_actor):
        """The slug is the model's business, so a project that a script
        creates gets one, unique in its organization, and a rename moves it."""
        from app.projects.models import Project

        await async_client.post("/projects", json={"name": "Script Made"})
        async with _async_session(async_actor) as session:
            project = Project(name="Script Made")
            session.add(project)
            await session.flush()
            assert project.slug == "script-made-2"

            project.name = "Renamed Here"
            await session.flush()
            assert project.slug == "renamed-here"

    def test_a_patch_keeps_a_sent_slug_and_fills_a_blank_one(self, client):
        """A rename gives a new slug, unless the client sends a slug. A form
        that sends the whole record back keeps the slug of saved links."""
        project = client.post("/projects", json={"name": "Keep Me"}).json()
        url = f"/projects/{project['id']}"
        kept = client.patch(url, json={"name": "Renamed", "slug": "keep-me"}).json()
        assert kept["slug"] == "keep-me"
        followed = client.patch(url, json={"name": "Follows"}).json()
        assert followed["slug"] == "follows"
        blank = client.patch(url, json={"name": "Filled In", "slug": ""}).json()
        assert blank["slug"] == "filled-in"

    def test_audit_stamps_record_the_acting_user(self, client, actor):
        """The column defaults stamp the tenant and the user of the request."""
        p = client.post("/projects", json={"name": "Audit Test"}).json()
        assert p["organization_id"] == actor.org_id
        assert p["created_by_id"] == actor.user_id
        assert p["updated_by_id"] == actor.user_id

    def test_updated_by_follows_the_updater(self, client, actor, auth_context):
        """``onupdate`` restamps ``updated_by_id``; ``created_by_id`` stays."""
        p = client.post("/projects", json={"name": "Handed Over"}).json()
        bob = client.post(
            "/users", json={"email": "bob@acme.test", "name": "Bob", "role": "admin"}
        ).json()

        with auth_context(user_id=bob["id"], role=UserRole.ADMIN):
            updated = client.patch(
                f"/projects/{p['id']}", json={"description": "by Bob"}
            ).json()

        assert updated["created_by_id"] == actor.user_id
        assert updated["updated_by_id"] == bob["id"]

    async def test_stamped_rows_survive_their_user(
        self, async_client, async_actor, auth_context
    ):
        """The stamp columns are ``ON DELETE SET NULL``: a row outlives its user.

        Deleting an organization removes its users and its projects in one
        flush, and no relationship orders the two. Without the rule, a user
        deleted before the project they stamped violates the foreign key.
        """
        bob = (
            await async_client.post(
                "/users", json={"email": "bob@acme.test", "name": "Bob"}
            )
        ).json()
        with auth_context(user_id=bob["id"], role=UserRole.ADMIN):
            project = (
                await async_client.post("/projects", json={"name": "By Bob"})
            ).json()
        assert project["created_by_id"] == bob["id"]

        async with _async_session(async_actor) as session:
            await session.execute(delete(User).where(User.id == bob["id"]))
            await session.commit()

        kept = (await async_client.get(f"/projects/{project['id']}")).json()
        assert kept["created_by_id"] is None
        assert kept["updated_by_id"] is None

    def test_can_edit_decoration_present_on_get(self, client):
        p = client.post("/projects", json={"name": "Decorate Me"}).json()
        got = client.get(f"/projects/{p['id']}").json()
        assert got["can_edit"] is True


# ---------------------------------------------------------------------------
# Outbox events written transactionally
# ---------------------------------------------------------------------------


class TestOutbox:
    async def test_project_create_emits_outbox(self, async_client, async_actor):
        from app.outbox import OutboxEvent

        await async_client.post("/projects", json={"name": "Outboxed"})
        async with _async_session(async_actor) as session:
            events = (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "project.created"
                    )
                )
            ).all()
        assert len(events) == 1
        assert events[0].aggregate_type == "Project"
        assert events[0].payload["name"] == "Outboxed"

    async def test_status_transition_emits_event(self, async_client, async_actor):
        from app.outbox import OutboxEvent

        p = (await async_client.post("/projects", json={"name": "Transitions"})).json()
        await async_client.patch(f"/projects/{p['id']}", json={"status": "archived"})
        async with _async_session(async_actor) as session:
            events = (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "project.status_changed"
                    )
                )
            ).all()
        assert len(events) == 1
        assert events[0].payload == {"from": "active", "to": "archived"}

    async def test_no_event_for_idempotent_update(self, async_client, async_actor):
        """Updating with the same status should NOT emit a transition event."""
        from app.outbox import OutboxEvent

        p = (await async_client.post("/projects", json={"name": "Idempotent"})).json()
        await async_client.patch(f"/projects/{p['id']}", json={"status": "active"})
        async with _async_session(async_actor) as session:
            events = (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "project.status_changed"
                    )
                )
            ).all()
        assert events == []


# ---------------------------------------------------------------------------
# Multipart upload — early-flush-for-PK
# ---------------------------------------------------------------------------


class TestMultipartUpload:
    def test_csv_upload_creates_parent_and_lines(self, client, actor):
        csv_bytes = b"title,amount\nfoo,10\nbar,20\nbaz,30\n"
        response = client.post(
            "/uploads",
            files={"file": ("import.csv", io.BytesIO(csv_bytes), "text/csv")},
        )
        assert response.status_code == 201
        upload = response.json()
        assert upload["filename"] == "import.csv"
        assert upload["line_count"] == 3
        assert upload["completed_at"] is not None
        # The tenant and the uploader are the model's stamps, not form fields.
        assert upload["organization_id"] == actor.org_id
        assert upload["uploaded_by_id"] == actor.user_id

        lines = client.get(f"/uploads/{upload['id']}/lines").json()
        assert len(lines) == 3
        assert {ln["title"] for ln in lines} == {"foo", "bar", "baz"}
        # All lines reference the parent's autogenerated PK.
        assert all(ln["upload_id"] == upload["id"] for ln in lines)

    async def test_upload_emits_completion_event(self, async_client, async_actor):
        from app.outbox import OutboxEvent

        await async_client.post(
            "/uploads", files={"file": ("a.csv", io.BytesIO(b"title\nx\n"), "text/csv")}
        )
        async with _async_session(async_actor) as session:
            events = (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "upload.completed"
                    )
                )
            ).all()
        assert len(events) == 1
        assert events[0].payload == {"line_count": 1}


# ---------------------------------------------------------------------------
# CSV bulk import (Task)
# ---------------------------------------------------------------------------


class TestTaskCSVImport:
    def test_csv_import_per_row_results(self, client):
        project = client.post("/projects", json={"name": "CSV target"}).json()
        csv_bytes = b"title,description\nFirst,Hello\n,Empty title row\nThird,\n"
        response = client.post(
            "/tasks/import-csv",
            data={"project_id": str(project["id"])},
            files={"file": ("tasks.csv", io.BytesIO(csv_bytes), "text/csv")},
        )
        result = response.json()
        assert result["success"] == 2
        assert result["failed"] == 1
        assert any("title is required" in err for err in result["errors"])

    def test_csv_import_runs_before_hook_inside_each_savepoint(
        self, client, monkeypatch
    ):
        from app.tasks.views import TaskView

        async def reject_one(self, action, new, old=None):
            if action == "create" and new.title == "Rejected by hook":
                raise ValueError("rejected by before_action_commit")

        monkeypatch.setattr(TaskView, "before_action_commit", reject_one)
        project = client.post("/projects", json={"name": "Hooked CSV"}).json()
        csv_bytes = b"title,description\nAccepted,Hello\nRejected by hook,Nope\n"

        result = client.post(
            "/tasks/import-csv",
            data={"project_id": str(project["id"])},
            files={"file": ("tasks.csv", io.BytesIO(csv_bytes), "text/csv")},
        ).json()

        assert result["success"] == 1
        assert result["failed"] == 1
        tasks = client.get(f"/tasks?project_id={project['id']}").json()["data"]
        assert [task["title"] for task in tasks] == ["Accepted"]


# ---------------------------------------------------------------------------
# Custom POST with Location header
# ---------------------------------------------------------------------------


class TestLocationHeader:
    def test_location_header_on_org_create(self, client):
        response = client.post(
            "/organizations", json={"name": "With Header", "slug": "with-header"}
        )
        assert response.status_code == 201
        body = response.json()
        assert response.headers["location"] == f"/organizations/{body['id']}"


# ---------------------------------------------------------------------------
# Read-only Country lookup
# ---------------------------------------------------------------------------


class TestReadOnlyLookup:
    async def test_seeded_data_is_listable(self, async_client):
        countries = (await async_client.get("/countries")).json()["data"]
        codes = {c["code"] for c in countries}
        assert {"NL", "DE"} <= codes

    def test_post_is_not_allowed(self, client):
        # The default POST route is excluded, so no route is registered.
        client.post(
            "/countries", json={"code": "FR", "name": "France"}, assert_status_code=405
        )

    def test_patch_is_not_allowed(self, client):
        client.patch("/countries/1", json={"name": "x"}, assert_status_code=405)

    def test_delete_is_not_allowed(self, client):
        client.delete("/countries/1", assert_status_code=405)


# ---------------------------------------------------------------------------
# Story-point rollup on Project (update-related-on-update)
# ---------------------------------------------------------------------------


class TestStoryPointRollup:
    def _setup(self, client):
        project = client.post("/projects", json={"name": "Rollup"}).json()
        return project

    async def test_a_script_keeps_the_total_too(self, async_client, async_actor):
        """The total is the model's business, so a write outside the views
        keeps it: a new task, new points, a move to another project, a soft
        delete, a restore and a hard delete."""
        from app.projects.models import Project
        from app.tasks.models import Task, TaskType

        first = (await async_client.post("/projects", json={"name": "A"})).json()
        second = (await async_client.post("/projects", json={"name": "B"})).json()
        async with _async_session(async_actor) as session:
            a = await session.get(Project, first["id"])
            b = await session.get(Project, second["id"])
            assert a is not None and b is not None
            task = Task(
                title="T", project_id=a.id, task_type=TaskType.FEATURE, story_points=5
            )
            session.add(task)
            await session.flush()
            assert (a.total_story_points, b.total_story_points) == (5, 0)

            task.story_points = 8
            await session.flush()
            assert (a.total_story_points, b.total_story_points) == (8, 0)

            task.project_id = b.id
            await session.flush()
            assert (a.total_story_points, b.total_story_points) == (0, 8)

            task.deleted_at = datetime.now(timezone.utc)
            await session.flush()
            assert b.total_story_points == 0

            task.deleted_at = None
            await session.flush()
            assert b.total_story_points == 8

            await session.delete(task)
            await session.flush()
            assert b.total_story_points == 0

    async def test_a_script_keeps_the_total_through_the_relationship(
        self, async_client, async_actor
    ):
        """A move through the relationship sets the key only during the
        flush, and removing a task from ``Project.tasks`` deletes it."""
        from app.projects.models import Project
        from app.tasks.models import Task, TaskType

        first = (await async_client.post("/projects", json={"name": "A"})).json()
        second = (await async_client.post("/projects", json={"name": "B"})).json()
        async with _async_session(async_actor) as session:
            a = await session.get(Project, first["id"])
            b = await session.get(Project, second["id"])
            assert a is not None and b is not None
            task = Task(title="T", project_id=a.id, task_type=TaskType.FEATURE)
            task.story_points = 5
            session.add(task)
            await session.flush()

            task.project = b
            await session.flush()
            assert (a.total_story_points, b.total_story_points) == (0, 5)

            tasks = await b.awaitable_attrs.tasks
            tasks.remove(task)
            await session.flush()
            assert b.total_story_points == 0

    async def test_a_script_keeps_the_total_when_a_value_was_not_loaded(
        self, async_client, async_actor
    ):
        """SQLAlchemy keeps no old value for an attribute that was not
        loaded when it was set, so the event reads the row."""
        from app.projects.models import Project
        from app.tasks.models import Task, TaskType

        first = (await async_client.post("/projects", json={"name": "A"})).json()
        async with _async_session(async_actor) as session:
            a = await session.get(Project, first["id"])
            assert a is not None
            task = Task(title="T", project_id=a.id, task_type=TaskType.FEATURE)
            task.story_points = 5
            session.add(task)
            await session.flush()

            session.expire(task)
            task.story_points = 8
            await session.flush()
            assert a.total_story_points == 8

            session.expire(task)
            task.deleted_at = datetime.now(timezone.utc)
            await session.flush()
            assert a.total_story_points == 0

    def test_rollup_on_create(self, client):
        project = self._setup(client)
        client.post(
            "/tasks",
            json={
                "title": "T1",
                "project_id": project["id"],
                "task_type": "feature",
                "story_points": 5,
            },
        )
        client.post(
            "/tasks",
            json={
                "title": "T2",
                "project_id": project["id"],
                "task_type": "feature",
                "story_points": 8,
            },
        )
        got = client.get(f"/projects/{project['id']}").json()
        assert got["total_story_points"] == 13

    def test_rollup_on_update(self, client):
        project = self._setup(client)
        task = client.post(
            "/tasks",
            json={
                "title": "T1",
                "project_id": project["id"],
                "task_type": "feature",
                "story_points": 5,
            },
        ).json()
        client.patch(f"/tasks/{task['id']}", json={"story_points": 13})
        got = client.get(f"/projects/{project['id']}").json()
        assert got["total_story_points"] == 13

    def test_rollup_on_delete(self, client):
        project = self._setup(client)
        task = client.post(
            "/tasks",
            json={
                "title": "T1",
                "project_id": project["id"],
                "task_type": "feature",
                "story_points": 7,
            },
        ).json()
        client.delete(f"/tasks/{task['id']}")
        got = client.get(f"/projects/{project['id']}").json()
        assert got["total_story_points"] == 0


# ---------------------------------------------------------------------------
# NOT_SET / exclude_unset semantic
# ---------------------------------------------------------------------------


class TestPartialUpdateSemantic:
    """The matrix's "skip fields in shared schema" pattern.

    An omitted field is preserved; a field sent explicitly as None clears.
    """

    def test_omitted_field_is_preserved(self, client):
        project = client.post("/projects", json={"name": "Partial"}).json()
        task = client.post(
            "/tasks",
            json={
                "title": "Has description",
                "description": "the original",
                "project_id": project["id"],
                "story_points": 3,
            },
        ).json()
        # Only patch story_points — description must NOT change.
        client.patch(f"/tasks/{task['id']}", json={"story_points": 5})
        got = client.get(f"/tasks/{task['id']}").json()
        assert got["description"] == "the original"
        assert got["story_points"] == 5


# ---------------------------------------------------------------------------
# Admin bypass of tenant + row scope
# ---------------------------------------------------------------------------


class TestAdminBypass:
    """The matrix's 'Admin bypass of tenant/row scope' row.

    ``Current.is_admin`` bypasses the session's tenant criteria and the
    ``assigned_to_current_user`` clause. The same routes serve both identities.
    """

    def _two_orgs_with_projects(self, client, new_tenant):
        beta = new_tenant("beta")
        pa = client.post("/projects", json={"name": "A-only"}).json()
        with beta.acting():
            pb = client.post("/projects", json={"name": "B-only"}).json()
        return pa["id"], pb["id"]

    def test_non_admin_sees_only_own_org(self, client, new_tenant):
        pa_id, pb_id = self._two_orgs_with_projects(client, new_tenant)
        ids = {p["id"] for p in client.get("/projects").json()["data"]}
        assert pa_id in ids
        assert pb_id not in ids

    def test_admin_sees_all_orgs(self, client, new_tenant, auth_context):
        """Admin bypass: flipping the ``is_admin`` source shows everything."""
        pa_id, pb_id = self._two_orgs_with_projects(client, new_tenant)

        with auth_context(is_admin=True):  # still acting in Acme
            ids = {p["id"] for p in client.get("/projects").json()["data"]}
        assert pa_id in ids
        assert pb_id in ids  # admin sees the other org despite the current one

    def test_admin_sees_other_users_tasks(self, client, auth_context):
        """TaskView's assignee scope also short-circuits for admin."""
        # Two members, two tasks (one each)
        u1 = client.post(
            "/users", json={"email": "u1@x", "name": "U1", "password": "p"}
        ).json()
        u2 = client.post(
            "/users", json={"email": "u2@x", "name": "U2", "password": "p"}
        ).json()
        proj = client.post("/projects", json={"name": "P"}).json()
        client.post(
            "/tasks",
            json={"title": "T1", "project_id": proj["id"], "assignee_id": u1["id"]},
        )
        client.post(
            "/tasks",
            json={"title": "T2", "project_id": proj["id"], "assignee_id": u2["id"]},
        )

        # As the member u1, only T1 visible
        with auth_context(user_id=u1["id"], role=UserRole.MEMBER):
            titles = {t["title"] for t in client.get("/tasks").json()["data"]}
            assert titles == {"T1"}

            # Now flip admin on while user_id is still u1 — should see both.
            with auth_context(is_admin=True):
                titles = {t["title"] for t in client.get("/tasks").json()["data"]}
                assert titles == {"T1", "T2"}


# ---------------------------------------------------------------------------
# Sibling-creation custom endpoint + IDRef behavior
# ---------------------------------------------------------------------------


class TestSiblingCreation:
    """The brenntag permissions.py:188-201 pattern: create a sibling row,
    then create another row that references it via IDRef."""

    def _task(self, client):
        proj = client.post("/projects", json={"name": "Sib P"}).json()
        return client.post(
            "/tasks", json={"title": "Sib T", "project_id": proj["id"]}
        ).json()

    def test_create_and_attach_creates_both_rows(self, client, actor):
        task = self._task(client)
        response = client.post(
            "/task-labels/create-and-attach",
            json={"task_id": task["id"], "label_name": "urgent", "color": "#ff0000"},
        )
        assert response.status_code == 201
        tl = response.json()
        # IDRef serializes as a scalar id in the JSON.
        assert tl["task_id"] == task["id"]
        assert isinstance(tl["label_id"], int)
        assert tl["added_by_id"] == actor.user_id

        # Both rows exist, in the acting organization.
        label = client.get(f"/labels/{tl['label_id']}").json()
        assert label["name"] == "urgent"
        assert label["color"] == "#ff0000"
        assert label["organization_id"] == actor.org_id

    def test_idref_resolution_for_freshly_created_sibling(self, client):
        """The IDRef resolver requires a flushed PK before construction.

        Without ``await session.flush()`` between the Label insert and the
        TaskLabel build, the IDRef value would be invalid. The route flushes
        manually; this test verifies the happy path works end-to-end."""
        task = self._task(client)
        r1 = client.post(
            "/task-labels/create-and-attach",
            json={"task_id": task["id"], "label_name": "first"},
        )
        r2 = client.post(
            "/task-labels/create-and-attach",
            json={"task_id": task["id"], "label_name": "second"},
        )
        assert r1.status_code == 201
        assert r2.status_code == 201
        # Different label IDs — each request really created its own
        # row, the resolver isn't returning a stale cached one.
        assert r1.json()["label_id"] != r2.json()["label_id"]

    def test_added_by_is_stamped_by_the_column_default(
        self, client, actor, auth_context
    ):
        """The stamp lives on the column, so the free helper gets it too.

        ``create_and_attach`` builds the TaskLabel with the free
        ``async_make_new_object``, which runs no view code at all; the
        ``added_by_id`` insert default still fires from ``Current.user_id``,
        for whoever is acting.
        """
        task = self._task(client)
        response = client.post(
            "/task-labels/create-and-attach",
            json={"task_id": task["id"], "label_name": "by-alice"},
        )
        assert response.json()["added_by_id"] == actor.user_id

        carol = client.post(
            "/users", json={"email": "carol@acme.test", "name": "Carol"}
        ).json()
        # A member labels only a task assigned to them.
        client.patch(f"/tasks/{task['id']}", json={"assignee_id": carol["id"]})
        with auth_context(user_id=carol["id"], role=UserRole.MEMBER):
            response = client.post(
                "/task-labels/create-and-attach",
                json={"task_id": task["id"], "label_name": "by-carol"},
            )
        assert response.json()["added_by_id"] == carol["id"]

    def test_create_and_attach_rejects_cross_tenant_task(self, client, new_tenant):
        """A task in another org must not be attachable from the caller's org.

        The session's tenant criteria apply to the ``task_id`` reference check.
        A foreign task returns 404. The request rolls back both new rows.
        """
        beta = new_tenant("beta")
        with beta.acting():
            task_b = self._task(client)

        # Acting in Acme, try to attach a label to Beta's task.
        client.post(
            "/task-labels/create-and-attach",
            json={"task_id": task_b["id"], "label_name": "sneaky"},
            assert_status_code=404,
        )
        # No Label leaked into Acme from the rejected attach.
        assert client.get("/labels").json()["data"] == []

    def test_create_and_attach_missing_task_returns_404(self, client):
        """A task id that matches no row reads as 404 from the reference check."""
        client.post(
            "/task-labels/create-and-attach",
            json={"task_id": 999_999, "label_name": "ghost"},
            assert_status_code=404,
        )
        assert client.get("/labels").json()["data"] == []

    def test_tenant_routes_require_an_identity(self, client, anonymous):
        """Without an authenticated identity, a tenant route answers 401.

        The context sources refuse the request before any clause or stamp
        runs, so there is no "no org" path to reason about: a write is one
        user's in one organization or it does not happen. The plain views
        (organizations, countries) take no identity.
        """
        task = self._task(client)
        with anonymous():
            client.get("/projects", assert_status_code=401)
            client.post(
                "/task-labels/create-and-attach",
                json={"task_id": task["id"], "label_name": "no-org"},
                assert_status_code=401,
            )
            client.get("/organizations")
            client.get("/countries")
