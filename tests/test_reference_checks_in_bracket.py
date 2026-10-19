"""The write bracket checks every reference in the data of a write.

The check runs after ``authorize`` and before the business method, so a
``create`` or ``update`` that builds the object itself cannot skip it. The
references are the ``MustExist`` fields and the ``IDRef`` / ``IDSchema``
fields, in the data itself and in the schemas nested in it.

These tests pin: a create of your own is still checked; the order of the
errors (the row's 404, then 403, then a reference 404); ``write_action(data=...)``
with a schema object, a list of them, and a schema of another model; nested
schemas; the CSV import shape (``handle_create`` for each row); one query for
each referenced model; a reference that the business method changes is
checked again; and the record of the checks ends with the bracket.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import Body, FastAPI
from pydantic import BaseModel
from sqlalchemy import Engine, ForeignKey, String, event, select
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.db._globals import _fr_globals
from fastapi_restly.exc import NotFound, RestlyConfigurationError
from fastapi_restly.schemas._base import _CHECKED_REFERENCES_KEY, _check_body_references
from fastapi_restly.testing import RestlyTestClient

from ._models import SharedBase
from .conftest import create_tables


@dataclass
class _Models:
    Project: Any
    Owner: Any
    Task: Any
    Label: Any
    TaskSchema: Any
    TaskIn: Any


def _models() -> _Models:
    class Project(fr.IDMixin, SharedBase):
        name: Mapped[str]
        archived: Mapped[bool] = mapped_column(default=False)

    class ProjectClauses(fr.ClauseNamespace):
        model = Project
        default_scope = fr.where_clause(Project.archived.is_(False))

    class Owner(fr.IDMixin, SharedBase):
        name: Mapped[str]
        hidden: Mapped[bool] = mapped_column(default=False)

    class OwnerClauses(fr.ClauseNamespace):
        model = Owner
        default_scope = fr.where_clause(Owner.hidden.is_(False))

    class Task(fr.IDMixin, SharedBase):
        title: Mapped[str]
        project_id: Mapped[int] = mapped_column(ForeignKey(Project.id))
        owner_id: Mapped[int | None] = mapped_column(ForeignKey(Owner.id), default=None)

    class Label(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class TaskSchema(fr.IDSchema):
        title: str
        project_id: fr.MustExist[int, Project]
        owner_id: fr.IDRef[Owner] | None = None

    class TaskIn(fr.BaseSchema):
        """A request body of a custom route: the fields of TaskSchema, no id."""

        title: str
        project_id: fr.MustExist[int, Project]
        owner_id: fr.IDRef[Owner] | None = None

    return _Models(Project, Owner, Task, Label, TaskSchema, TaskIn)


@dataclass
class _Api:
    kind: type
    models: _Models
    engine: Callable[[], Engine]
    make_client: Callable[..., RestlyTestClient]

    @property
    def is_async(self) -> bool:
        return self.kind is fr.AsyncRestView

    def pick(self, sync_fn: Callable[..., Any], async_fn: Callable[..., Any]) -> Any:
        return async_fn if self.is_async else sync_fn


@pytest.fixture(params=[fr.RestView, fr.AsyncRestView], ids=["sync", "async"])
def api(request: pytest.FixtureRequest) -> Iterator[_Api]:
    models = _models()
    if request.param is fr.RestView:
        sync_engine, _ = request.getfixturevalue("sync_db")
        SharedBase.metadata.create_all(sync_engine)

        def engine() -> Engine:
            return sync_engine

    else:
        create_tables()

        def engine() -> Engine:
            return _fr_globals.async_make_session.kw["bind"].sync_engine

    def make_client(
        *, view_model: Any = None, **task_view_attrs: Any
    ) -> RestlyTestClient:
        app = FastAPI()
        unscoped = fr.clauses.UNSCOPED
        for name, model in (("projects", models.Project), ("owners", models.Owner)):
            view = type(
                f"{model.__name__}View",
                (request.param,),
                {"prefix": f"/{name}", "model": model, "scope": unscoped},
            )
            fr.include_view(app, view)
        # a view of another model gets a generated schema
        namespace = (
            {"model": view_model}
            if view_model is not None
            else {"model": models.Task, "schema": models.TaskSchema}
        )
        task_view = type(
            "TaskView",
            (request.param,),
            {"prefix": "/tasks", **namespace, **task_view_attrs},
        )
        fr.include_view(app, task_view)
        return RestlyTestClient(app)

    yield _Api(request.param, models, engine, make_client)


def _seed(client: RestlyTestClient) -> dict[str, int]:
    """A live project and owner, and a project and owner that the default
    scopes hide."""

    def post(path: str, body: dict[str, Any]) -> int:
        return client.post(path, json=body).json()["id"]

    return {
        "project": post("/projects/", {"name": "live"}),
        "archived": post("/projects/", {"name": "old", "archived": True}),
        "owner": post("/owners/", {"name": "ann"}),
        "hidden": post("/owners/", {"name": "bob", "hidden": True}),
    }


def _task_count(client: RestlyTestClient) -> int:
    return len(client.get("/tasks/").json()["data"])


# ---------------------------------------------------------------------------
# A create of your own is still checked
# ---------------------------------------------------------------------------


def test_create_that_builds_its_own_object_is_still_checked(api):
    Task = api.models.Task

    def create(self, schema_obj):
        return Task(**schema_obj.model_dump())

    async def async_create(self, schema_obj):
        return Task(**schema_obj.model_dump())

    client = api.make_client(create=api.pick(create, async_create))
    ids = _seed(client)

    missing = client.post(
        "/tasks/", json={"title": "a", "project_id": 999}, assert_status_code=404
    )
    assert missing.json()["detail"] == "Id not found for project_id: 999"
    # a row that the target's default_scope hides is a miss too
    client.post(
        "/tasks/",
        json={"title": "b", "project_id": ids["archived"]},
        assert_status_code=404,
    )
    # an IDRef on a foreign-key column dumps to a plain id, so it is
    # checked before the business method as well
    hidden = client.post(
        "/tasks/",
        json={"title": "c", "project_id": ids["project"], "owner_id": ids["hidden"]},
        assert_status_code=404,
    )
    assert hidden.json()["detail"] == f"Id not found for owner_id: {ids['hidden']}"
    assert _task_count(client) == 0

    client.post(
        "/tasks/",
        json={"title": "d", "project_id": ids["project"], "owner_id": ids["owner"]},
        assert_status_code=201,
    )
    assert _task_count(client) == 1


def test_update_that_changes_the_object_itself_is_still_checked(api):
    def update(self, obj, schema_obj):
        for name, value in schema_obj.model_dump(exclude_unset=True).items():
            setattr(obj, name, value)
        return obj

    async def async_update(self, obj, schema_obj):
        for name, value in schema_obj.model_dump(exclude_unset=True).items():
            setattr(obj, name, value)
        return obj

    client = api.make_client(update=api.pick(update, async_update))
    ids = _seed(client)
    task = client.post("/tasks/", json={"title": "a", "project_id": ids["project"]})

    client.patch(
        f"/tasks/{task.json()['id']}",
        json={"project_id": ids["archived"]},
        assert_status_code=404,
    )
    row = client.get(f"/tasks/{task.json()['id']}").json()
    assert row["project_id"] == ids["project"]


# ---------------------------------------------------------------------------
# The order of the errors
# ---------------------------------------------------------------------------


def _forbid(*actions: str) -> tuple[Callable[..., Any], Callable[..., Any]]:
    def authorize(self, action, obj=None, data=None):
        if action in actions:
            raise fr.exc.Forbidden("no")

    async def async_authorize(self, action, obj=None, data=None):
        if action in actions:
            raise fr.exc.Forbidden("no")

    return authorize, async_authorize


def test_create_answers_403_before_a_reference_404(api):
    client = api.make_client(authorize=api.pick(*_forbid(fr.Action.CREATE)))
    client.post(
        "/tasks/", json={"title": "a", "project_id": 999}, assert_status_code=403
    )


def test_update_answers_row_404_then_403_then_reference_404(api):
    client = api.make_client()
    ids = _seed(client)
    task_id = client.post(
        "/tasks/", json={"title": "a", "project_id": ids["project"]}
    ).json()["id"]

    # the row comes first: an unknown row is a 404 about the row
    unknown_row = client.patch(
        "/tasks/999", json={"project_id": 998}, assert_status_code=404
    )
    assert "Task" in unknown_row.json()["detail"]

    # then authorize, with the loaded row
    forbidding = api.make_client(authorize=api.pick(*_forbid(fr.Action.UPDATE)))
    forbidding.patch(
        f"/tasks/{task_id}", json={"project_id": 998}, assert_status_code=403
    )

    # then the references
    reference = client.patch(
        f"/tasks/{task_id}", json={"project_id": 998}, assert_status_code=404
    )
    assert reference.json()["detail"] == "Id not found for project_id: 998"


# ---------------------------------------------------------------------------
# write_action(data=...)
# ---------------------------------------------------------------------------


def test_write_action_checks_the_references_of_data(api):
    Task, TaskIn = api.models.Task, api.models.TaskIn

    def import_task(self, body: TaskIn):
        task = Task(title=body.title.upper(), project_id=body.project_id)
        with self.write_action("import", obj=task, data=body):
            pass
        return {"id": task.id}

    async def async_import_task(self, body: TaskIn):
        task = Task(title=body.title.upper(), project_id=body.project_id)
        async with self.write_action("import", obj=task, data=body):
            pass
        return {"id": task.id}

    route = fr.post("/import")(api.pick(import_task, async_import_task))
    client = api.make_client(import_task=route)
    ids = _seed(client)

    client.post(
        "/tasks/import",
        json={"title": "a", "project_id": ids["archived"]},
        assert_status_code=404,
    )
    assert _task_count(client) == 0
    client.post("/tasks/import", json={"title": "a", "project_id": ids["project"]})
    assert [t["title"] for t in client.get("/tasks/").json()["data"]] == ["A"]


def test_write_action_checks_each_schema_object_of_a_list(api):
    Task, TaskIn = api.models.Task, api.models.TaskIn

    def bulk(self, body: list[TaskIn]):
        with self.write_action("bulk", obj=None, data=body):
            for item in body:
                self.session.add(Task(title=item.title, project_id=item.project_id))
        return {"ok": True}

    async def async_bulk(self, body: list[TaskIn]):
        async with self.write_action("bulk", obj=None, data=body):
            for item in body:
                self.session.add(Task(title=item.title, project_id=item.project_id))
        return {"ok": True}

    route = fr.post("/bulk")(api.pick(bulk, async_bulk))
    client = api.make_client(bulk=route)
    ids = _seed(client)

    body = [
        {"title": "a", "project_id": ids["project"]},
        {"title": "b", "project_id": 999},
    ]
    response = client.post("/tasks/bulk", json=body, assert_status_code=404)
    assert response.json()["detail"] == "Id not found for [1].project_id: 999"
    assert _task_count(client) == 0


def test_write_action_checks_a_schema_of_another_model(api):
    """A reference names its model, so a ``data=`` schema for another model
    than the view's is checked against the right table."""
    Project, Label = api.models.Project, api.models.Label

    class Attach(BaseModel):
        label_name: str
        project_id: fr.MustExist[int, Project]

    def attach(self, body: Attach):
        with self.write_action("attach", obj=Label(name=body.label_name), data=body):
            pass
        return {"ok": True}

    async def async_attach(self, body: Attach):
        label = Label(name=body.label_name)
        async with self.write_action("attach", obj=label, data=body):
            pass
        return {"ok": True}

    client = api.make_client(
        view_model=Label, attach=fr.post("/attach")(api.pick(attach, async_attach))
    )
    ids = _seed(client)

    client.post(
        "/tasks/attach",
        json={"label_name": "x", "project_id": ids["archived"]},
        assert_status_code=404,
    )
    client.post("/tasks/attach", json={"label_name": "x", "project_id": ids["project"]})


