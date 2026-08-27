"""The view scope and scoped reference checks.

A model's declared ``default_scope`` arms every view read (list,
retrieve, count)
and every reference check on the model. A view's ``scope`` attribute
replaces that default; ``fr.clauses.UNSCOPED`` opts out explicitly. Reference
checks (``MustExist`` / ``RefExists`` / ``IDRef`` / ``IDSchema``) apply
only the predicate half of the clause, and ``RefExists(scope=...)``
overrides per field, with ``fr.clauses.UNSCOPED`` the one explicit
unscoped spelling, system-wide.
"""

from typing import Annotated

import pytest
from fastapi import Depends, Header
from sqlalchemy import ForeignKey, Select, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

import fastapi_restly as fr
from fastapi_restly.clauses import _default_scope
from fastapi_restly.exc import NotFound, RestlyConfigurationError
from fastapi_restly.schemas._base import (
    _check_ref_exists,
    _resolve_ids_to_sqlalchemy_objects,
)

from .conftest import create_tables

# ---------------------------------------------------------------------------
# default_scope arms every read; a view scope replaces it
# ---------------------------------------------------------------------------


def test_default_scope_and_view_scope_over_http(client):
    class ScopeItem(fr.IDBase):
        tenant_id: Mapped[int]
        name: Mapped[str]
        deleted: Mapped[bool] = mapped_column(default=False)

    class Current(fr.ContextNamespace):
        tenant_id: fr.ContextParam[int]

    class ScopeItemClauses(fr.ClauseNamespace):
        model = ScopeItem

        is_deleted = fr.where_clause(ScopeItem.deleted.is_(True))
        owned_by_tenant = fr.where_clause(ScopeItem.tenant_id == Current.tenant_id)
        visible = fr.all_of(owned_by_tenant, fr.none_of(is_deleted))
        trashed = fr.all_of(owned_by_tenant, is_deleted)
        default_scope = visible

    class ScopeItemSchema(fr.IDSchema):
        tenant_id: int
        name: str
        deleted: bool = False

    async def bind_tenant(tenant: Annotated[int, Header(alias="x-tenant-id")]):
        with Current.tenant_id.bind(tenant_id=tenant):
            yield

    @fr.include_view(client.app)
    class ScopeItemView(fr.AsyncRestView):
        prefix = "/scope-items"
        model = ScopeItem
        schema = ScopeItemSchema
        dependencies = [Depends(bind_tenant)]

    @fr.include_view(client.app)
    class ScopeTrashView(fr.AsyncRestView):
        prefix = "/scope-trash"
        model = ScopeItem
        schema = ScopeItemSchema
        dependencies = [Depends(bind_tenant)]
        scope = ScopeItemClauses.trashed

    create_tables()

    t1 = {"x-tenant-id": "1"}
    t2 = {"x-tenant-id": "2"}

    def post(payload, headers):
        return client.post("/scope-items/", json=payload, headers=headers).json()

    live = post({"tenant_id": 1, "name": "live"}, t1)
    gone = post({"tenant_id": 1, "name": "gone", "deleted": True}, t1)
    other = post({"tenant_id": 2, "name": "other"}, t2)

    # list + count see only the caller's live rows
    payload = client.get("/scope-items/", headers=t1).json()
    assert [row["name"] for row in payload["data"]] == ["live"]
    assert payload["total_count"] == 1

    # retrieve outside the scope is 404: another tenant's row and a
    # soft-deleted own row alike
    client.get(f"/scope-items/{other['id']}", headers=t1, assert_status_code=404)
    client.get(f"/scope-items/{gone['id']}", headers=t1, assert_status_code=404)
    assert client.get(f"/scope-items/{live['id']}", headers=t1).json()["name"] == "live"

    # writes load through the same scope: cross-tenant update is 404
    client.patch(
        f"/scope-items/{other['id']}",
        json={"name": "stolen"},
        headers=t1,
        assert_status_code=404,
    )

    # the trash view's scope REPLACES the default: it sees exactly the
    # complement, and restoring through it works
    trash = client.get("/scope-trash/", headers=t1).json()
    assert [row["name"] for row in trash["data"]] == ["gone"]
    client.patch(f"/scope-trash/{gone['id']}", json={"deleted": False}, headers=t1)
    assert client.get(f"/scope-items/{gone['id']}", headers=t1).json()["name"] == "gone"


