"""Tenant restrictions apply to child routes and ORM relationship loads."""

import io
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from typing import Annotated

import pydantic
import pytest
from app.countries.models import Country
from app.current import Current
from app.labels.models import Label, TaskLabel
from app.labels.views import TaskLabelView
from app.organizations.models import Organization
from app.projects.models import Project
from app.tasks.models import Task
from app.tasks.views import TaskView
from app.uploads.models import Upload, UploadLine
from app.users.models import User
from sqlalchemy import event, select
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import aliased, joinedload, lazyload, selectinload

import fastapi_restly as fr
from fastapi_restly.objects import async_make_new_object

from .conftest import Tenant


@dataclass
class TenantRows:
    identity: Tenant
    project: int
    task: int
    label: int
    link: int
    upload: int
    line: int


@pytest.fixture
async def tenants(restly_async_client, as_admin):
    """Seed two tenants through the app, leaving the test's identity unbound."""
    rows = []
    for slug in ("alpha", "beta"):
        org = (
            await restly_async_client.post(
                "/organizations", json={"name": slug.title(), "slug": slug}
            )
        ).json()
        with as_admin(org["id"]):
            user = (
                await restly_async_client.post(
                    "/users", json={"name": slug, "email": f"owner@{slug}.test"}
                )
            ).json()
        identity = Tenant(org_id=org["id"], user_id=user["id"])
        with identity.acting():
            project = (
                await restly_async_client.post("/projects", json={"name": slug})
            ).json()
            task = (
                await restly_async_client.post(
                    "/tasks", json={"title": slug, "project_id": project["id"]}
                )
            ).json()
            label = (
                await restly_async_client.post("/labels", json={"name": slug})
            ).json()
            link = (
                await restly_async_client.post(
                    "/task-labels",
                    json={"task_id": task["id"], "label_id": label["id"]},
                )
            ).json()
            upload = (
                await restly_async_client.post(
                    "/uploads",
                    files={
                        "file": ("rows.csv", io.BytesIO(b"title\nrow\n"), "text/csv")
                    },
                )
            ).json()
            (line,) = (
                await restly_async_client.get(f"/uploads/{upload['id']}/lines")
            ).json()
        rows.append(
            TenantRows(
                identity,
                project["id"],
                task["id"],
                label["id"],
                link["id"],
                upload["id"],
                line["id"],
            )
        )
    return rows


async def test_task_label_list_and_count_are_tenant_scoped(
    restly_async_client, tenants
):
    own, foreign = tenants
    with own.identity.acting():
        page = (await restly_async_client.get("/task-labels")).json()
    assert page["total_count"] == 1
    assert [row["id"] for row in page["data"]] == [own.link]


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
async def test_task_label_routes_hide_foreign_rows(
    restly_async_client, tenants, method
):
    own, foreign = tenants
    with own.identity.acting():
        await getattr(restly_async_client, method)(
            f"/task-labels/{foreign.link}",
            **({"json": {}} if method == "patch" else {}),
            assert_status_code=404,
        )
    with foreign.identity.acting():
        assert (await restly_async_client.get(f"/task-labels/{foreign.link}")).json()[
            "id"
        ] == foreign.link


@pytest.mark.parametrize("admin", [False, True])
async def test_task_label_access_for_owner_and_admin(
    restly_async_client, tenants, as_admin, admin
):
    own, foreign = tenants
    target = foreign if admin else own
    with as_admin(own.identity.org_id) if admin else own.identity.acting():
        page = (await restly_async_client.get("/task-labels")).json()
        expected = {own.link, foreign.link} if admin else {own.link}
        assert {row["id"] for row in page["data"]} == expected
        assert page["total_count"] == len(expected)
        url = f"/task-labels/{target.link}"
        assert (await restly_async_client.get(url)).json()["id"] == target.link
        assert (await restly_async_client.patch(url, json={})).json()[
            "id"
        ] == target.link
        await restly_async_client.delete(url)
        await restly_async_client.get(url, assert_status_code=404)


@pytest.mark.parametrize("foreign_field", ["task_id", "label_id"])
async def test_task_label_references_reject_foreign_rows(
    restly_async_client, tenants, foreign_field
):
    own, foreign = tenants
    payload = {"task_id": own.task, "label_id": own.label}
    payload[foreign_field] = (
        foreign.task if foreign_field == "task_id" else foreign.label
    )
    with own.identity.acting():
        await restly_async_client.post(
            "/task-labels", json=payload, assert_status_code=404
        )


