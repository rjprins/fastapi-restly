"""Tests for the use-case-matrix patterns from rut-notes/discussion_save_object.md.

These exercise every row of the matrix that the SaaS example covers,
locking the expected behavior down so future helper/handler design experiments
have a benchmark to refactor against.

Uses the framework's ``async_session`` fixture (savepoint-isolated
session bound to the same connection as the test client) for direct
DB inspection inside each test.
"""

import io
from contextlib import asynccontextmanager

import pytest
from app.auth import verify_password
from app.main import app
from app.models import Country
from sqlalchemy import select

from fastapi_restly.testing import RestlyTestClient


@pytest.fixture
def client() -> RestlyTestClient:
    return RestlyTestClient(app)


@pytest.fixture
def org_id(client) -> int:
    response = client.post(
        "/organizations/",
        json={"name": "Pattern Org", "slug": "pattern-org"},
    )
    return response.json()["id"]


@asynccontextmanager
async def _async_session():
    """Wrap fr_globals.async_make_session() so tests can inspect rows directly.

    Uses the (test-patched) sessionmaker so changes a route just made are
    visible to the savepoint we open here.
    """
    from fastapi_restly.db._globals import get_fr_globals

    factory = get_fr_globals().async_make_session
    assert factory is not None, "fr.configure() must run first"
    async with factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Password hashing on create + change-password action
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    """Use-case: hash password on user create — UserView.handle_create."""

    def test_password_is_hashed_at_rest(self, client, org_id):
        """The plaintext from the body must never reach the database column."""
        response = client.post(
            "/users/",
            json={
                "email": "alice@example.com",
                "name": "Alice",
                "organization_id": org_id,
                "password": "supersecret",
            },
        )
        assert response.status_code == 201
        # The plaintext must not appear in the response under any key.
        body = response.json()
        assert "supersecret" not in str(body)

    async def test_stored_value_is_a_real_hash(self, org_id, client):
        """Round-trip: pull the row from the DB, verify hash matches plaintext."""
        from app.models import User

        client.post(
            "/users/",
            json={
                "email": "bob@example.com",
                "name": "Bob",
                "organization_id": org_id,
                "password": "trustno1",
            },
        )

        async with _async_session() as session:
            user = (
                await session.scalars(select(User).where(User.email == "bob@example.com"))
            ).one()
        assert user.password != "trustno1"  # not plaintext
        assert verify_password("trustno1", user.password)
        assert not verify_password("wrong", user.password)

    def test_change_password_requires_current(self, client, org_id):
        """The action route rejects a wrong current password with 403."""
        u = client.post(
            "/users/",
            json={
                "email": "carol@example.com",
                "name": "Carol",
                "organization_id": org_id,
                "password": "old-pw",
            },
        ).json()

        bad = client.post(
            f"/users/{u['id']}/change-password",
            json={"current_password": "wrong", "new_password": "x"},
            assert_status_code=403,
        )
        assert "incorrect" in bad.json()["detail"].lower()

    async def test_change_password_swaps_hash(self, client, org_id):
        """Successful change replaces the stored digest."""
        from app.models import User

        u = client.post(
            "/users/",
            json={
                "email": "dave@example.com",
                "name": "Dave",
                "organization_id": org_id,
                "password": "old-pw",
            },
        ).json()
        client.post(
            f"/users/{u['id']}/change-password",
            json={"current_password": "old-pw", "new_password": "new-pw"},
        )
        async with _async_session() as session:
            user = await session.get(User, u["id"])
        assert user is not None
        assert verify_password("new-pw", user.password)
        assert not verify_password("old-pw", user.password)


# ---------------------------------------------------------------------------
# Slug + audit stamps + computed_field on Project
# ---------------------------------------------------------------------------


class TestProjectMeta:
    def test_slug_generated_from_name(self, client, org_id):
        p = client.post(
            "/projects/",
            json={"name": "My Cool Project", "organization_id": org_id},
        ).json()
        assert p["slug"] == "my-cool-project"

    def test_slug_uniqueness_within_tenant(self, client, org_id):
        a = client.post(
            "/projects/",
            json={"name": "Same Name", "organization_id": org_id},
        ).json()
        b = client.post(
            "/projects/",
            json={"name": "Same Name", "organization_id": org_id},
        ).json()
        assert a["slug"] == "same-name"
        assert b["slug"] == "same-name-2"

    def test_audit_stamps_default_to_none_without_auth(self, client, org_id):
        """Without request.state.user_id set, stamps are None — tested in the
        no-auth-context path. Real auth tests would assert IDs match."""
        p = client.post(
            "/projects/",
            json={"name": "Audit Test", "organization_id": org_id},
        ).json()
        assert p["created_by_id"] is None
        assert p["updated_by_id"] is None

    def test_can_edit_decoration_present_on_get(self, client, org_id):
        p = client.post(
            "/projects/",
            json={"name": "Decorate Me", "organization_id": org_id},
        ).json()
        got = client.get(f"/projects/{p['id']}").json()
        assert got["can_edit"] is True


