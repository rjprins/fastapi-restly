"""How many SQL statements the project routes run.

The computed project fields (``task_count``, ``completed_task_count``) load
with the row, so a list of N projects does not run 2 more queries per row.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

_SQL = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)


@contextmanager
def _statements() -> Iterator[list[str]]:
    """The SELECT, INSERT, UPDATE and DELETE statements that run in the block."""
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if _SQL.match(statement):
            seen.append(" ".join(statement.split()))

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", record)


def _projects_with_tasks(client, n: int) -> list[dict]:
    projects = []
    for i in range(n):
        project = client.post("/projects", json={"name": f"Counted {i}"}).json()
        for status in ("todo", "done"):
            client.post(
                "/tasks",
                json={"title": "t", "status": status, "project_id": project["id"]},
            )
        projects.append(project)
    return projects


@pytest.mark.parametrize("n", [1, 5])
def test_the_project_list_runs_the_same_statements_for_any_page_size(client, n):
    _projects_with_tasks(client, n)

    with _statements() as seen:
        page = client.get("/projects").json()

    assert len(page["data"]) == n
    assert {p["task_count"] for p in page["data"]} == {2}
    assert {p["completed_task_count"] for p in page["data"]} == {1}
    assert {p["can_edit"] for p in page["data"]} == {True}
    # the count and the page: the task counts load with each project
    assert len(seen) == 2, seen
    # and the count does not compute them for every project of the list
    assert "FROM task" not in seen[0]


def test_the_item_routes_count_their_statements(client):
    (project,) = _projects_with_tasks(client, 1)
    url = f"/projects/{project['id']}"

    with _statements() as got:
        one = client.get(url).json()
    with _statements() as patched:
        changed = client.patch(url, json={"description": "new"}).json()
    with _statements() as posted:
        new = client.post("/projects", json={"name": "Fresh"}).json()

    assert one["task_count"] == 2 and one["can_edit"] is True
    assert changed["task_count"] == 2 and changed["can_edit"] is True
    assert new["task_count"] == 0 and new["can_edit"] is True
    assert len(got) == 1, got
    # the load, the UPDATE and the refresh after it
    assert len(patched) == 3, patched
    # the slug lookup, the INSERT, the refresh and the outbox row
    assert len(posted) == 4, posted