@pytest.mark.parametrize("foreign_field", ["task_id", "label_id"])
async def test_task_label_update_rejects_foreign_references(
    restly_async_client, tenants, foreign_field
):
    own, foreign = tenants
    target = foreign.task if foreign_field == "task_id" else foreign.label
    with own.identity.acting():
        await restly_async_client.patch(
            f"/task-labels/{own.link}",
            json={foreign_field: target},
            assert_status_code=404,
        )
        link = (await restly_async_client.get(f"/task-labels/{own.link}")).json()
    assert (link["task_id"], link["label_id"]) == (own.task, own.label)


async def test_task_label_requires_both_ends_to_belong_to_tenant(
    restly_async_client, tenants, as_admin
):
    own, foreign = tenants
    with as_admin(own.identity.org_id):
        link = (
            await restly_async_client.post(
                "/task-labels", json={"task_id": own.task, "label_id": foreign.label}
            )
        ).json()
    for tenant in tenants:
        with tenant.identity.acting():
            await restly_async_client.get(
                f"/task-labels/{link['id']}", assert_status_code=404
            )


@pytest.mark.parametrize("admin", [False, True])
async def test_upload_lines_route_hides_foreign_uploads(
    restly_async_client, tenants, as_admin, admin
):
    own, foreign = tenants
    with as_admin(own.identity.org_id) if admin else own.identity.acting():
        lines = (await restly_async_client.get(f"/uploads/{own.upload}/lines")).json()
        assert [line["id"] for line in lines] == [own.line]
        response = await restly_async_client.get(
            f"/uploads/{foreign.upload}/lines", assert_status_code=200 if admin else 404
        )
    if admin:
        assert [line["id"] for line in response.json()] == [foreign.line]


@pytest.mark.parametrize("relationship", ["users", "projects", "labels"])
@pytest.mark.parametrize("loader", [joinedload, selectinload, lazyload])
@pytest.mark.parametrize("admin", [False, True])
async def test_public_organization_relationships_respect_identity(
    tenants, relationship, loader, admin
):
    own, foreign = tenants
    identity = replace(own.identity, is_admin=admin)
    with Current.bind(**asdict(identity)):
        async with fr.open_async_session() as session:
            organizations = (
                (
                    await session.scalars(
                        select(Organization)
                        .where(
                            Organization.id.in_(
                                [own.identity.org_id, foreign.identity.org_id]
                            )
                        )
                        .options(loader(getattr(Organization, relationship)))
                    )
                )
                .unique()
                .all()
            )
            loaded = {
                org.id: await session.run_sync(
                    lambda _, org=org: [
                        row.organization_id for row in getattr(org, relationship)
                    ]
                )
                for org in organizations
            }
    assert loaded[own.identity.org_id] == [own.identity.org_id]
    assert loaded[foreign.identity.org_id] == (
        [foreign.identity.org_id] if admin else []
    )


@pytest.mark.parametrize(
    "model, field", [(Task, "task"), (TaskLabel, "link"), (UploadLine, "line")]
)
@pytest.mark.parametrize("use_alias", [False, True])
@pytest.mark.parametrize("admin", [False, True])
async def test_direct_selects_respect_identity(tenants, model, field, use_alias, admin):
    own, foreign = tenants
    selected = aliased(model) if use_alias else model
    identity = replace(own.identity, is_admin=admin)
    with Current.bind(**asdict(identity)):
        async with fr.open_async_session() as session:
            rows = (await session.scalars(select(selected))).all()
    expected = (
        {getattr(own, field), getattr(foreign, field)}
        if admin
        else {getattr(own, field)}
    )
    assert {row.id for row in rows} == expected


@pytest.mark.parametrize(
    "view_type, field", [(TaskView, "task"), (TaskLabelView, "link")]
)
async def test_unscoped_view_reads_keep_tenant_restriction(tenants, view_type, field):
    own, foreign = tenants
    with Current.bind(**asdict(own.identity)):
        async with fr.open_async_session() as session:
            view = view_type(request=None, session=session)
            listing = await view.get_many({}, scope=fr.clauses.UNSCOPED)
            row = await view.get_one(getattr(own, field), scope=fr.clauses.UNSCOPED)
            with pytest.raises(fr.exc.NotFound):
                await view.get_one(getattr(foreign, field), scope=fr.clauses.UNSCOPED)
    assert {row.id for row in listing.objects} == {getattr(own, field)}
    assert row.id == getattr(own, field)


