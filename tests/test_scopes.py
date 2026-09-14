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
from sqlalchemy import ForeignKey, Select, create_engine, event, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    ORMExecuteState,
    Session,
    mapped_column,
    with_loader_criteria,
)

import fastapi_restly as fr
from fastapi_restly.clauses._scopes import _default_scope
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

        # a route declaring `query_params` takes the listing grammar
        @fr.get("/trash")
        async def trash(self, query_params):
            result = await self.handle_get_many(
                query_params, scope=ScopeNoteClauses.trashed
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

    # the same filter, sort and page grammar as GET /, guarded the same way
    post({"tenant_id": 1, "name": "also gone", "deleted": True}, t1)
    filtered = client.get("/scope-notes/trash?name=gone", headers=t1).json()
    assert [r["name"] for r in filtered["data"]] == ["gone"]
    ordered = client.get("/scope-notes/trash?sort=-name", headers=t1).json()
    assert [r["name"] for r in ordered["data"]] == ["gone", "also gone"]
    paged = client.get("/scope-notes/trash?page_size=1", headers=t1).json()
    assert paged["total_count"] == 2 and len(paged["data"]) == 1
    response = client.get(
        "/scope-notes/trash?bogus=1", headers=t1, assert_status_code=422
    )
    assert response.json()["detail"][0]["loc"] == ["query", "bogus"]
    # and the grammar is in the contract
    trash_params = {
        p["name"]
        for p in client.app.openapi()["paths"]["/scope-notes/trash"]["get"][
            "parameters"
        ]
    }
    assert {"name", "sort", "page", "page_size"} <= trash_params

    # the per-read escape is the same loud spelling as everywhere else
    everything = client.get("/scope-notes/everything", headers=t1).json()
    assert {r["name"] for r in everything["data"]} == {
        "live",
        "gone",
        "also gone",
        "other",
    }

    # restore loads through the trash surface: a live row is outside it
    client.post(
        f"/scope-notes/{live['id']}/restore", headers=t1, assert_status_code=404
    )
    restored = client.post(f"/scope-notes/{gone['id']}/restore", headers=t1).json()
    assert restored["deleted"] is False
    assert client.get(f"/scope-notes/{gone['id']}", headers=t1).json()["name"] == "gone"


def test_the_resolved_scope_and_the_reads_agree_on_the_rows_async(client):
    """The reads answer with the rows their scope names: the accessor's
    answer when a read names none (the model default or a declared view
    scope), and the clause it names instead (a per-read scope or
    UNSCOPED). One route reports all four answers for one surface."""

    class AgreeRow(fr.IDBase):
        tenant_id: Mapped[int]
        name: Mapped[str]
        deleted: Mapped[bool] = mapped_column(default=False)

    class Current(fr.ContextNamespace):
        tenant_id: fr.ContextParam[int]

    class AgreeRowClauses(fr.ClauseNamespace):
        model = AgreeRow

        is_deleted = fr.where_clause(AgreeRow.deleted.is_(True))
        owned_by_tenant = fr.where_clause(AgreeRow.tenant_id == Current.tenant_id)
        visible = fr.all_of(owned_by_tenant, fr.none_of(is_deleted))
        trashed = fr.all_of(owned_by_tenant, is_deleted)
        default_scope = visible

    class AgreeRowSchema(fr.IDSchema):
        tenant_id: int
        name: str
        deleted: bool = False

    surfaces = {
        "view": None,  # the view's own scope, declared or inherited
        "trash": AgreeRowClauses.trashed,  # a per-read scope
        "all": fr.clauses.UNSCOPED,  # the explicit escape
    }

    async def bind_tenant(tenant: Annotated[int, Header(alias="x-tenant-id")]):
        with Current.tenant_id.bind(tenant_id=tenant):
            yield

    async def agreement(view, scope):
        """The four answers for one scope: accessor, listing, retrieve, count."""
        resolved = fr.resolve_scope(view) if scope is None else scope
        query = fr.apply_clauses(select(AgreeRow), resolved)
        listing = await view.handle_get_many({}, scope=scope)
        retrieved = []
        for row_id in (await view.session.scalars(select(AgreeRow.id))).all():
            try:
                retrieved.append((await view.get_one(row_id, scope=scope)).id)
            except fr.exc.NotFound:
                pass
        return {
            "accessor": sorted(
                row.id for row in (await view.session.scalars(query)).all()
            ),
            "listed": sorted(obj.id for obj in listing.objects),
            "retrieved": sorted(retrieved),
            "count": await view.count(query),
            "total": listing.total_count,
        }

    @fr.include_view(client.app)
    class AgreeRowView(fr.AsyncRestView):
        prefix = "/agree-rows"
        model = AgreeRow
        schema = AgreeRowSchema
        dependencies = [Depends(bind_tenant)]

        @fr.get("/agree")
        async def agree(self) -> dict:
            surface = self.request.query_params["surface"]
            return await agreement(self, surfaces[surface])

    @fr.include_view(client.app)
    class AgreeTrashView(fr.AsyncRestView):
        prefix = "/agree-trash"
        model = AgreeRow
        schema = AgreeRowSchema
        dependencies = [Depends(bind_tenant)]
        scope = AgreeRowClauses.trashed

        @fr.get("/agree")
        async def agree(self) -> dict:
            surface = self.request.query_params["surface"]
            return await agreement(self, surfaces[surface])

    create_tables()

    t1 = {"x-tenant-id": "1"}

    def post(payload, headers=t1):
        return client.post("/agree-rows/", json=payload, headers=headers).json()["id"]

    live = post({"tenant_id": 1, "name": "live"})
    gone = post({"tenant_id": 1, "name": "gone", "deleted": True})
    other = post({"tenant_id": 2, "name": "other"}, {"x-tenant-id": "2"})

    for path, surface, expected in (
        ("/agree-rows/agree", "view", {live}),  # the model's default_scope
        ("/agree-trash/agree", "view", {gone}),  # the view's declared scope
        ("/agree-rows/agree", "trash", {gone}),  # a per-read scope replaces it
        ("/agree-rows/agree", "all", {live, gone, other}),  # the explicit escape
    ):
        payload = client.get(f"{path}?surface={surface}", headers=t1).json()
        assert set(payload["accessor"]) == expected
        assert payload["accessor"] == payload["listed"] == payload["retrieved"]
        assert payload["count"] == payload["total"] == len(expected)


def test_a_count_route_counts_the_scoped_select_under_the_listing_grammar(client):
    """A count route applies the client's filters to the view's scoped
    select, so it totals the rows the listing pages without fetching them."""

    class CountRow(fr.IDBase):
        tenant_id: Mapped[int]
        name: Mapped[str]

    class Current(fr.ContextNamespace):
        tenant_id: fr.ContextParam[int]

    class CountRowClauses(fr.ClauseNamespace):
        model = CountRow

        default_scope = fr.where_clause(CountRow.tenant_id == Current.tenant_id)

    class CountRowSchema(fr.IDSchema):
        tenant_id: int
        name: str

    async def bind_tenant(tenant: Annotated[int, Header(alias="x-tenant-id")]):
        with Current.tenant_id.bind(tenant_id=tenant):
            yield

    @fr.include_view(client.app)
    class CountRowView(fr.AsyncRestView):
        prefix = "/count-rows"
        model = CountRow
        schema = CountRowSchema
        dependencies = [Depends(bind_tenant)]

        # deliberately not named `count`: that name is the read seam below
        @fr.get("/count")
        async def total(self, query_params) -> int:
            await self.authorize(fr.Action.GET_MANY)
            query = fr.apply_clauses(select(CountRow), fr.resolve_scope(self))
            return await self.count(self.apply_query_params(query, query_params))

    create_tables()

    t1 = {"x-tenant-id": "1"}
    for name in ("alpha", "beta", "beta"):
        client.post("/count-rows/", json={"tenant_id": 1, "name": name}, headers=t1)
    client.post(
        "/count-rows/",
        json={"tenant_id": 2, "name": "beta"},
        headers={"x-tenant-id": "2"},
    )

    # the scope holds: another tenant's row is outside the total
    assert client.get("/count-rows/count", headers=t1).json() == 3
    assert client.get("/count-rows/", headers=t1).json()["total_count"] == 3

    # the client's filter narrows the count; paging does not change it
    assert client.get("/count-rows/count?name=beta", headers=t1).json() == 2
    assert client.get("/count-rows/count?page_size=1", headers=t1).json() == 3

    # and the route takes the same unknown-key guard as GET /
    client.get("/count-rows/count?bogus=1", headers=t1, assert_status_code=422)


def test_a_session_rule_holds_under_every_scope_and_reference_check(client):
    """A rule that must hold whatever a view or a route named is not a
    scope: a ``do_orm_execute`` listener adds it with SQLAlchemy's
    ``with_loader_criteria``. Restly's reads and reference checks are ORM
    statements, so the rule reaches a view that opted out, a route that
    named its own scope, and a ``MustExist`` check alike."""

    class FloorNote(fr.IDBase):
        tenant_id: Mapped[int]
        name: Mapped[str]
        deleted: Mapped[bool] = mapped_column(default=False)

    class FloorRef(fr.IDBase):
        note_id: Mapped[int] = mapped_column(ForeignKey(FloorNote.id))

    class Current(fr.ContextNamespace):
        tenant_id: fr.ContextParam[int]

    class FloorNoteClauses(fr.ClauseNamespace):
        model = FloorNote

        is_deleted = fr.where_clause(FloorNote.deleted.is_(True))
        default_scope = fr.none_of(is_deleted)

    class FloorNoteSchema(fr.IDSchema):
        tenant_id: int
        name: str
        deleted: bool = False

    class FloorRefSchema(fr.IDSchema):
        note_id: fr.MustExist[int, FloorNote]

    async def bind_tenant(tenant: Annotated[int, Header(alias="x-tenant-id")]):
        with Current.tenant_id.bind(tenant_id=tenant):
            yield

    def restrict(state: ORMExecuteState) -> None:
        if not state.is_select or state.is_column_load or state.is_relationship_load:
            return
        if not any(m.class_ is FloorNote for m in state.all_mappers):
            return
        tenant = Current.tenant_id()
        state.statement = state.statement.options(
            with_loader_criteria(
                FloorNote, lambda cls: cls.tenant_id == tenant, include_aliases=True
            )
        )

    class TenantBase(fr.AsyncRestView):
        dependencies = [Depends(bind_tenant)]

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

    @fr.include_view(client.app)
    class FloorRefView(TenantBase):
        prefix = "/floor-refs"
        model = FloorRef
        schema = FloorRefSchema

    create_tables()

    t1 = {"x-tenant-id": "1"}
    t2 = {"x-tenant-id": "2"}

    def post(payload, headers):
        return client.post("/floor-notes/", json=payload, headers=headers).json()

    names = lambda response: [r["name"] for r in response.json()["data"]]  # noqa: E731

    event.listen(Session, "do_orm_execute", restrict)
    try:
        live = post({"tenant_id": 1, "name": "live"}, t1)
        gone = post({"tenant_id": 1, "name": "gone", "deleted": True}, t1)
        theirs = post({"tenant_id": 2, "name": "theirs", "deleted": True}, t2)

        # the default read: the model default under the rule
        assert names(client.get("/floor-notes/", headers=t1)) == ["live"]

        # a route's own scope replaces the default; the rule still holds
        assert names(client.get("/floor-notes/trash", headers=t1)) == ["gone"]
        assert names(client.get("/floor-notes/trash", headers=t2)) == ["theirs"]

        # a view that opted out of the default scope is still tenant-bound
        assert names(client.get("/all-floor-notes/", headers=t1)) == ["live", "gone"]
        client.get(
            f"/all-floor-notes/{theirs['id']}", headers=t1, assert_status_code=404
        )
        assert (
            client.get(f"/all-floor-notes/{gone['id']}", headers=t1).json()["name"]
            == "gone"
        )

        # the reference check is an ORM statement: the other tenant's row
        # does not exist for a write
        client.post("/floor-refs/", json={"note_id": live["id"]}, headers=t1)
        client.post(
            "/floor-refs/",
            json={"note_id": live["id"]},
            headers=t2,
            assert_status_code=404,
        )
    finally:
        event.remove(Session, "do_orm_execute", restrict)


# ---------------------------------------------------------------------------
# reference checks: default_scope, per-field override, explicit escape
# ---------------------------------------------------------------------------


def test_a_sync_route_takes_the_listing_grammar(sync_db):
    """RestView parity: a def route declaring ``query_params`` is typed and
    guarded like ``GET /``, and a subclass's copy of it is guarded once."""
    engine, _make_session = sync_db
    from fastapi import FastAPI

    from fastapi_restly.testing import RestlyTestClient

    class SyncListedNote(fr.IDBase):
        name: Mapped[str]
        deleted: Mapped[bool] = mapped_column(default=False)

    class SyncListedNoteClauses(fr.ClauseNamespace):
        model = SyncListedNote

        is_deleted = fr.where_clause(SyncListedNote.deleted.is_(True))
        default_scope = fr.none_of(is_deleted)

    class SyncListedNoteSchema(fr.IDSchema):
        name: str
        deleted: bool = False

    guard_calls: list[str] = []

    client = RestlyTestClient(FastAPI())

    class SyncListedNoteView(fr.RestView):
        prefix = "/sync-listed"
        model = SyncListedNote
        schema = SyncListedNoteSchema

        def _reject_unknown_query_params(self):
            guard_calls.append(self.request.url.path)
            super()._reject_unknown_query_params()

        @fr.get("/trash")
        def trash(self, query_params):
            result = self.handle_get_many(
                query_params, scope=SyncListedNoteClauses.is_deleted
            )
            return self.to_response(result, fr.ResponseShape.LISTING)

    @fr.include_view(client.app)
    class SyncListedNoteSubView(SyncListedNoteView):
        pass

    fr.DataclassBase.metadata.create_all(engine)

    client.post("/sync-listed/", json={"name": "live"})
    client.post("/sync-listed/", json={"name": "gone", "deleted": True})
    client.post("/sync-listed/", json={"name": "also gone", "deleted": True})

    assert [r["name"] for r in client.get("/sync-listed/").json()["data"]] == ["live"]
    trash = client.get("/sync-listed/trash?sort=name").json()
    assert [r["name"] for r in trash["data"]] == ["also gone", "gone"]
    assert [
        r["name"] for r in client.get("/sync-listed/trash?name=gone").json()["data"]
    ] == ["gone"]
    client.get("/sync-listed/trash?bogus=1", assert_status_code=422)
    client.get("/sync-listed/?bogus=1", assert_status_code=422)
    # one guard call per request, on the inherited copy too
    assert guard_calls.count("/sync-listed/trash") == 3
    assert guard_calls.count("/sync-listed/") == 2


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

        # a scope override is one call's argument; the next read is back
        # on the view scope, after an error too
        with pytest.raises(NotFound):
            view.handle_get_one(3, scope=fr.clauses.UNSCOPED)
        with pytest.raises(NotFound):
            view.get_one(2)

        # a raw expression is the same loud configuration error as on `scope`
        with pytest.raises(RestlyConfigurationError, match="per-read scope"):
            view.handle_get_one(1, scope=SyncRow.tenant_id == 2)  # type: ignore[arg-type]


def test_domain_ops_take_a_scope_directly(sync_session):
    """``get_one`` / ``get_many`` accept ``scope=`` themselves, so a custom
    action can load another surface and make its own auth decision instead
    of inheriting read-auth from ``handle_get_one``."""
    view = _SyncRowView()
    view.session = sync_session
    with _SyncContext.tenant_id.bind(tenant_id=1):
        other = fr.where_clause(SyncRow.tenant_id == 2)
        assert view.get_one(2, scope=other).id == 2
        with pytest.raises(NotFound):
            view.get_one(1, scope=other)
        ids = {r.id for r in view.get_many({}, scope=fr.clauses.UNSCOPED).objects}
        assert ids == {1, 2}


def test_per_read_scope_passes_through_a_get_one_override(sync_session):
    """The handlers always forward ``scope=``, so an override declares the
    parameter, passes it on, and reads the surface the route asked for."""
    seen: list[int] = []

    class _Overriding(_SyncRowView):
        def get_one(self, id, *, scope=None):
            obj = super().get_one(id, scope=scope)
            seen.append(obj.id)
            return obj

    view = _Overriding()
    view.session = sync_session
    with _SyncContext.tenant_id.bind(tenant_id=1):
        assert view.handle_get_one(2, scope=fr.clauses.UNSCOPED).id == 2
        with pytest.raises(NotFound):
            view.handle_get_one(2)
    assert seen == [2]


def test_a_scope_unaware_get_one_override_fails_loudly(sync_session):
    """An override without the ``scope`` parameter breaks on the first
    handler read, not silently on the first route that names a scope."""

    class _ScopeUnaware(_SyncRowView):
        def get_one(self, id):
            return super().get_one(id)

    view = _ScopeUnaware()
    view.session = sync_session
    with _SyncContext.tenant_id.bind(tenant_id=1):
        with pytest.raises(TypeError, match="scope"):
            view.handle_get_one(1)


def test_apply_clauses_accepts_unscoped_as_nothing():
    stmt = fr.apply_clauses(select(SyncRow), fr.clauses.UNSCOPED)
    assert stmt.whereclause is None

    other = fr.where_clause(SyncRow.tenant_id == 2)
    stmt = fr.apply_clauses(select(SyncRow), other, fr.clauses.UNSCOPED)
    assert str(stmt.whereclause) == "scope_sync_row.tenant_id = :tenant_id_1"


@pytest.mark.parametrize("op", [fr.all_of, fr.any_of, fr.none_of])
def test_composed_unscoped_controls_sync_reads_and_references(sync_session, op):
    scope = op(fr.clauses.UNSCOPED)

    class ComposedView(_SyncRowView):
        pass

    ComposedView.scope = scope
    view = ComposedView()
    view.session = sync_session

    class RefSchema(fr.BaseSchema):
        row_id: Annotated[int, fr.RefExists(SyncRow, scope=scope)]

    listed = view.handle_get_many({})
    if op is fr.none_of:
        assert listed.objects == []
        assert listed.total_count == 0
        with pytest.raises(NotFound):
            view.handle_get_one(1)
        with pytest.raises(NotFound):
            _check_ref_exists(sync_session, SyncRow, RefSchema(row_id=1))
    else:
        assert {row.id for row in listed.objects} == {1, 2}
        assert listed.total_count == 2
        assert view.handle_get_one(1).id == 1
        _check_ref_exists(sync_session, SyncRow, RefSchema(row_id=1))


def test_negated_unscoped_default_hides_async_reads_and_references(client):
    class HiddenRow(fr.IDBase):
        name: Mapped[str]

    class HiddenRowClauses(fr.ClauseNamespace):
        model = HiddenRow
        default_scope = fr.none_of(fr.clauses.UNSCOPED)

    class HiddenRowSchema(fr.IDSchema):
        name: str

    class RefRow(fr.IDBase):
        target_id: Mapped[int] = mapped_column(ForeignKey(HiddenRow.id))

    class RefRowSchema(fr.IDSchema):
        target_id: fr.MustExist[int]

    @fr.include_view(client.app)
    class HiddenRowView(fr.AsyncRestView):
        prefix = "/hidden-rows"
        model = HiddenRow
        schema = HiddenRowSchema

    @fr.include_view(client.app)
    class RefRowView(fr.AsyncRestView):
        prefix = "/ref-rows"
        model = RefRow
        schema = RefRowSchema

    create_tables()
    row = client.post("/hidden-rows/", json={"name": "hidden"}).json()
    listed = client.get("/hidden-rows/").json()
    assert listed["data"] == []
    assert listed["total_count"] == 0
    client.get(f"/hidden-rows/{row['id']}", assert_status_code=404)
    client.post("/ref-rows/", json={"target_id": row["id"]}, assert_status_code=404)


def test_a_view_scope_replaces_the_default_and_a_per_read_scope_replaces_both(
    sync_session,
):
    """Resolution per read: the per-read scope, else the view's, else the
    model default, with UNSCOPED for none. Pinned by what each read returns."""
    other = fr.where_clause(SyncRow.tenant_id == 2)

    class _Pinned(_SyncRowView):
        scope = other

    class _OptedOut(_SyncRowView):
        scope = fr.clauses.UNSCOPED

    with _SyncContext.tenant_id.bind(tenant_id=1):
        for view_cls, expected in [
            (_SyncRowView, {1}),  # the model default: the bound tenant
            (_Pinned, {2}),  # the view's own scope replaces it
            (_OptedOut, {1, 2}),  # UNSCOPED on the view reads past it
        ]:
            view = view_cls()
            view.session = sync_session
            assert {r.id for r in view.handle_get_many({}).objects} == expected
            # a per-read scope replaces whatever the view resolved to
            assert view.handle_get_one(2, scope=fr.clauses.UNSCOPED).id == 2
            assert view.handle_get_one(2, scope=other).id == 2
            with pytest.raises(NotFound):
                view.handle_get_one(1, scope=other)


def test_a_sync_session_rule_holds_under_unscoped_reads_and_references(sync_session):
    """The sync leg of the session rule: ``UNSCOPED`` on the view and per
    read, and an explicitly unscoped reference check, all stay under it."""

    def restrict(state: ORMExecuteState) -> None:
        if not state.is_select or state.is_column_load or state.is_relationship_load:
            return
        if not any(m.class_ is SyncRow for m in state.all_mappers):
            return
        tenant = _SyncContext.tenant_id()
        state.statement = state.statement.options(
            with_loader_criteria(
                SyncRow, lambda cls: cls.tenant_id == tenant, include_aliases=True
            )
        )

    class _OptedOut(_SyncRowView):
        scope = fr.clauses.UNSCOPED

    class RefSchema(fr.BaseSchema):
        row_id: Annotated[int, fr.RefExists(SyncRow, scope=fr.clauses.UNSCOPED)]

    view = _OptedOut()
    view.session = sync_session
    event.listen(Session, "do_orm_execute", restrict)
    try:
        with _SyncContext.tenant_id.bind(tenant_id=1):
            # UNSCOPED on the view, UNSCOPED per read: the rule still holds
            assert {r.id for r in view.handle_get_many({}).objects} == {1}
            ids = {
                r.id
                for r in view.handle_get_many({}, scope=fr.clauses.UNSCOPED).objects
            }
            assert ids == {1}
            with pytest.raises(NotFound):
                view.handle_get_one(2, scope=fr.where_clause(SyncRow.id == 2))
            assert view.handle_get_one(1).id == 1
            # an explicitly unscoped reference check is under the rule too
            _check_ref_exists(sync_session, SyncRow, RefSchema(row_id=1))
            with pytest.raises(NotFound):
                _check_ref_exists(sync_session, SyncRow, RefSchema(row_id=2))
    finally:
        event.remove(Session, "do_orm_execute", restrict)


def test_unbound_scope_raises_the_teaching_error(sync_session):
    view = _SyncRowView()
    view.session = sync_session
    with pytest.raises(TypeError, match="missing bound values"):
        view.get_one(1)


def test_scope_resolution_falls_back_from_view_to_model_to_unscoped():
    # a view that declares none reads through the model's default_scope,
    # and the answer is the same asked of the class or of an instance
    assert fr.resolve_scope(_SyncRowView) is SyncRowClauses.default_scope
    assert fr.resolve_scope(_SyncRowView()) is SyncRowClauses.default_scope

    other = fr.where_clause(SyncRow.tenant_id == 0)

    class _PinnedView(_SyncRowView):
        scope = other

    assert fr.resolve_scope(_PinnedView) is other

    class _OptedOutView(_SyncRowView):
        scope = fr.clauses.UNSCOPED

    assert fr.resolve_scope(_OptedOutView) is fr.clauses.UNSCOPED

    class _Bare(_SyncBase):
        __tablename__ = "scope_sync_bare"
        C = "not a namespace"  # unrelated attribute; no clause namespace

        id: Mapped[int] = mapped_column(primary_key=True)

    class _BareView(fr.RestView):
        prefix = "/bare"
        model = _Bare
        schema = fr.IDSchema

    assert fr.resolve_scope(_BareView) is fr.clauses.UNSCOPED

    # the model rung on its own: what every reference check applies
    assert fr.resolve_scope(SyncRow) is SyncRowClauses.default_scope
    assert fr.resolve_scope(_Bare) is fr.clauses.UNSCOPED


def test_a_criterion_narrows_inside_the_scope_and_cannot_widen_it(sync_session):
    """``get_one`` takes a predicate in place of the id. It is ANDed under
    the resolved scope, so it addresses another key inside what the view
    sees and never past it; only ``scope=`` changes what that is."""
    view = _SyncRowView()
    view.session = sync_session
    with _SyncContext.tenant_id.bind(tenant_id=1):
        # the natural-key shape: another column, the same scope and 404
        assert view.get_one(SyncRow.tenant_id == 1).id == 1

        # row 2 is another tenant's: naming it directly is still a 404
        with pytest.raises(NotFound):
            view.get_one(SyncRow.id == 2)

        # only scope= reaches it, and the criterion still narrows inside
        assert view.get_one(SyncRow.id == 2, scope=fr.clauses.UNSCOPED).id == 2
        with pytest.raises(NotFound):
            view.get_one(SyncRow.id == 3, scope=fr.clauses.UNSCOPED)

        # the handler adds read-auth on the predicate path like any other
        assert view.handle_get_one(SyncRow.id == 1).id == 1


def test_resolve_scope_rejects_a_target_that_is_neither_view_nor_model():
    with pytest.raises(TypeError, match="RestView"):
        fr.resolve_scope(SyncRowClauses)  # type: ignore[call-overload]


def test_a_per_read_scope_that_is_not_a_clause_is_rejected(sync_session):
    """The handlers forward ``scope=`` as given, so a raw expression is
    refused at the read instead of being applied as if it were a clause."""
    view = _SyncRowView()
    view.session = sync_session
    raw = SyncRow.tenant_id == 1
    with pytest.raises(
        fr.exc.RestlyConfigurationError, match="a per-read scope must be a Clause"
    ):
        view.get_one(1, scope=raw)  # type: ignore[arg-type]
    with pytest.raises(fr.exc.RestlyConfigurationError, match="wrap a raw expression"):
        view.get_many({}, scope=raw)  # type: ignore[arg-type]


def test_the_resolved_scope_and_the_reads_agree_on_the_rows_sync(sync_session):
    """The sync half of the agreement: the accessor, the listing, the
    retrieves that succeed and the count answer with the same rows on
    every rung."""

    class _ScopedView(_SyncRowView):
        scope = fr.where_clause(SyncRow.id == 2)

    def through_the_accessor(view, scope):
        resolved = fr.resolve_scope(view) if scope is None else scope
        query = fr.apply_clauses(select(SyncRow), resolved)
        return {row.id for row in view.session.scalars(query)}

    def through_retrieve(view, scope):
        found = set()
        for row_id in (1, 2):
            try:
                found.add(view.get_one(row_id, scope=scope).id)
            except NotFound:
                pass
        return found

    per_read = fr.where_clause(SyncRow.tenant_id == 2)
    with _SyncContext.tenant_id.bind(tenant_id=1):
        for view_cls, scope, expected in (
            (_SyncRowView, None, {1}),  # the model's default_scope
            (_ScopedView, None, {2}),  # the view's declared scope
            (_SyncRowView, per_read, {2}),  # a per-read scope replaces it
            (_SyncRowView, fr.clauses.UNSCOPED, {1, 2}),  # the explicit escape
        ):
            view = view_cls()
            view.session = sync_session
            listing = view.get_many({}, scope=scope)
            assert through_the_accessor(view, scope) == expected
            assert {obj.id for obj in listing.objects} == expected
            assert through_retrieve(view, scope) == expected
            assert listing.total_count == len(expected)


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


def test_a_namespace_without_a_model_is_a_plain_group():
    """A clause needs no model: a namespace that declares none validates
    its members and registers nothing."""

    class Shared(fr.ClauseNamespace):
        positive = fr.where_clause(SyncRow.id > 0)

    assert isinstance(Shared.positive, fr.WhereClause)
    with pytest.raises(TypeError, match="not a Clause"):

        class Bad(fr.ClauseNamespace):
            positive = SyncRow.id > 0


def test_a_namespace_base_composes_a_floor_into_default_scope():
    """The reference-check half of an application floor: a base namespace
    stacks the tenant clause under whatever its subclasses declare, and
    the registered default_scope is the composed clause."""

    class TenantClauses(fr.ClauseNamespace):
        def __init_subclass__(cls, **kwargs):
            model = vars(cls).get("model")
            if model is not None:
                floor = fr.where_clause(model.tenant_id == _SyncContext.tenant_id)
                declared = vars(cls).get("default_scope")
                cls.default_scope = (
                    floor if declared is None else fr.all_of(floor, declared)
                )
            super().__init_subclass__(**kwargs)

    class FlooredRow(_SyncBase):
        __tablename__ = "scope_sync_floored_row"

        id: Mapped[int] = mapped_column(primary_key=True)
        tenant_id: Mapped[int]
        deleted: Mapped[bool] = mapped_column(default=False)

    class FlooredRowClauses(TenantClauses):
        model = FlooredRow
        default_scope = fr.where_clause(FlooredRow.deleted.is_(False))

    scope = _default_scope(FlooredRow)
    assert scope is FlooredRowClauses.default_scope
    with _SyncContext.tenant_id.bind(tenant_id=1):
        rendered = str(scope.select(FlooredRow).whereclause)
    assert "tenant_id = :tenant_id" in rendered
    assert "deleted IS false" in rendered

    # a base can also declare the default itself; a subclass with a model
    # registers it, through the namespace MRO
    class DefaultingBase(fr.ClauseNamespace):
        default_scope = fr.where_clause(FlooredRow.deleted.is_(False))

    class OtherRow(_SyncBase):
        __tablename__ = "scope_sync_other_row"

        id: Mapped[int] = mapped_column(primary_key=True)

    class OtherRowClauses(DefaultingBase):
        model = OtherRow

    assert _default_scope(OtherRow) is DefaultingBase.default_scope
    assert OtherRowClauses.default_scope is DefaultingBase.default_scope


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