# ---------------------------------------------------------------------------
# Nested schemas
# ---------------------------------------------------------------------------


def test_references_in_nested_schemas_are_checked(api):
    Project, Owner, Task = api.models.Project, api.models.Owner, api.models.Task

    class Line(BaseModel):
        title: str
        project_id: fr.MustExist[int, Project]
        owner: fr.IDRef[Owner] | None = None

    class Batch(BaseModel):
        reviewers: list[fr.IDRef[Owner]] = []
        lines: list[Line]

    def add_lines(self, body: Batch):
        with self.write_action("add_lines", obj=None, data=body):
            for line in body.lines:
                self.session.add(Task(title=line.title, project_id=line.project_id))
        return {"ok": True}

    async def async_add_lines(self, body: Batch):
        async with self.write_action("add_lines", obj=None, data=body):
            for line in body.lines:
                self.session.add(Task(title=line.title, project_id=line.project_id))
        return {"ok": True}

    route = fr.post("/lines")(api.pick(add_lines, async_add_lines))
    client = api.make_client(add_lines=route)
    ids = _seed(client)

    def post(status: int, **body: Any) -> Any:
        return client.post("/tasks/lines", json=body, assert_status_code=status)

    line = {"title": "a", "project_id": ids["project"]}
    response = post(404, lines=[line, {"title": "b", "project_id": ids["archived"]}])
    assert response.json()["detail"] == (
        f"Id not found for lines[1].project_id: {ids['archived']}"
    )
    response = post(404, lines=[{**line, "owner": ids["hidden"]}])
    assert response.json()["detail"] == (
        f"Id not found for lines[0].owner: {ids['hidden']}"
    )
    # a list of references names every id it misses
    response = post(404, reviewers=[ids["owner"], ids["hidden"], 999], lines=[line])
    assert response.json()["detail"] == (
        f"Id not found for reviewers: [{ids['hidden']}, 999]"
    )
    assert _task_count(client) == 0

    post(201, reviewers=[ids["owner"]], lines=[line, {**line, "owner": ids["owner"]}])
    assert _task_count(client) == 2