class UnscopedLinkSchema(pydantic.BaseModel):
    """Reference checks that opt out of ``default_scope``, not of tenancy."""

    task_id: Annotated[int, fr.RefExists(Task, scope=fr.clauses.UNSCOPED)]
    label_id: Annotated[int, fr.RefExists(Label, scope=fr.clauses.UNSCOPED)]


@pytest.mark.parametrize("foreign_field", [None, "task_id", "label_id"])
async def test_unscoped_reference_checks_keep_tenant_restriction(
    tenants, foreign_field
):
    own, foreign = tenants
    ids = {"task_id": own.task, "label_id": own.label}
    if foreign_field:
        ids[foreign_field] = getattr(foreign, foreign_field.removesuffix("_id"))
    with Current.bind(**asdict(own.identity)):
        async with fr.open_async_session() as session:
            if foreign_field:
                with pytest.raises(fr.exc.NotFound):
                    await async_make_new_object(
                        session, TaskLabel, UnscopedLinkSchema(**ids)
                    )
            else:
                link = await async_make_new_object(
                    session, TaskLabel, UnscopedLinkSchema(**ids)
                )
                session.expunge(link)


async def test_relationship_loads_carry_each_criterion_once(
    restly_async_client, tenants
):
    """A load that inherits the criteria must not receive another copy."""
    own, _ = tenants
    with own.identity.acting():
        parent = own.task
        for _ in range(3):
            parent = (
                await restly_async_client.post(
                    "/tasks",
                    json={
                        "title": "sub",
                        "project_id": own.project,
                        "parent_id": parent,
                    },
                )
            ).json()["id"]

    def walk_subtasks(sync_session):
        statements = []

        def record(conn, cursor, statement, *_):
            if statement.startswith("SELECT"):
                statements.append(statement)

        bind = sync_session.get_bind()
        event.listen(bind, "before_cursor_execute", record)
        try:
            task = sync_session.scalars(select(Task).where(Task.id == own.task)).one()
            while task.subtasks:
                (task,) = task.subtasks
        finally:
            event.remove(bind, "before_cursor_execute", record)
        return statements

    with Current.bind(**asdict(own.identity)):
        async with fr.open_async_session() as session:
            statements = await session.run_sync(walk_subtasks)
    # The root SELECT, then one lazy load per level. Each names Task's EXISTS once.
    assert [statement.count("EXISTS") for statement in statements] == [1] * 5


@pytest.mark.parametrize(
    "model", [User, Project, Label, Task, TaskLabel, Upload, UploadLine]
)
async def test_direct_tenant_reads_require_context(tenants, model):
    async with fr.open_async_session() as session:
        with missing_identity():
            await session.scalars(select(model))


@pytest.mark.parametrize("relationship", ["users", "projects", "labels"])
@pytest.mark.parametrize("loader", [joinedload, selectinload, lazyload])
async def test_public_parent_cannot_load_tenant_rows_without_context(
    tenants, relationship, loader
):
    own, _ = tenants
    async with fr.open_async_session() as session:
        with missing_identity():
            organization = (
                (
                    await session.scalars(
                        select(Organization)
                        .where(Organization.id == own.identity.org_id)
                        .options(loader(getattr(Organization, relationship)))
                    )
                )
                .unique()
                .one()
            )
            await session.run_sync(lambda _: getattr(organization, relationship))


async def test_public_models_remain_readable_without_context(tenants):
    async with fr.open_async_session() as session:
        org_ids = set(await session.scalars(select(Organization.id)))
        countries = (await session.scalars(select(Country))).all()
    assert {rows.identity.org_id for rows in tenants} <= org_ids
    assert {"NL", "DE"} <= {country.code for country in countries}


@contextmanager
def missing_identity():
    """Accept the context error directly or wrapped by SQLAlchemy execution."""
    with pytest.raises((LookupError, StatementError)) as error:
        yield
    if isinstance(error.value, StatementError):
        assert isinstance(error.value.orig, LookupError)
