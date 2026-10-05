"""Nested resources, as docs/howto_nested.md builds them.

A path parameter in the prefix, a lookup dependency that binds the parent id
to a context member, and a view scope that reads it. Sync and async, one level
(/projects/{project_id}/tasks) and two (/companies/.../projects/.../tasks).
"""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Annotated, Any

import pytest
import sqlalchemy as sa
from fastapi import Depends, FastAPI
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient


@dataclass
class _Nested:
    client: RestlyTestClient
    lookups: list[str] = field(default_factory=list)


def _seed(request: pytest.FixtureRequest, sync: bool, rows: list[Any]) -> None:
    if sync:
        engine, make_session = request.getfixturevalue("sync_db")
        fr.DataclassBase.metadata.create_all(engine)
        with make_session() as session:
            for row in rows:
                session.add(row)
                session.flush()
            session.commit()
        return

    async def seed() -> None:
        await fr.db.async_create_all(fr.DataclassBase)
        async with fr.open_async_session() as session:
            for row in rows:
                session.add(row)
                await session.flush()
            await session.commit()

    asyncio.run(seed())


# --- One level -------------------------------------------------------------


@pytest.fixture(params=["sync", "async"])
def projects(request: pytest.FixtureRequest) -> Iterator[_Nested]:
    sync = request.param == "sync"
    base = fr.RestView if sync else fr.AsyncRestView
    lookups: list[str] = []

    class Project(fr.IDBase):
        name: Mapped[str]
        archived: Mapped[bool] = mapped_column(default=False)

    class Task(fr.IDBase):
        title: Mapped[str]
        project_id: Mapped[int] = mapped_column(sa.ForeignKey("project.id"), init=False)

    class ProjectRead(fr.IDSchema):
        name: str

    class TaskRead(fr.IDSchema):
        title: str
        project_id: fr.ReadOnly[int]

    class Parent(fr.ContextNamespace):
        project_id: fr.ContextParam[int]

    app = FastAPI()

    @fr.include_view(app)
    class ProjectView(base):  # type: ignore[misc,valid-type]
        prefix = "/projects"
        model = Project
        schema = ProjectRead
        scope = fr.where_clause(Project.archived.is_(False))

    def project_query(project_id: int) -> sa.Select[Any]:
        return fr.apply_clauses(
            sa.select(Project.id).where(Project.id == project_id),
            fr.resolve_scope(ProjectView),
        )

    def checked(found: int | None, project_id: int) -> int:
        lookups.append("project")
        if found is None:
            raise fr.exc.NotFound(f"Project with id {project_id} was not found")
        return found

    if sync:

        def project_from_path(project_id: int, session: fr.SessionDep) -> int:
            return checked(session.scalar(project_query(project_id)), project_id)

    else:

        async def project_from_path(  # type: ignore[misc]
            project_id: int, session: fr.AsyncSessionDep
        ) -> int:
            found = await session.scalar(project_query(project_id))
            return checked(found, project_id)

    class ProjectTaskView(base):  # type: ignore[misc,valid-type]
        prefix = "/projects/{project_id}/tasks"
        model = Task
        schema = TaskRead
        dependencies = [Parent.depends(project_id=project_from_path)]
        scope = fr.all_of(
            fr.resolve_scope(Task),
            fr.where_clause(Task.project_id == Parent.project_id),
        )

        if sync:

            def create(self, schema_obj):
                task = self.make_new_object(schema_obj)
                task.project_id = Parent.project_id()
                return self.save_object(task)

        else:

            async def create(self, schema_obj):  # type: ignore[misc]
                task = await self.make_new_object(schema_obj)
                task.project_id = Parent.project_id()
                return await self.save_object(task)

    fr.include_view(app, ProjectTaskView)

    rows: list[Any] = [
        Project(name="one"),
        Project(name="two"),
        Project(name="old", archived=True),
    ]
    _seed(request, sync, rows)
    client = RestlyTestClient(app)
    for project_id, title in ((1, "alpha"), (1, "beta"), (1, "gamma"), (2, "delta")):
        client.post(f"/projects/{project_id}/tasks", json={"title": title})
    lookups.clear()
    yield _Nested(client=client, lookups=lookups)


def test_list_shows_the_parents_rows_only(projects):
    payload = projects.client.get("/projects/1/tasks").json()

    assert [row["title"] for row in payload["data"]] == ["alpha", "beta", "gamma"]
    assert payload["total_count"] == 3
    assert {row["project_id"] for row in payload["data"]} == {1}