# ---------------------------------------------------------------------------
# The CSV import shape: handle_create for each row
# ---------------------------------------------------------------------------


def test_handle_create_for_each_row_checks_each_row(api):
    Task, TaskIn = api.models.Task, api.models.TaskIn

    def create(self, schema_obj):
        return Task(**schema_obj.model_dump())

    async def async_create(self, schema_obj):
        return Task(**schema_obj.model_dump())

    def import_rows(self, project_ids: list[int] = Body()):
        errors = []
        with self.shared_write_action_commit():
            for n, project_id in enumerate(project_ids, start=1):
                row = TaskIn(title=f"row {n}", project_id=project_id)
                try:
                    with self.session.begin_nested():
                        self.handle_create(row)
                except NotFound as exc:
                    errors.append(f"row {n}: {exc.detail}")
        return {"errors": errors}

    async def async_import_rows(self, project_ids: list[int] = Body()):
        errors = []
        async with self.shared_write_action_commit():
            for n, project_id in enumerate(project_ids, start=1):
                row = TaskIn(title=f"row {n}", project_id=project_id)
                try:
                    async with self.session.begin_nested():
                        await self.handle_create(row)
                except NotFound as exc:
                    errors.append(f"row {n}: {exc.detail}")
        return {"errors": errors}

    client = api.make_client(
        create=api.pick(create, async_create),
        import_rows=fr.post("/import-rows")(api.pick(import_rows, async_import_rows)),
    )
    ids = _seed(client)

    result = client.post(
        "/tasks/import-rows", json=[ids["project"], ids["archived"], ids["project"]]
    ).json()
    assert result == {
        "errors": [f"row 2: Id not found for project_id: {ids['archived']}"]
    }
    titles = sorted(t["title"] for t in client.get("/tasks/").json()["data"])
    assert titles == ["row 1", "row 3"]


