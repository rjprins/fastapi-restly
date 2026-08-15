"""Tests for the tasks API.

``restly_client`` is a fixture Restly ships. Each test runs inside a transaction
that is rolled back afterwards, so no test sees another's rows. The client also
asserts the status code for you: ``get`` expects 200, ``post`` 201, ``delete``
204. Pass ``assert_status_code=`` to expect something else.
"""

from fastapi_restly.testing import RestlyTestClient


def test_create_and_list(restly_client: RestlyTestClient) -> None:
    created = restly_client.post("/tasks", json={"title": "Write the API"}).json()

    assert created["title"] == "Write the API"
    assert created["done"] is False
    assert created["completed_at"] is None

    # A list response is an envelope: the rows are under "data", beside the
    # pagination counts.
    listing = restly_client.get("/tasks").json()

    assert [task["id"] for task in listing["data"]] == [created["id"]]
    assert listing["total_count"] == 1


def test_get_one(restly_client: RestlyTestClient) -> None:
    created = restly_client.post("/tasks", json={"title": "Read the docs"}).json()

    assert restly_client.get(f"/tasks/{created['id']}").json() == created


def test_update(restly_client: RestlyTestClient) -> None:
    created = restly_client.post("/tasks", json={"title": "Draft"}).json()

    updated = restly_client.patch(
        f"/tasks/{created['id']}", json={"title": "Final"}
    ).json()

    assert updated["title"] == "Final"


def test_delete(restly_client: RestlyTestClient) -> None:
    created = restly_client.post("/tasks", json={"title": "Temporary"}).json()

    restly_client.delete(f"/tasks/{created['id']}")

    restly_client.get(f"/tasks/{created['id']}", assert_status_code=404)


def test_completed_at_is_read_only(restly_client: RestlyTestClient) -> None:
    """``fr.ReadOnly`` keeps a server-owned field out of request bodies."""
    created = restly_client.post(
        "/tasks",
        json={"title": "Not mine to set", "completed_at": "2020-01-01T00:00:00"},
    ).json()

    assert created["completed_at"] is None


def test_create_marked_done_stamps_completed_at(
    restly_client: RestlyTestClient,
) -> None:
    """The business-method override in ``TaskView.create``."""
    created = restly_client.post(
        "/tasks", json={"title": "Already finished", "done": True}
    ).json()

    assert created["done"] is True
    assert created["completed_at"] is not None


def test_complete_route(restly_client: RestlyTestClient) -> None:
    """The custom route bracketed by ``write_action``."""
    created = restly_client.post("/tasks", json={"title": "Ship it"}).json()

    completed = restly_client.post(f"/tasks/{created['id']}/complete").json()

    assert completed["done"] is True
    assert completed["completed_at"] is not None

    # The bracket committed, so a fresh read sees the change.
    assert restly_client.get(f"/tasks/{created['id']}").json()["done"] is True


def test_health(restly_client: RestlyTestClient) -> None:
    """The endpoint ``fr.configure(health=...)`` mounts."""
    assert restly_client.get("/health").json() == {"status": "ok"}