def test_unscoped_view_reads_past_the_default_scope(client):
    class ScopeDoc(fr.IDBase):
        tenant_id: Mapped[int]
        name: Mapped[str]

    class Current(fr.ContextNamespace):
        tenant_id: fr.ContextParam[int]

    class ScopeDocClauses(fr.ClauseNamespace):
        model = ScopeDoc

        owned_by_tenant = fr.where_clause(ScopeDoc.tenant_id == Current.tenant_id)
        default_scope = owned_by_tenant

    class ScopeDocSchema(fr.IDSchema):
        tenant_id: int
        name: str

    async def bind_tenant(tenant: Annotated[int, Header(alias="x-tenant-id")]):
        with Current.tenant_id.bind(tenant_id=tenant):
            yield

    @fr.include_view(client.app)
    class ScopeDocView(fr.AsyncRestView):
        prefix = "/scope-docs"
        model = ScopeDoc
        schema = ScopeDocSchema
        dependencies = [Depends(bind_tenant)]

    @fr.include_view(client.app)
    class AllDocsView(fr.AsyncRestView):
        prefix = "/all-docs"
        model = ScopeDoc
        schema = ScopeDocSchema
        scope = fr.clauses.UNSCOPED  # the explicit opt-out; e.g. behind admin auth

    create_tables()

    t1 = {"x-tenant-id": "1"}
    client.post("/scope-docs/", json={"tenant_id": 1, "name": "mine"}, headers=t1)
    client.post("/scope-docs/", json={"tenant_id": 2, "name": "theirs"}, headers=t1)

    scoped = client.get("/scope-docs/", headers=t1).json()
    assert [row["name"] for row in scoped["data"]] == ["mine"]

    unscoped = client.get("/all-docs/").json()  # no tenant bind needed
    assert {row["name"] for row in unscoped["data"]} == {"mine", "theirs"}


# ---------------------------------------------------------------------------
# a route names its own scope per read
# ---------------------------------------------------------------------------


def test_a_route_reads_through_its_own_scope(client):
    """A custom route lists or loads another surface of the same model by
    naming a scope on the read call; the view scope stays the default
    for every other read, and the route's literal path wins over /{id}."""

    class ScopeNote(fr.IDBase):
        tenant_id: Mapped[int]
        name: Mapped[str]
        deleted: Mapped[bool] = mapped_column(default=False)

    class Current(fr.ContextNamespace):
        tenant_id: fr.ContextParam[int]

    class ScopeNoteClauses(fr.ClauseNamespace):
        model = ScopeNote

        is_deleted = fr.where_clause(ScopeNote.deleted.is_(True))
        owned_by_tenant = fr.where_clause(ScopeNote.tenant_id == Current.tenant_id)
        visible = fr.all_of(owned_by_tenant, fr.none_of(is_deleted))
        trashed = fr.all_of(owned_by_tenant, is_deleted)
        default_scope = visible

    class ScopeNoteSchema(fr.IDSchema):
        tenant_id: int
        name: str
        deleted: bool = False

    async def bind_tenant(tenant: Annotated[int, Header(alias="x-tenant-id")]):
        with Current.tenant_id.bind(tenant_id=tenant):
            yield

    @fr.include_view(client.app)
    class ScopeNoteView(fr.AsyncRestView):
        prefix = "/scope-notes"
        model = ScopeNote
        schema = ScopeNoteSchema
        dependencies = [Depends(bind_tenant)]

        @fr.get("/trash")
        async def trash(self):
            result = await self.handle_get_many(
                self.request.query_params, scope=ScopeNoteClauses.trashed
            )
            return self.to_response(result, fr.ResponseShape.LISTING)

        @fr.get("/everything")
        async def everything(self):
            result = await self.handle_get_many(
                self.request.query_params, scope=fr.clauses.UNSCOPED
            )
            return self.to_response(result, fr.ResponseShape.LISTING)

        @fr.post("/{id}/restore")
        async def restore(self, id: int):
            note = await self.handle_get_one(id, scope=ScopeNoteClauses.trashed)
            async with self.write_action("restore", obj=note):
                note.deleted = False
            return self.to_response(note)

    create_tables()

    t1 = {"x-tenant-id": "1"}
    t2 = {"x-tenant-id": "2"}

    def post(payload, headers):
        return client.post("/scope-notes/", json=payload, headers=headers).json()

    live = post({"tenant_id": 1, "name": "live"}, t1)
    gone = post({"tenant_id": 1, "name": "gone", "deleted": True}, t1)
    post({"tenant_id": 2, "name": "other", "deleted": True}, t2)

    # the view's own reads still apply the default scope
    assert [
        r["name"] for r in client.get("/scope-notes/", headers=t1).json()["data"]
    ] == ["live"]

    # /trash is a route on the same view, not captured by /{id}, and reads
    # the trash surface: own deleted rows only, with the page total
    trash = client.get("/scope-notes/trash", headers=t1).json()
    assert [r["name"] for r in trash["data"]] == ["gone"]
    assert trash["total_count"] == 1

    # the per-read escape is the same loud spelling as everywhere else
    everything = client.get("/scope-notes/everything", headers=t1).json()
    assert {r["name"] for r in everything["data"]} == {"live", "gone", "other"}

    # restore loads through the trash surface: a live row is outside it
    client.post(
        f"/scope-notes/{live['id']}/restore", headers=t1, assert_status_code=404
    )
    restored = client.post(f"/scope-notes/{gone['id']}/restore", headers=t1).json()
    assert restored["deleted"] is False
    assert client.get(f"/scope-notes/{gone['id']}", headers=t1).json()["name"] == "gone"