# ---------------------------------------------------------------------------
# One query for each referenced model
# ---------------------------------------------------------------------------


def _count_selects(engine: Engine, table: str) -> tuple[list[str], Callable[[], None]]:
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        text = statement.lstrip().lower()
        if text.startswith("select") and f"from {table}" in text:
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    return seen, lambda: event.remove(engine, "before_cursor_execute", record)


def test_a_crud_create_checks_its_data_once(api):
    """The bracket checks the data, and ``make_new_object`` takes the rows
    from its record: one query for the ``MustExist`` field and one for the
    ``IDRef`` field."""
    client = api.make_client()
    ids = _seed(client)

    projects, stop_projects = _count_selects(api.engine(), "project")
    owners, stop_owners = _count_selects(api.engine(), "owner")
    try:
        client.post(
            "/tasks/",
            json={"title": "a", "project_id": ids["project"], "owner_id": ids["owner"]},
            assert_status_code=201,
        )
    finally:
        stop_projects()
        stop_owners()
    assert len(projects) == 1
    assert len(owners) == 1


def test_a_reference_that_the_business_method_changes_is_checked_again(api):
    """The record holds the ids that the bracket checked: a ``create`` that
    changes a reference after the check gets a new check in
    ``make_new_object``."""
    archived: dict[str, int] = {}

    def create(self, schema_obj):
        schema_obj.project_id = archived["id"]
        return fr.objects.make_new_object(self.session, self.model, schema_obj)

    async def async_create(self, schema_obj):
        schema_obj.project_id = archived["id"]
        return await fr.objects.async_make_new_object(
            self.session, self.model, schema_obj
        )

    client = api.make_client(create=api.pick(create, async_create))
    ids = _seed(client)
    archived["id"] = ids["archived"]

    client.post(
        "/tasks/",
        json={"title": "a", "project_id": ids["project"]},
        assert_status_code=404,
    )
    assert _task_count(client) == 0