def test_list_keeps_paging_filters_and_sort(projects):
    payload = projects.client.get(
        "/projects/1/tasks",
        params={"title__ne": "beta", "sort": "-title", "page_size": 1, "page": 2},
    ).json()

    assert [row["title"] for row in payload["data"]] == ["alpha"]
    assert payload["total_count"] == 2


def test_get_one_finds_the_parents_row(projects):
    task = projects.client.get("/projects/1/tasks/1").json()

    assert task == {"id": 1, "title": "alpha", "project_id": 1}


def test_another_parents_row_is_not_found(projects):
    client = projects.client
    # Task 4 is in project 2.
    client.get("/projects/1/tasks/4", assert_status_code=404)
    client.patch("/projects/1/tasks/4", json={"title": "x"}, assert_status_code=404)
    client.delete("/projects/1/tasks/4", assert_status_code=404)

    assert client.get("/projects/2/tasks/4").json()["title"] == "delta"


def test_create_takes_the_parent_from_the_path(projects):
    created = projects.client.post(
        "/projects/2/tasks",
        json={"title": "new", "project_id": 1},
        assert_status_code=201,
    ).json()

    assert created["project_id"] == 2
    assert projects.client.get("/projects/2/tasks").json()["total_count"] == 2


def test_update_cannot_move_a_row_to_another_parent(projects):
    updated = projects.client.patch(
        "/projects/1/tasks/1",
        json={"title": "renamed", "project_id": 2},
        assert_status_code=200,
    ).json()

    assert updated == {"id": 1, "title": "renamed", "project_id": 1}


def test_delete_removes_the_parents_row(projects):
    projects.client.delete("/projects/1/tasks/1", assert_status_code=204)

    assert projects.client.get("/projects/1/tasks").json()["total_count"] == 2


@pytest.mark.parametrize("project_id", [99, 3], ids=["unknown", "hidden"])
def test_unknown_or_hidden_parent_is_not_found(projects, project_id):
    client = projects.client
    url = f"/projects/{project_id}/tasks"

    response = client.get(url, assert_status_code=404)
    assert response.json() == {"detail": f"Project with id {project_id} was not found"}
    client.get(f"{url}/1", assert_status_code=404)
    client.post(url, json={"title": "sneaky"}, assert_status_code=404)

    titles = [row["title"] for row in client.get("/projects/1/tasks").json()["data"]]
    assert "sneaky" not in titles


def test_parent_id_must_be_an_integer(projects):
    detail = projects.client.get("/projects/x/tasks", assert_status_code=422).json()

    assert detail["detail"][0]["loc"] == ["path", "project_id"]


def test_lookup_runs_once_per_request(projects):
    projects.client.get("/projects/1/tasks/1")

    assert projects.lookups == ["project"]


def test_openapi_lists_the_parent_on_every_route(projects):
    spec = projects.client.get("/openapi.json").json()

    for path in ("/projects/{project_id}/tasks", "/projects/{project_id}/tasks/{id}"):
        for operation in spec["paths"][path].values():
            parameters = {
                (parameter["name"], parameter["in"])
                for parameter in operation["parameters"]
            }
            assert ("project_id", "path") in parameters