def test_a_base_class_stacks_a_floor_under_every_read(client):
    """``apply_scope`` is the seam under every read. A base class that
    stacks the tenant clause there keeps a view that opted out, and a
    route that named its own scope, tenant-bound: the framework's scopes
    replace each other, the application's floor holds."""

    class FloorNote(fr.IDBase):
        tenant_id: Mapped[int]
        name: Mapped[str]
        deleted: Mapped[bool] = mapped_column(default=False)

    class Current(fr.ContextNamespace):
        tenant_id: fr.ContextParam[int]

    class FloorNoteClauses(fr.ClauseNamespace):
        model = FloorNote

        is_deleted = fr.where_clause(FloorNote.deleted.is_(True))
        owned_by_tenant = fr.where_clause(FloorNote.tenant_id == Current.tenant_id)
        default_scope = fr.none_of(is_deleted)

    class FloorNoteSchema(fr.IDSchema):
        tenant_id: int
        name: str
        deleted: bool = False

    async def bind_tenant(tenant: Annotated[int, Header(alias="x-tenant-id")]):
        with Current.tenant_id.bind(tenant_id=tenant):
            yield

    seen: list[object] = []

    class TenantBase(fr.AsyncRestView):
        dependencies = [Depends(bind_tenant)]

        def apply_scope(self, query, scope):
            seen.append(scope)
            return fr.apply_clauses(query, FloorNoteClauses.owned_by_tenant, scope)

    @fr.include_view(client.app)
    class FloorNoteView(TenantBase):
        prefix = "/floor-notes"
        model = FloorNote
        schema = FloorNoteSchema

        @fr.get("/trash")
        async def trash(self):
            result = await self.handle_get_many(
                self.request.query_params, scope=FloorNoteClauses.is_deleted
            )
            return self.to_response(result, fr.ResponseShape.LISTING)

    @fr.include_view(client.app)
    class OptedOutView(TenantBase):
        prefix = "/all-floor-notes"
        model = FloorNote
        schema = FloorNoteSchema
        scope = fr.clauses.UNSCOPED

    create_tables()

    t1 = {"x-tenant-id": "1"}
    t2 = {"x-tenant-id": "2"}

    def post(payload, headers):
        return client.post("/floor-notes/", json=payload, headers=headers).json()

    post({"tenant_id": 1, "name": "live"}, t1)
    gone = post({"tenant_id": 1, "name": "gone", "deleted": True}, t1)
    theirs = post({"tenant_id": 2, "name": "theirs", "deleted": True}, t2)

    names = lambda response: [r["name"] for r in response.json()["data"]]  # noqa: E731

    # the default read: model default under the floor
    assert names(client.get("/floor-notes/", headers=t1)) == ["live"]
    assert seen[-1] is FloorNoteClauses.default_scope

    # a route's own scope replaces the default; the floor still holds
    assert names(client.get("/floor-notes/trash", headers=t1)) == ["gone"]
    assert names(client.get("/floor-notes/trash", headers=t2)) == ["theirs"]
    assert seen[-1] is FloorNoteClauses.is_deleted

    # a view that opted out of the default scope is still tenant-bound
    assert names(client.get("/all-floor-notes/", headers=t1)) == ["live", "gone"]
    assert seen[-1] is fr.clauses.UNSCOPED
    client.get(f"/all-floor-notes/{theirs['id']}", headers=t1, assert_status_code=404)
    assert (
        client.get(f"/all-floor-notes/{gone['id']}", headers=t1).json()["name"]
        == "gone"
    )


# ---------------------------------------------------------------------------
# reference checks: default_scope, per-field override, explicit escape
# ---------------------------------------------------------------------------


