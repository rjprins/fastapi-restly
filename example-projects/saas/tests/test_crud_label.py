"""CRUD tests for Label and the TaskLabel association."""

from dataclasses import dataclass

import pytest
from app.users.roles import UserRole


@dataclass
class MemberRows:
    member: int
    assigned_task: int
    other_task: int
    label: int
    other_link: int


@pytest.fixture
def member_rows(client) -> MemberRows:
    """A member with one assigned task, and a labelled task that is not theirs."""
    member = client.post(
        "/users", json={"email": "member@acme.test", "name": "Member"}
    ).json()
    project = client.post("/projects", json={"name": "Policy"}).json()
    assigned = client.post(
        "/tasks",
        json={
            "title": "Assigned",
            "project_id": project["id"],
            "assignee_id": member["id"],
        },
    ).json()
    other = client.post(
        "/tasks", json={"title": "Other", "project_id": project["id"]}
    ).json()
    label = client.post("/labels", json={"name": "policy"}).json()
    link = client.post(
        "/task-labels", json={"task_id": other["id"], "label_id": label["id"]}
    ).json()
    return MemberRows(
        member["id"], assigned["id"], other["id"], label["id"], link["id"]
    )


class TestMemberLabelPolicy:
    """A member labels only a task assigned to them, and reads every link."""

    @pytest.fixture(autouse=True)
    def as_member(self, member_rows, auth_context):
        with auth_context(user_id=member_rows.member, role=UserRole.MEMBER):
            yield

    def test_member_attaches_to_an_assigned_task(self, client, member_rows):
        link = client.post(
            "/task-labels",
            json={"task_id": member_rows.assigned_task, "label_id": member_rows.label},
        ).json()
        assert link["added_by_id"] == member_rows.member
        client.patch(f"/task-labels/{link['id']}", json={})
        client.delete(f"/task-labels/{link['id']}")

    def test_member_cannot_attach_to_another_task(self, client, member_rows):
        client.get(f"/tasks/{member_rows.other_task}", assert_status_code=404)
        client.post(
            "/task-labels",
            json={"task_id": member_rows.other_task, "label_id": member_rows.label},
            assert_status_code=404,
        )

    def test_member_cannot_create_and_attach_to_another_task(self, client, member_rows):
        client.post(
            "/task-labels/create-and-attach",
            json={"task_id": member_rows.other_task, "label_name": "rolled-back"},
            assert_status_code=404,
        )
        names = {label["name"] for label in client.get("/labels").json()["data"]}
        assert "rolled-back" not in names

    def test_member_cannot_move_a_link_to_another_task(self, client, member_rows):
        link = client.post(
            "/task-labels",
            json={"task_id": member_rows.assigned_task, "label_id": member_rows.label},
        ).json()
        client.patch(
            f"/task-labels/{link['id']}",
            json={"task_id": member_rows.other_task},
            assert_status_code=404,
        )

    def test_member_reads_links_of_another_task(self, client, member_rows):
        ids = {link["id"] for link in client.get("/task-labels").json()["data"]}
        assert member_rows.other_link in ids
        link = client.get(f"/task-labels/{member_rows.other_link}").json()
        assert link["task_id"] == member_rows.other_task

    @pytest.mark.parametrize("method", ["patch", "delete"])
    def test_member_cannot_change_links_of_another_task(
        self, client, member_rows, method
    ):
        getattr(client, method)(
            f"/task-labels/{member_rows.other_link}",
            **({"json": {}} if method == "patch" else {}),
            assert_status_code=403,
        )
        client.get(f"/task-labels/{member_rows.other_link}")

    @pytest.mark.parametrize("role", [UserRole.OWNER, UserRole.ADMIN])
    def test_other_roles_change_links_of_any_task(
        self, client, member_rows, auth_context, role
    ):
        with auth_context(role=role):
            client.patch(f"/task-labels/{member_rows.other_link}", json={})
            client.delete(f"/task-labels/{member_rows.other_link}")


class TestLabelCRUD:
    """Test Label and TaskLabel CRUD operations."""

    def test_create_label(self, client, actor):
        """Test creating a label; it lands in the acting organization."""
        response = client.post("/labels", json={"name": "urgent", "color": "#ff0000"})
        label = response.json()

        assert label["name"] == "urgent"
        assert label["color"] == "#ff0000"
        assert label["organization_id"] == actor.org_id

    def test_add_label_to_task(self, client, actor):
        """Test adding a label to a task via TaskLabel."""
        # Create project, task, and label
        response = client.post("/projects", json={"name": "Label Project"})
        project_id = response.json()["id"]

        response = client.post(
            "/tasks", json={"title": "Labeled Task", "project_id": project_id}
        )
        task_id = response.json()["id"]

        response = client.post("/labels", json={"name": "bug", "color": "#ff0000"})
        label_id = response.json()["id"]

        # Add label to task using IDRef[T] scalar wire format. The framework
        # still validates the referenced rows exist and resolves them to FK
        # values automatically. ``added_by_id`` is the column's stamp.
        response = client.post(
            "/task-labels", json={"task_id": task_id, "label_id": label_id}
        )
        task_label = response.json()

        assert task_label["task_id"] == task_id
        assert task_label["label_id"] == label_id
        assert task_label["added_by_id"] == actor.user_id