def test_write_schemas_leave_the_parent_out(projects):
    spec = projects.client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]

    for method, path in (
        ("post", "/projects/{project_id}/tasks"),
        ("patch", "/projects/{project_id}/tasks/{id}"),
    ):
        body = spec["paths"][path][method]["requestBody"]["content"]
        name = body["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
        assert "project_id" not in schemas[name]["properties"]


# --- Two levels ------------------------------------------------------------


@pytest.fixture(params=["sync", "async"])
def companies(request: pytest.FixtureRequest) -> Iterator[_Nested]:
    sync = request.param == "sync"
    base = fr.RestView if sync else fr.AsyncRestView
    lookups: list[str] = []

    class Company(fr.IDBase):
        name: Mapped[str]

    class Project(fr.IDBase):
        name: Mapped[str]
        company_id: Mapped[int] = mapped_column(sa.ForeignKey("company.id"), init=False)

    class Task(fr.IDBase):
        title: Mapped[str]
        project_id: Mapped[int] = mapped_column(sa.ForeignKey("project.id"), init=False)

    class TaskRead(fr.IDSchema):
        title: str
        project_id: fr.ReadOnly[int]

    class Parent(fr.ContextNamespace):
        company_id: fr.ContextParam[int]
        project_id: fr.ContextParam[int]

    def company_query(company_id: int) -> sa.Select[Any]:
        return sa.select(Company.id).where(Company.id == company_id)

    def project_query(project_id: int, company_id: int) -> sa.Select[Any]:
        return sa.select(Project.id).where(
            Project.id == project_id, Project.company_id == company_id
        )

    def checked(name: str, found: int | None, value: int) -> int:
        lookups.append(name)
        if found is None:
            raise fr.exc.NotFound(f"{name} with id {value} was not found")
        return found

    if sync:

        def company_from_path(company_id: int, session: fr.SessionDep) -> int:
            found = session.scalar(company_query(company_id))
            return checked("Company", found, company_id)

        def project_from_path(
            project_id: int,
            company_id: Annotated[int, Depends(company_from_path)],
            session: fr.SessionDep,
        ) -> int:
            found = session.scalar(project_query(project_id, company_id))
            return checked("Project", found, project_id)

    else:

        async def company_from_path(  # type: ignore[misc]
            company_id: int, session: fr.AsyncSessionDep
        ) -> int:
            found = await session.scalar(company_query(company_id))
            return checked("Company", found, company_id)

        async def project_from_path(  # type: ignore[misc]
            project_id: int,
            company_id: Annotated[int, Depends(company_from_path)],
            session: fr.AsyncSessionDep,
        ) -> int:
            found = await session.scalar(project_query(project_id, company_id))
            return checked("Project", found, project_id)

    class CompanyScoped(base):  # type: ignore[misc,valid-type]
        prefix = "/companies/{company_id}"
        dependencies = [Parent.depends(company_id=company_from_path)]

    class ProjectScoped(CompanyScoped):
        prefix = "/projects/{project_id}"
        dependencies = [Parent.depends(project_id=project_from_path)]

    class TaskView(ProjectScoped):
        prefix = "/tasks"
        model = Task
        schema = TaskRead
        scope = fr.all_of(
            fr.resolve_scope(Task),
            fr.where_clause(Task.project_id == Parent.project_id),
        )

        if sync:

            def create(self, schema_obj):
                task = self.make_new_object(schema_obj)
                task.project_id = Parent.project_id()
                return self.save_object(task)

        else:

            async def create(self, schema_obj):  # type: ignore[misc]
                task = await self.make_new_object(schema_obj)
                task.project_id = Parent.project_id()
                return await self.save_object(task)

    app = FastAPI()
    fr.include_view(app, TaskView)

    acme, other = Company(name="acme"), Company(name="other")
    first, second = Project(name="first"), Project(name="second")
    first.company_id, second.company_id = 1, 2
    _seed(request, sync, [acme, other, first, second])
    client = RestlyTestClient(app)
    client.post("/companies/1/projects/1/tasks", json={"title": "a"})
    client.post("/companies/2/projects/2/tasks", json={"title": "b"})
    lookups.clear()
    yield _Nested(client=client, lookups=lookups)


def test_two_levels_list_and_create(companies):
    client = companies.client
    created = client.post(
        "/companies/1/projects/1/tasks", json={"title": "c"}, assert_status_code=201
    ).json()

    assert created["project_id"] == 1
    payload = client.get("/companies/1/projects/1/tasks").json()
    assert [row["title"] for row in payload["data"]] == ["a", "c"]


def test_two_levels_run_each_lookup_once(companies):
    companies.client.get("/companies/1/projects/1/tasks")

    assert companies.lookups == ["Company", "Project"]


def test_project_of_another_company_is_not_found(companies):
    response = companies.client.get(
        "/companies/1/projects/2/tasks", assert_status_code=404
    )

    assert response.json() == {"detail": "Project with id 2 was not found"}


def test_unknown_company_stops_before_the_project_lookup(companies):
    response = companies.client.get(
        "/companies/9/projects/1/tasks", assert_status_code=404
    )

    assert response.json() == {"detail": "Company with id 9 was not found"}
    assert companies.lookups == ["Company"]


@pytest.mark.parametrize(
    ("url", "segment"),
    [
        ("/companies/x/projects/1/tasks", "company_id"),
        ("/companies/1/projects/x/tasks", "project_id"),
    ],
)
def test_two_levels_validate_both_segments(companies, url, segment):
    detail = companies.client.get(url, assert_status_code=422).json()["detail"]

    assert ["path", segment] in [error["loc"] for error in detail]


def test_two_levels_openapi_lists_both_segments(companies):
    spec = companies.client.get("/openapi.json").json()
    path = "/companies/{company_id}/projects/{project_id}/tasks/{id}"

    for operation in spec["paths"][path].values():
        names = [parameter["name"] for parameter in operation["parameters"]]
        assert {"company_id", "project_id", "id"} <= set(names)