def test_reference_checks_apply_scopes_over_http(client):
    class ScopeOwner(fr.IDBase):
        tenant_id: Mapped[int]
        active: Mapped[bool]

    class Current(fr.ContextNamespace):
        tenant_id: fr.ContextParam[int]

    class ScopeOwnerClauses(fr.ClauseNamespace):
        model = ScopeOwner

        owned_by_tenant = fr.where_clause(ScopeOwner.tenant_id == Current.tenant_id)
        is_active = fr.where_clause(ScopeOwner.active.is_(True))
        default_scope = fr.all_of(owned_by_tenant, is_active)

    class ScopeTask(fr.IDBase):
        title: Mapped[str]
        owner_id: Mapped[int] = mapped_column(ForeignKey(ScopeOwner.id))
        tenant_owner_id: Mapped[int | None] = mapped_column(default=None)
        audit_owner_id: Mapped[int | None] = mapped_column(default=None)

    class ScopeOwnerSchema(fr.IDSchema):
        tenant_id: int
        active: bool

    class ScopeTaskSchema(fr.IDSchema):
        title: str
        owner_id: fr.MustExist[int]  # target inferred from the FK
        tenant_owner_id: (
            Annotated[
                int, fr.RefExists(ScopeOwner, scope=ScopeOwnerClauses.owned_by_tenant)
            ]
            | None
        ) = None
        audit_owner_id: (
            Annotated[int, fr.RefExists(ScopeOwner, scope=fr.clauses.UNSCOPED)] | None
        ) = None

    async def bind_tenant(tenant: Annotated[int, Header(alias="x-tenant-id")]):
        with Current.tenant_id.bind(tenant_id=tenant):
            yield

    @fr.include_view(client.app)
    class ScopeOwnerView(fr.AsyncRestView):
        prefix = "/scope-owners"
        model = ScopeOwner
        schema = ScopeOwnerSchema
        scope = fr.clauses.UNSCOPED  # seeding endpoint: read past the default_scope

    @fr.include_view(client.app)
    class ScopeTaskView(fr.AsyncRestView):
        prefix = "/scope-tasks"
        model = ScopeTask
        schema = ScopeTaskSchema
        dependencies = [Depends(bind_tenant)]

    create_tables()

    def owner(tenant_id, active):
        payload = {"tenant_id": tenant_id, "active": active}
        return client.post("/scope-owners/", json=payload).json()["id"]

    mine = owner(1, True)
    mine_inactive = owner(1, False)
    theirs = owner(2, True)

    t1 = {"x-tenant-id": "1"}

    def post_task(assert_status_code=201, **fields):
        return client.post(
            "/scope-tasks/",
            json={"title": "t", "owner_id": mine, **fields},
            headers=t1,
            assert_status_code=assert_status_code,
        )

    # MustExist checks against the target's default_scope: cross-tenant and
    # inactive rows do not exist
    response = post_task(owner_id=theirs, assert_status_code=404)
    assert "owner_id" in response.json()["detail"]
    post_task(owner_id=mine_inactive, assert_status_code=404)
    post_task(owner_id=mine)

    # scope= override: tenant-bound but active or not
    post_task(tenant_owner_id=mine_inactive)
    post_task(tenant_owner_id=theirs, assert_status_code=404)

    # scope=UNSCOPED: the explicit unscoped escape
    post_task(audit_owner_id=theirs)


def test_idref_resolution_applies_default_scope_over_http(client):
    class ScopeAuthor(fr.IDBase):
        tenant_id: Mapped[int]
        name: Mapped[str]

    class Current(fr.ContextNamespace):
        tenant_id: fr.ContextParam[int]

    class ScopeAuthorClauses(fr.ClauseNamespace):
        model = ScopeAuthor

        default_scope = fr.where_clause(ScopeAuthor.tenant_id == Current.tenant_id)

    class ScopeBook(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey(ScopeAuthor.id))

    class ScopeAuthorSchema(fr.IDSchema):
        tenant_id: int
        name: str

    class ScopeBookSchema(fr.IDSchema):
        title: str
        author_id: fr.IDRef[ScopeAuthor]

    async def bind_tenant(tenant: Annotated[int, Header(alias="x-tenant-id")]):
        with Current.tenant_id.bind(tenant_id=tenant):
            yield

    @fr.include_view(client.app)
    class ScopeAuthorView(fr.AsyncRestView):
        prefix = "/scope-authors"
        model = ScopeAuthor
        schema = ScopeAuthorSchema
        scope = fr.clauses.UNSCOPED

    @fr.include_view(client.app)
    class ScopeBookView(fr.AsyncRestView):
        prefix = "/scope-books"
        model = ScopeBook
        schema = ScopeBookSchema
        dependencies = [Depends(bind_tenant)]

    create_tables()

    mine = client.post("/scope-authors/", json={"tenant_id": 1, "name": "mine"}).json()[
        "id"
    ]
    theirs = client.post(
        "/scope-authors/", json={"tenant_id": 2, "name": "theirs"}
    ).json()["id"]

    t1 = {"x-tenant-id": "1"}
    client.post("/scope-books/", json={"title": "ok", "author_id": mine}, headers=t1)
    response = client.post(
        "/scope-books/",
        json={"title": "cross", "author_id": theirs},
        headers=t1,
        assert_status_code=404,
    )
    assert "author_id" in response.json()["detail"]