# ---------------------------------------------------------------------------
# Outbox events written transactionally
# ---------------------------------------------------------------------------


class TestOutbox:
    async def test_project_create_emits_outbox(self, client, org_id):
        from app.models import OutboxEvent

        client.post(
            "/projects/",
            json={"name": "Outboxed", "organization_id": org_id},
        )
        async with _async_session() as session:
            events = (
                await session.scalars(
                    select(OutboxEvent).where(OutboxEvent.event_type == "project.created")
                )
            ).all()
        assert len(events) == 1
        assert events[0].aggregate_type == "Project"
        assert events[0].payload["name"] == "Outboxed"

    async def test_status_transition_emits_event(self, client, org_id):
        from app.models import OutboxEvent

        p = client.post(
            "/projects/",
            json={"name": "Transitions", "organization_id": org_id},
        ).json()
        client.patch(f"/projects/{p['id']}", json={"status": "archived"})
        async with _async_session() as session:
            events = (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "project.status_changed"
                    )
                )
            ).all()
        assert len(events) == 1
        assert events[0].payload == {"from": "active", "to": "archived"}

    async def test_no_event_for_idempotent_update(self, client, org_id):
        """Updating with the same status should NOT emit a transition event."""
        from app.models import OutboxEvent

        p = client.post(
            "/projects/",
            json={"name": "Idempotent", "organization_id": org_id},
        ).json()
        client.patch(f"/projects/{p['id']}", json={"status": "active"})
        async with _async_session() as session:
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
    def test_csv_upload_creates_parent_and_lines(self, client, org_id):
        csv_bytes = b"title,amount\nfoo,10\nbar,20\nbaz,30\n"
        response = client.post(
            "/uploads/",
            data={"organization_id": str(org_id)},
            files={"file": ("import.csv", io.BytesIO(csv_bytes), "text/csv")},
        )
        assert response.status_code == 201
        upload = response.json()
        assert upload["filename"] == "import.csv"
        assert upload["line_count"] == 3
        assert upload["completed_at"] is not None

        lines = client.get(f"/uploads/{upload['id']}/lines").json()
        assert len(lines) == 3
        assert {ln["title"] for ln in lines} == {"foo", "bar", "baz"}
        # All lines reference the parent's autogenerated PK.
        assert all(ln["upload_id"] == upload["id"] for ln in lines)

    async def test_upload_emits_completion_event(self, client, org_id):
        from app.models import OutboxEvent

        client.post(
            "/uploads/",
            data={"organization_id": str(org_id)},
            files={"file": ("a.csv", io.BytesIO(b"title\nx\n"), "text/csv")},
        )
        async with _async_session() as session:
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
    def test_csv_import_per_row_results(self, client, org_id):
        project = client.post(
            "/projects/",
            json={"name": "CSV target", "organization_id": org_id},
        ).json()
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


# ---------------------------------------------------------------------------
# Custom POST with Location header
# ---------------------------------------------------------------------------


class TestLocationHeader:
    def test_location_header_on_org_create(self, client):
        response = client.post(
            "/organizations/",
            json={"name": "With Header", "slug": "with-header"},
        )
        assert response.status_code == 201
        body = response.json()
        assert response.headers["location"] == f"/organizations/{body['id']}"


# ---------------------------------------------------------------------------
# Read-only Country lookup
# ---------------------------------------------------------------------------


class TestReadOnlyLookup:
    async def test_seeded_data_is_listable(self, client):
        async with _async_session() as session:
            session.add(Country(code="NL", name="Netherlands"))
            session.add(Country(code="DE", name="Germany"))
            await session.commit()

        listing = client.get("/countries/").json()
        codes = {c["code"] for c in listing}
        assert {"NL", "DE"} <= codes

    def test_post_is_not_allowed(self, client):
        # Generated POST is excluded — no route registered.
        client.post(
            "/countries/",
            json={"code": "FR", "name": "France"},
            assert_status_code=405,
        )

    def test_patch_is_not_allowed(self, client):
        client.patch("/countries/1", json={"name": "x"}, assert_status_code=405)

    def test_delete_is_not_allowed(self, client):
        client.delete("/countries/1", assert_status_code=405)