def test_a_long_list_is_checked_in_chunks(sync_db):
    """The ids of one model go in ``IN`` lists of a limited length, so a long
    body stays below the parameter limit of the database."""
    engine, make_session = sync_db
    models = _models()
    SharedBase.metadata.create_all(engine)

    with make_session() as session:
        projects = [models.Project(name=f"p{n}") for n in range(1200)]
        session.add_all(projects)
        session.flush()
        body = [models.TaskIn(title="t", project_id=p.id) for p in projects]

        statements, stop = _count_selects(engine, "project")
        try:
            found = _check_body_references(session, body)
        finally:
            stop()
        assert len(found) == 1200
        assert len(statements) == 3

        body[1100] = models.TaskIn(title="t", project_id=99999)
        with pytest.raises(NotFound, match=r"\[1100\]\.project_id: 99999"):
            _check_body_references(session, body)


# ---------------------------------------------------------------------------
# The record of the checks
# ---------------------------------------------------------------------------


def test_the_record_ends_with_the_bracket_and_an_inner_bracket_restores_it(api):
    """The record lives on ``session.info`` while the block runs. An inner
    bracket has its own record, and the outer one comes back after it."""
    Project, TaskIn = api.models.Project, api.models.TaskIn
    seen: dict[str, Any] = {}

    def record(session: Any) -> set[Any]:
        return set(session.info[_CHECKED_REFERENCES_KEY])

    def nested(self, body: TaskIn):
        with self.write_action("outer", obj=None, data=body):
            seen["outer"] = record(self.session)
            with self.write_action("inner", obj=None):
                seen["inner"] = record(self.session)
            seen["after"] = record(self.session)
        seen["left"] = _CHECKED_REFERENCES_KEY in self.session.info
        return {"ok": True}

    async def async_nested(self, body: TaskIn):
        async with self.write_action("outer", obj=None, data=body):
            seen["outer"] = record(self.session)
            async with self.write_action("inner", obj=None):
                seen["inner"] = record(self.session)
            seen["after"] = record(self.session)
        seen["left"] = _CHECKED_REFERENCES_KEY in self.session.info
        return {"ok": True}

    client = api.make_client(nested=fr.post("/nested")(api.pick(nested, async_nested)))
    ids = _seed(client)

    client.post("/tasks/nested", json={"title": "a", "project_id": ids["project"]})
    assert seen == {
        "outer": {(Project, ids["project"])},
        "inner": set(),
        "after": {(Project, ids["project"])},
        "left": False,
    }


def test_no_record_outside_a_write_action(sync_db):
    """A script that calls the builders has no record: they query."""
    engine, make_session = sync_db
    models = _models()
    SharedBase.metadata.create_all(engine)

    with make_session() as session:
        project = models.Project(name="live")
        session.add(project)
        session.flush()
        statements, stop = _count_selects(engine, "project")
        try:
            task = fr.objects.make_new_object(
                session, models.Task, models.TaskIn(title="t", project_id=project.id)
            )
        finally:
            stop()
        assert task.project_id == project.id
        assert len(statements) == 1
        assert _CHECKED_REFERENCES_KEY not in session.info
        assert session.scalars(select(models.Task)).all() == [task]


# ---------------------------------------------------------------------------
# Cases from review
# ---------------------------------------------------------------------------