def test_async_bind_dependency_reaches_sync_endpoints(sync_db):
    engine, _make_session = sync_db
    """A def endpoint runs in the threadpool with a COPY of the request
    task's context, so a bind set by an async-generator dependency reaches
    it; this pins the must-be-async-def rule for sync views too."""
    from fastapi import FastAPI

    from fastapi_restly.testing import RestlyTestClient

    class SyncScopedNote(fr.IDBase):
        tenant_id: Mapped[int]
        name: Mapped[str]

    class Current(fr.ContextNamespace):
        tenant_id: fr.ContextParam[int]

    class SyncScopedNoteClauses(fr.ClauseNamespace):
        model = SyncScopedNote

        default_scope = fr.where_clause(SyncScopedNote.tenant_id == Current.tenant_id)

    class SyncScopedNoteSchema(fr.IDSchema):
        tenant_id: int
        name: str

    async def bind_tenant(tenant: Annotated[int, Header(alias="x-tenant-id")]):
        with Current.tenant_id.bind(tenant_id=tenant):
            yield

    client = RestlyTestClient(FastAPI())

    @fr.include_view(client.app)
    class SyncScopedNoteView(fr.RestView):
        prefix = "/sync-notes"
        model = SyncScopedNote
        schema = SyncScopedNoteSchema
        dependencies = [Depends(bind_tenant)]

    fr.DataclassBase.metadata.create_all(engine)

    t1 = {"x-tenant-id": "1"}
    t2 = {"x-tenant-id": "2"}
    mine = client.post(
        "/sync-notes/", json={"tenant_id": 1, "name": "mine"}, headers=t1
    ).json()
    client.post("/sync-notes/", json={"tenant_id": 2, "name": "theirs"}, headers=t2)

    listed = client.get("/sync-notes/", headers=t1).json()
    assert [row["name"] for row in listed["data"]] == ["mine"]
    client.get(f"/sync-notes/{mine['id']}", headers=t2, assert_status_code=404)


# ---------------------------------------------------------------------------
# programmatic coverage: sync flavor, fallbacks, fail-loud paths
# ---------------------------------------------------------------------------


class _SyncBase(DeclarativeBase):
    pass


class SyncRow(_SyncBase):
    __tablename__ = "scope_sync_row"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int]


class _SyncContext(fr.ContextNamespace):
    tenant_id: fr.ContextParam[int]


class SyncRowClauses(fr.ClauseNamespace):
    model = SyncRow

    default_scope = fr.where_clause(SyncRow.tenant_id == _SyncContext.tenant_id)


class _SyncRowSchema(fr.IDSchema):
    tenant_id: int


class _SyncRowView(fr.RestView):
    prefix = "/sync-rows"
    model = SyncRow
    schema = _SyncRowSchema


@pytest.fixture
def sync_session():
    engine = create_engine("sqlite://")
    _SyncBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([SyncRow(id=1, tenant_id=1), SyncRow(id=2, tenant_id=2)])
        session.flush()
        yield session
    engine.dispose()


def test_sync_view_reads_through_default_scope(sync_session):
    view = _SyncRowView()
    view.session = sync_session
    with _SyncContext.tenant_id.bind(tenant_id=1):
        assert view.get_one(1).id == 1
        with pytest.raises(NotFound):
            view.get_one(2)


def test_sync_handlers_take_a_per_read_scope(sync_session):
    view = _SyncRowView()
    view.session = sync_session
    with _SyncContext.tenant_id.bind(tenant_id=1):
        # a clause replaces the view scope for that read only
        other = fr.where_clause(SyncRow.tenant_id == 2)
        assert view.handle_get_one(2, scope=other).id == 2
        with pytest.raises(NotFound):
            view.handle_get_one(1, scope=other)
        assert view.handle_get_one(1).id == 1

        # UNSCOPED reads past the default, in the one loud spelling
        assert view.handle_get_one(2, scope=fr.clauses.UNSCOPED).id == 2
        ids = {
            r.id for r in view.handle_get_many({}, scope=fr.clauses.UNSCOPED).objects
        }
        assert ids == {1, 2}
        assert {r.id for r in view.handle_get_many({}).objects} == {1}

        # the per-read scope is gone once the call is, on error too
        with pytest.raises(NotFound):
            view.handle_get_one(3, scope=fr.clauses.UNSCOPED)
        with pytest.raises(NotFound):
            view.get_one(2)

        # a raw expression is the same loud configuration error as on `scope`
        with pytest.raises(RestlyConfigurationError, match="per-read scope"):
            view.handle_get_one(1, scope=SyncRow.tenant_id == 2)  # type: ignore[arg-type]