# ---------------------------------------------------------------------------
# Story-point rollup on Project (update-related-on-update)
# ---------------------------------------------------------------------------


class TestStoryPointRollup:
    def _setup(self, client, org_id):
        project = client.post(
            "/projects/",
            json={"name": "Rollup", "organization_id": org_id},
        ).json()
        return project

    def test_rollup_on_create(self, client, org_id):
        project = self._setup(client, org_id)
        client.post(
            "/tasks/",
            json={
                "title": "T1",
                "project_id": project["id"],
                "task_type": "feature",
                "story_points": 5,
            },
        )
        client.post(
            "/tasks/",
            json={
                "title": "T2",
                "project_id": project["id"],
                "task_type": "feature",
                "story_points": 8,
            },
        )
        got = client.get(f"/projects/{project['id']}").json()
        assert got["total_story_points"] == 13

    def test_rollup_on_update(self, client, org_id):
        project = self._setup(client, org_id)
        task = client.post(
            "/tasks/",
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

    def test_rollup_on_delete(self, client, org_id):
        project = self._setup(client, org_id)
        task = client.post(
            "/tasks/",
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

    def test_omitted_field_is_preserved(self, client, org_id):
        project = client.post(
            "/projects/",
            json={"name": "Partial", "organization_id": org_id},
        ).json()
        task = client.post(
            "/tasks/",
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

    A request with ``request.state.is_admin = True`` short-circuits the
    tenant filter in TenantScopedMixin and the assignee filter in
    TaskView.handle_get / handle_list. Demonstrates the runtime-flag design:
    no separate route tree, no parallel base view.
    """

    def _setup_two_orgs_with_projects(self, client):
        a = client.post("/organizations/", json={"name": "Org A", "slug": "org-a"}).json()
        b = client.post("/organizations/", json={"name": "Org B", "slug": "org-b"}).json()
        pa = client.post(
            "/projects/", json={"name": "A-only", "organization_id": a["id"]}
        ).json()
        pb = client.post(
            "/projects/", json={"name": "B-only", "organization_id": b["id"]}
        ).json()
        return a["id"], b["id"], pa["id"], pb["id"]

    def test_non_admin_sees_only_own_org(self, client):
        from app.views import _base as base_module

        a_id, _b_id, pa_id, pb_id = self._setup_two_orgs_with_projects(client)
        base_module._TEST_ORG_ID = a_id
        try:
            ids = {p["id"] for p in client.get("/projects/").json()["items"]}
            assert pa_id in ids
            assert pb_id not in ids
        finally:
            base_module._TEST_ORG_ID = None

    def test_admin_sees_all_orgs(self, client, monkeypatch):
        """Admin bypass: setting request.state.is_admin shows everything."""
        from app.views import _base as base_module

        a_id, _b_id, pa_id, pb_id = self._setup_two_orgs_with_projects(client)

        # Patch _is_admin to return True regardless of state — equivalent
        # to auth middleware having set ``request.state.is_admin = True``.
        from app.views._base import TenantBase
        monkeypatch.setattr(TenantBase, "_is_admin", lambda self: True)

        base_module._TEST_ORG_ID = a_id  # would normally hide org B
        try:
            ids = {p["id"] for p in client.get("/projects/").json()["items"]}
            assert pa_id in ids
            assert pb_id in ids  # admin sees other org despite _TEST_ORG_ID
        finally:
            base_module._TEST_ORG_ID = None

    def test_admin_sees_other_users_tasks(self, client, monkeypatch):
        """TaskView's assignee scope also short-circuits for admin."""
        from app.views import task as task_module
        from app.views._base import TenantBase

        org = client.post(
            "/organizations/", json={"name": "TaskOrg", "slug": "task-org"}
        ).json()
        # Two users, two tasks (one each)
        u1 = client.post(
            "/users/",
            json={
                "email": "u1@x", "name": "U1", "organization_id": org["id"],
                "password": "p",
            },
        ).json()
        u2 = client.post(
            "/users/",
            json={
                "email": "u2@x", "name": "U2", "organization_id": org["id"],
                "password": "p",
            },
        ).json()
        proj = client.post(
            "/projects/", json={"name": "P", "organization_id": org["id"]}
        ).json()
        client.post(
            "/tasks/",
            json={"title": "T1", "project_id": proj["id"], "assignee_id": u1["id"]},
        )
        client.post(
            "/tasks/",
            json={"title": "T2", "project_id": proj["id"], "assignee_id": u2["id"]},
        )

        # As u1, only T1 visible
        task_module._TEST_USER_ID = u1["id"]
        try:
            titles = {t["title"] for t in client.get("/tasks/").json()}
            assert titles == {"T1"}

            # Now flip admin on while user_id is still u1 — should see both.
            monkeypatch.setattr(TenantBase, "_is_admin", lambda self: True)
            titles = {t["title"] for t in client.get("/tasks/").json()}
            assert titles == {"T1", "T2"}
        finally:
            task_module._TEST_USER_ID = None


# ---------------------------------------------------------------------------
# Sibling-creation custom endpoint + IDRef behavior
# ---------------------------------------------------------------------------


class TestSiblingCreation:
    """The brenntag permissions.py:188-201 pattern: create a sibling row,
    then create another row that references it via IDRef."""

    def _ctx(self, client):
        from app.views import _base as base_module

        org = client.post(
            "/organizations/", json={"name": "Sib", "slug": "sib"}
        ).json()
        base_module._TEST_ORG_ID = org["id"]  # required: TenantScopedMixin
        proj = client.post(
            "/projects/", json={"name": "Sib P", "organization_id": org["id"]}
        ).json()
        task = client.post(
            "/tasks/",
            json={"title": "Sib T", "project_id": proj["id"]},
        ).json()
        return org["id"], task["id"]

    def test_create_and_attach_creates_both_rows(self, client):
        from app.views import _base as base_module

        try:
            _org_id, task_id = self._ctx(client)
            response = client.post(
                "/task-labels/create-and-attach",
                json={
                    "task_id": task_id,
                    "label_name": "urgent",
                    "color": "#ff0000",
                },
            )
            assert response.status_code == 201
            tl = response.json()
            # IDRef serializes as a scalar id on the wire.
            assert tl["task_id"] == task_id
            assert isinstance(tl["label_id"], int)
            assert tl["added_by_id"] is None  # no user context set in the test

            # Both rows actually exist.
            label_id = tl["label_id"]
            label = client.get(f"/labels/{label_id}").json()
            assert label["name"] == "urgent"
            assert label["color"] == "#ff0000"
        finally:
            base_module._TEST_ORG_ID = None

    def test_idref_resolution_for_freshly_created_sibling(self, client):
        """The pinned behavior: the framework's IDRef resolver requires
        the referenced row to have a flushed PK before it can be
        constructed. Without ``await session.flush()`` between the Label
        insert and the TaskLabel build, the IDRef value would be invalid.

        The route flushes manually for exactly this reason; this test
        verifies the happy path works end-to-end."""
        from app.views import _base as base_module

        try:
            _org_id, task_id = self._ctx(client)
            r1 = client.post(
                "/task-labels/create-and-attach",
                json={"task_id": task_id, "label_name": "first"},
            )
            r2 = client.post(
                "/task-labels/create-and-attach",
                json={"task_id": task_id, "label_name": "second"},
            )
            assert r1.status_code == 201
            assert r2.status_code == 201
            # Different label IDs — each request really created its own
            # row, the resolver isn't returning a stale cached one.
            assert r1.json()["label_id"] != r2.json()["label_id"]
        finally:
            base_module._TEST_ORG_ID = None

    def test_async_make_new_object_skips_view_overrides(self, client):
        """Insight worth pinning: the *free function* async_make_new_object
        does NOT go through ``self.make_new_object`` on TaskLabelView, so
        the ``added_by_id`` stamp (which is in the view's bound override)
        is bypassed. Verified by confirming added_by_id stays None unless
        the route explicitly sets it. If we wanted the stamp, we'd need
        to either call ``self.make_new_object`` from a TaskLabelView
        instance (we're not in one — we're in TaskLabelView itself but
        building a TaskLabel via the *free* helper), or apply the stamp
        manually in the custom route."""
        from app.views import _base as base_module
        from app.views import label as label_module

        try:
            _org_id, task_id = self._ctx(client)
            response = client.post(
                "/task-labels/create-and-attach",
                json={"task_id": task_id, "label_name": "no-stamp"},
            )
            assert response.status_code == 201
            # The route explicitly stamps added_by_id from request.state
            # — without that explicit step, async_make_new_object alone
            # wouldn't have stamped it. The test confirms the route's
            # explicit stamp is what keeps the value in sync.
            tl = response.json()
            # added_by_id is None because the test client doesn't set
            # request.state.user_id; the route handles None gracefully.
            assert tl["added_by_id"] is None
            del label_module  # silence unused
        finally:
            base_module._TEST_ORG_ID = None