def _joined_models() -> tuple[Any, Any, Any]:
    class Shelf(fr.IDMixin, SharedBase):
        name: Mapped[str]
        books: Mapped[list["Book"]] = relationship(
            back_populates="shelf", lazy="joined", default_factory=list
        )

    class Book(fr.IDMixin, SharedBase):
        title: Mapped[str]
        shelf_id: Mapped[int] = mapped_column(ForeignKey(Shelf.id), default=None)
        shelf: Mapped[Shelf] = relationship(back_populates="books", default=None)

    class BookIn(BaseModel):
        title: str
        shelf: fr.IDRef[Shelf]

    return Shelf, Book, BookIn


def test_a_reference_to_a_model_with_a_joined_collection_sync(sync_db):
    """The rows of the check come from one statement, so a joined eager load
    of a collection needs ``unique()``."""
    engine, make_session = sync_db
    Shelf, Book, BookIn = _joined_models()
    SharedBase.metadata.create_all(engine)

    with make_session() as session:
        shelf = Shelf(name="s", books=[Book(title="a"), Book(title="b")])
        session.add(shelf)
        session.flush()
        found = _check_body_references(session, BookIn(title="c", shelf=shelf.id))
        assert found == {(Shelf, shelf.id): shelf}
        book = fr.objects.make_new_object(
            session, Book, BookIn(title="c", shelf=shelf.id)
        )
        assert book.shelf is shelf


def test_a_reference_to_a_model_with_a_joined_collection_async():
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from fastapi_restly.schemas._base import _async_check_body_references

    async def run() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        Shelf, Book, BookIn = _joined_models()
        async with engine.begin() as conn:
            await conn.run_sync(SharedBase.metadata.create_all)
        try:
            async with async_sessionmaker(bind=engine)() as session:
                shelf = Shelf(name="s", books=[Book(title="a"), Book(title="b")])
                session.add(shelf)
                await session.flush()
                body = BookIn(title="c", shelf=shelf.id)
                found = await _async_check_body_references(session, body)
                assert found == {(Shelf, shelf.id): shelf}
                book = await fr.objects.async_make_new_object(session, Book, body)
                assert book.shelf is shelf
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_the_check_does_not_write_an_object_that_is_being_built(api):
    """The check queries without autoflush: a pending object that the code
    finishes inside the block is not written half-built."""
    Task, TaskIn = api.models.Task, api.models.TaskIn

    def rename(self, body: TaskIn):
        task = Task(title=None, project_id=body.project_id)  # type: ignore[arg-type]
        self.session.add(task)
        with self.write_action("rename", obj=task, data=body):
            task.title = body.title
        return {"id": task.id}

    async def async_rename(self, body: TaskIn):
        task = Task(title=None, project_id=body.project_id)  # type: ignore[arg-type]
        self.session.add(task)
        async with self.write_action("rename", obj=task, data=body):
            task.title = body.title
        return {"id": task.id}

    route = fr.post("/rename")(api.pick(rename, async_rename))
    client = api.make_client(rename=route)
    ids = _seed(client)

    client.post("/tasks/rename", json={"title": "a", "project_id": ids["project"]})
    assert [t["title"] for t in client.get("/tasks/").json()["data"]] == ["a"]


def test_a_marker_inside_a_type_of_a_plain_model_fails_loudly(sync_db):
    """``fr.BaseSchema`` refuses ``list[MustExist[...]]`` when the class is
    defined. A plain pydantic model gets the same error at its first check,
    so the ids are never written unchecked."""
    engine, make_session = sync_db
    models = _models()
    SharedBase.metadata.create_all(engine)

    class Ids(BaseModel):
        project_ids: list[fr.MustExist[int, models.Project]]

    with make_session() as session:
        for _ in range(2):
            with pytest.raises(RestlyConfigurationError, match="project_ids"):
                _check_body_references(session, Ids(project_ids=[1, 999]))


def test_a_single_reference_matches_as_the_database_does(sync_db):
    """The database decides whether a row matches, also for a key that
    compares without case."""
    engine, make_session = sync_db

    class Country(SharedBase):
        __tablename__ = "reference_check_country"
        code: Mapped[str] = mapped_column(String(collation="NOCASE"), primary_key=True)

    class Visit(BaseModel):
        country: fr.IDRef[Country]

    SharedBase.metadata.create_all(engine)

    with make_session() as session:
        country = Country(code="nl")
        session.add(country)
        session.flush()
        found = _check_body_references(session, Visit(country="NL"))
        assert found == {(Country, "NL"): country}