def test_per_read_scope_reaches_a_get_one_override(sync_session):
    """The scope is set around the domain op, so an override of
    ``get_one(self, id)`` keeps its signature and still reads the surface
    the route asked for."""
    seen: list[int] = []

    class _Overriding(_SyncRowView):
        def get_one(self, id):
            obj = super().get_one(id)
            seen.append(obj.id)
            return obj

    view = _Overriding()
    view.session = sync_session
    with _SyncContext.tenant_id.bind(tenant_id=1):
        assert view.handle_get_one(2, scope=fr.clauses.UNSCOPED).id == 2
        with pytest.raises(NotFound):
            view.handle_get_one(2)
    assert seen == [2]


def test_apply_clauses_accepts_unscoped_as_nothing():
    stmt = fr.apply_clauses(select(SyncRow), fr.clauses.UNSCOPED)
    assert stmt.whereclause is None

    other = fr.where_clause(SyncRow.tenant_id == 2)
    stmt = fr.apply_clauses(select(SyncRow), other, fr.clauses.UNSCOPED)
    assert str(stmt.whereclause) == "scope_sync_row.tenant_id = :tenant_id_1"


def test_apply_scope_receives_the_resolved_scope(sync_session):
    """The seam sees one settled answer per read: the per-read scope, else
    the view's, else the model default, with UNSCOPED for none."""
    seen: list[object] = []

    class _Recording(_SyncRowView):
        def apply_scope(self, query, scope):
            seen.append(scope)
            return super().apply_scope(query, scope)

    other = fr.where_clause(SyncRow.tenant_id == 2)

    class _Pinned(_Recording):
        scope = other

    class _OptedOut(_Recording):
        scope = fr.clauses.UNSCOPED

    with _SyncContext.tenant_id.bind(tenant_id=1):
        for view_cls, expected in [
            (_Recording, SyncRowClauses.default_scope),
            (_Pinned, other),
            (_OptedOut, fr.clauses.UNSCOPED),
        ]:
            view = view_cls()
            view.session = sync_session
            view.handle_get_many({})
            assert seen[-1] is expected
            view.handle_get_one(1, scope=fr.clauses.UNSCOPED)
            assert seen[-1] is fr.clauses.UNSCOPED
            view.handle_get_one(2, scope=other)
            assert seen[-1] is other


def test_a_sync_base_class_stacks_a_floor_in_apply_scope(sync_session):
    floor = fr.where_clause(SyncRow.tenant_id == _SyncContext.tenant_id)

    class _Floored(_SyncRowView):
        scope = fr.clauses.UNSCOPED

        def apply_scope(self, query, scope):
            return fr.apply_clauses(query, floor, scope)

    view = _Floored()
    view.session = sync_session
    with _SyncContext.tenant_id.bind(tenant_id=1):
        # UNSCOPED on the view, UNSCOPED per read: the floor still holds
        assert {r.id for r in view.handle_get_many({}).objects} == {1}
        ids = {
            r.id for r in view.handle_get_many({}, scope=fr.clauses.UNSCOPED).objects
        }
        assert ids == {1}
        with pytest.raises(NotFound):
            view.handle_get_one(2, scope=fr.where_clause(SyncRow.id == 2))
        assert view.handle_get_one(1).id == 1


def test_unbound_scope_raises_the_teaching_error(sync_session):
    view = _SyncRowView()
    view.session = sync_session
    with pytest.raises(TypeError, match="missing bound values"):
        view.get_one(1)


def test_scope_resolution_falls_back_from_view_to_model_to_none():
    view = _SyncRowView()
    assert view._resolved_scope() is SyncRowClauses.default_scope

    other = fr.where_clause(SyncRow.tenant_id == 0)

    class _PinnedView(_SyncRowView):
        scope = other

    assert _PinnedView()._resolved_scope() is other

    class _OptedOutView(_SyncRowView):
        scope = fr.clauses.UNSCOPED

    assert _OptedOutView()._resolved_scope() is None

    class _Bare(_SyncBase):
        __tablename__ = "scope_sync_bare"
        C = "not a namespace"  # unrelated attribute; no clause namespace

        id: Mapped[int] = mapped_column(primary_key=True)

    class _BareView(fr.RestView):
        prefix = "/bare"
        model = _Bare
        schema = fr.IDSchema

    assert _BareView()._resolved_scope() is None


def test_defining_build_query_fails_at_class_definition():
    # build_query is removed; a definition would be dead code, and dead
    # visibility filtering is a security hole
    with pytest.raises(RestlyConfigurationError, match="build_query"):

        class _LegacyView(_SyncRowView):
            def build_query(self):
                return None

    class _LegacyMixin:
        def build_query(self):
            return None

    with pytest.raises(RestlyConfigurationError, match="_LegacyMixin"):

        class _MixedView(_LegacyMixin, _SyncRowView):
            pass


def test_non_clause_scope_is_a_loud_configuration_error(sync_session):
    # declared form: rejected as the class is defined
    with pytest.raises(RestlyConfigurationError, match="where_clause"):

        class _BrokenView(_SyncRowView):
            scope = SyncRow.tenant_id == 1  # forgot where_clause()

    # post-definition assignment: the read-time backstop catches it
    view = _SyncRowView()
    view.session = sync_session
    view.scope = SyncRow.tenant_id == 1  # type: ignore[assignment]
    with pytest.raises(RestlyConfigurationError, match="where_clause"):
        view.get_one(1)


def test_idref_resolution_applies_default_scope_sync(sync_session):
    class _RefSchema(fr.BaseSchema):
        rows: list[fr.IDRef[SyncRow]]

    with _SyncContext.tenant_id.bind(tenant_id=1):
        resolved = _resolve_ids_to_sqlalchemy_objects(
            sync_session, _RefSchema(rows=[1])
        )
        assert [row.id for row in resolved["rows"]] == [1]

        with pytest.raises(NotFound, match="rows"):
            _resolve_ids_to_sqlalchemy_objects(sync_session, _RefSchema(rows=[1, 2]))


# ---------------------------------------------------------------------------
# the predicate half: transforms are dropped, join-dependent wheres are loud
# ---------------------------------------------------------------------------


class SyncRanked(_SyncBase):
    __tablename__ = "scope_sync_ranked"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int]
    rank: Mapped[int]


class SyncSide(_SyncBase):
    __tablename__ = "scope_sync_side"

    id: Mapped[int] = mapped_column(primary_key=True)
    ranked_id: Mapped[int] = mapped_column(ForeignKey(SyncRanked.id))


def _by_rank(stmt: Select) -> Select:
    return stmt.order_by(SyncRanked.rank)


class SyncRankedClauses(fr.ClauseNamespace):
    model = SyncRanked

    owned_by_tenant = fr.where_clause(SyncRanked.tenant_id == _SyncContext.tenant_id)
    by_rank = fr.transform_clause(_by_rank)  # material is fine; a scope is not
    default_scope = owned_by_tenant


def test_transform_carrying_default_scope_is_rejected():
    # an existence probe cannot honor a transform, so a scope carrying one
    # is refused outright instead of silently half-applied
    with pytest.raises(TypeError, match="WhereClause"):

        class _OrderedClauses(fr.ClauseNamespace):
            model = SyncRanked
            default_scope = fr.combine(
                SyncRankedClauses.owned_by_tenant, SyncRankedClauses.by_rank
            )

    with pytest.raises(TypeError, match="WhereClause"):
        fr.RefExists(
            SyncRanked,
            scope=fr.combine(
                SyncRankedClauses.owned_by_tenant, SyncRankedClauses.by_rank
            ),
        )


def test_join_dependent_reference_scope_fails_loudly(sync_session):
    # a predicate on a table the probe does not select from would be a
    # cartesian product; the table validation rejects it and points at EXISTS
    class _JoinSchema(fr.BaseSchema):
        ranked_id: Annotated[
            int,
            fr.RefExists(SyncRanked, scope=fr.where_clause(SyncSide.id.is_not(None))),
        ]

    with pytest.raises(TypeError, match="not in the statement"):
        _check_ref_exists(sync_session, SyncRanked, _JoinSchema(ranked_id=1))


def test_ref_exists_rejects_a_non_clause_scope():
    with pytest.raises(TypeError, match="where_clause"):
        fr.RefExists(SyncRow, scope=SyncRow.tenant_id == 1)
    with pytest.raises(TypeError, match="ContextParam"):

        class _SlotContext(fr.ContextNamespace):
            x: fr.ContextParam[int]

        fr.RefExists(SyncRow, scope=_SlotContext.x)
    with pytest.raises(TypeError, match="WhereClause"):
        fr.RefExists(SyncRow, scope=fr.transform_clause(_by_rank))
    # None says nothing: a variable that happens to be None must not
    # silently unscope; the escape is the loud word
    with pytest.raises(TypeError, match="UNSCOPED"):
        fr.RefExists(SyncRow, scope=None)


# ---------------------------------------------------------------------------
# fail-loud guards: a scope that cannot filter must not exist quietly
# ---------------------------------------------------------------------------


def test_default_scope_must_be_a_where_clause():
    class _Plain(_SyncBase):
        __tablename__ = "scope_sync_plain"

        id: Mapped[int] = mapped_column(primary_key=True)

    with pytest.raises(TypeError, match="WhereClause"):

        class _TransformOnly(fr.ClauseNamespace):
            model = _Plain
            default_scope = fr.transform_clause(_by_rank)

    class _ZContext(fr.ContextNamespace):
        z: fr.ContextParam[int]

    with pytest.raises(TypeError, match="WhereClause"):

        class _SlotOnly(fr.ClauseNamespace):
            model = _Plain
            default_scope = _ZContext.z

    with pytest.raises(TypeError, match="UNSCOPED"):

        class _NoneScope(fr.ClauseNamespace):
            model = _Plain
            default_scope = None


def test_subclass_inherits_default_scope_along_the_mro():
    class Animal(_SyncBase):
        __tablename__ = "scope_sync_animal"

        id: Mapped[int] = mapped_column(primary_key=True)
        kind: Mapped[str]
        tenant_id: Mapped[int]
        __mapper_args__ = {"polymorphic_on": "kind", "polymorphic_identity": "animal"}

    class Dog(Animal):
        __mapper_args__ = {"polymorphic_identity": "dog"}

    class Cat(Animal):
        __mapper_args__ = {"polymorphic_identity": "cat"}

    class Bird(Animal):
        __mapper_args__ = {"polymorphic_identity": "bird"}

    class Wolf(Dog):
        __mapper_args__ = {"polymorphic_identity": "wolf"}

    class AnimalClauses(fr.ClauseNamespace):
        model = Animal
        default_scope = fr.where_clause(Animal.tenant_id == _SyncContext.tenant_id)

    # no namespace of its own: the base model's scope applies
    assert _default_scope(Bird) is AnimalClauses.default_scope

    # a namespace silent about default_scope leaves the inherited scope
    # in force; a subclass can never drop the base scope by omission
    class DogClauses(fr.ClauseNamespace):
        model = Dog
        is_dog = fr.where_clause(Dog.kind == "dog")

    assert _default_scope(Dog) is AnimalClauses.default_scope

    # the explicit opt-out
    class CatClauses(fr.ClauseNamespace):
        model = Cat
        default_scope = fr.clauses.UNSCOPED

    assert _default_scope(Cat) is None

    # a namespace subclass keeps its parent namespace's declaration:
    # CatClauses' UNSCOPED stops the walk before Animal's scope applies
    class WolfClauses(CatClauses):
        model = Wolf
        is_wolf = fr.where_clause(Wolf.kind == "wolf")

    assert _default_scope(Wolf) is None


def test_post_hoc_default_scope_corruption_is_loud():
    class _Row(_SyncBase):
        __tablename__ = "scope_sync_corrupt"

        id: Mapped[int] = mapped_column(primary_key=True)
        tenant_id: Mapped[int]

    class _RowClauses(fr.ClauseNamespace):
        model = _Row
        default_scope = fr.where_clause(_Row.tenant_id == _SyncContext.tenant_id)

    _RowClauses.default_scope = _Row.tenant_id == 1  # forgot where_clause()
    with pytest.raises(TypeError, match="WhereClause"):
        _default_scope(_Row)


def test_buried_must_exist_marker_is_rejected():
    with pytest.raises(RestlyConfigurationError, match="unchecked"):

        class _ListRefSchema(fr.BaseSchema):
            row_ids: list[fr.MustExist[int, SyncRow]]


class SyncWeird(_SyncBase):
    __tablename__ = "scope_sync_weird"

    code: Mapped[int] = mapped_column(primary_key=True)  # pk not named `id`
    tenant_id: Mapped[int]


class SyncWeirdClauses(fr.ClauseNamespace):
    model = SyncWeird

    default_scope = fr.where_clause(SyncWeird.tenant_id == _SyncContext.tenant_id)


def test_scoped_resolution_uses_the_mapper_pk(sync_session):
    sync_session.add(SyncWeird(code=7, tenant_id=1))
    sync_session.flush()

    class _WeirdSchema(fr.BaseSchema):
        thing: fr.IDSchema[SyncWeird]

    with _SyncContext.tenant_id.bind(tenant_id=1):
        resolved = _resolve_ids_to_sqlalchemy_objects(
            sync_session, _WeirdSchema(thing=7)
        )
        assert resolved["thing"].code == 7
    with _SyncContext.tenant_id.bind(tenant_id=2):
        with pytest.raises(NotFound, match="thing"):
            _resolve_ids_to_sqlalchemy_objects(sync_session, _WeirdSchema(thing=7))
