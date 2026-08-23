"""The view scope and scoped reference checks.

A model's ``C.default_scope`` arms every view read (list, retrieve, count)
and every reference check on the model. A view's ``scope`` attribute
replaces that default; ``fr.clauses.UNSCOPED`` opts out explicitly. Reference
checks (``MustExist`` / ``RefExists`` / ``IDRef`` / ``IDSchema``) apply
only the predicate half of the clause, and ``RefExists(scope=...)``
overrides per field, with ``scope=None`` the explicit unscoped escape.
"""

from typing import Annotated

import pytest
from fastapi import Depends, Header
from sqlalchemy import ForeignKey, Select, create_engine
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

    current_tenant = fr.context_param("tenant_id", int)

    class ScopeItemClauses(fr.ClauseNamespace):
        model = ScopeItem

        is_deleted = fr.where_clause(ScopeItem.deleted.is_(True))
        owned_by_tenant = fr.where_clause(ScopeItem.tenant_id == current_tenant)
        visible = fr.all_of(owned_by_tenant, fr.none_of(is_deleted))
        trashed = fr.all_of(owned_by_tenant, is_deleted)
        default_scope = visible

    class ScopeItemSchema(fr.IDSchema):
        tenant_id: int
        name: str
        deleted: bool = False

    async def bind_tenant(tenant: Annotated[int, Header(alias="x-tenant-id")]):
        with current_tenant.bind(tenant_id=tenant):
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
        scope = ScopeItem.C.trashed

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

    current_tenant = fr.context_param("tenant_id", int)

    class ScopeDocClauses(fr.ClauseNamespace):
        model = ScopeDoc

        owned_by_tenant = fr.where_clause(ScopeDoc.tenant_id == current_tenant)
        default_scope = owned_by_tenant

    class ScopeDocSchema(fr.IDSchema):
        tenant_id: int
        name: str

    async def bind_tenant(tenant: Annotated[int, Header(alias="x-tenant-id")]):
        with current_tenant.bind(tenant_id=tenant):
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
# reference checks: default_scope, per-field override, explicit escape
# ---------------------------------------------------------------------------


def test_reference_checks_apply_scopes_over_http(client):
    class ScopeOwner(fr.IDBase):
        tenant_id: Mapped[int]
        active: Mapped[bool]

    current_tenant = fr.context_param("tenant_id", int)

    class ScopeOwnerClauses(fr.ClauseNamespace):
        model = ScopeOwner

        owned_by_tenant = fr.where_clause(ScopeOwner.tenant_id == current_tenant)
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
        audit_owner_id: Annotated[int, fr.RefExists(ScopeOwner, scope=None)] | None = (
            None
        )

    async def bind_tenant(tenant: Annotated[int, Header(alias="x-tenant-id")]):
        with current_tenant.bind(tenant_id=tenant):
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

    # scope=None: the explicit unscoped escape
    post_task(audit_owner_id=theirs)


def test_idref_resolution_applies_default_scope_over_http(client):
    class ScopeAuthor(fr.IDBase):
        tenant_id: Mapped[int]
        name: Mapped[str]

    current_tenant = fr.context_param("tenant_id", int)

    class ScopeAuthorClauses(fr.ClauseNamespace):
        model = ScopeAuthor

        default_scope = fr.where_clause(ScopeAuthor.tenant_id == current_tenant)

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
        with current_tenant.bind(tenant_id=tenant):
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


# ---------------------------------------------------------------------------
# programmatic coverage: sync flavor, fallbacks, fail-loud paths
# ---------------------------------------------------------------------------


class _SyncBase(DeclarativeBase):
    pass


class SyncRow(_SyncBase):
    __tablename__ = "scope_sync_row"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int]


_sync_tenant = fr.context_param("tenant_id", int)


class SyncRowClauses(fr.ClauseNamespace):
    model = SyncRow

    default_scope = fr.where_clause(SyncRow.tenant_id == _sync_tenant)


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
    with _sync_tenant.bind(tenant_id=1):
        assert view.get_one(1).id == 1
        with pytest.raises(NotFound):
            view.get_one(2)


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

    with _sync_tenant.bind(tenant_id=1):
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

    owned_by_tenant = fr.where_clause(SyncRanked.tenant_id == _sync_tenant)
    by_rank = fr.transform_clause(_by_rank)
    # against convention on purpose: default_scope carrying a transform
    default_scope = fr.combine(owned_by_tenant, by_rank)


def test_reference_check_uses_only_the_predicate_half(sync_session):
    sync_session.add(SyncRanked(id=1, tenant_id=1, rank=5))
    sync_session.flush()

    class _TaskSchema(fr.BaseSchema):
        ranked_id: Annotated[int, fr.RefExists(SyncRanked)]

    # the ordering transform in default_scope is dropped, the tenant
    # predicate is kept
    with _sync_tenant.bind(tenant_id=1):
        _check_ref_exists(sync_session, SyncRanked, _TaskSchema(ranked_id=1))
    with _sync_tenant.bind(tenant_id=2):
        with pytest.raises(NotFound, match="ranked_id"):
            _check_ref_exists(sync_session, SyncRanked, _TaskSchema(ranked_id=1))


def test_join_dependent_reference_scope_fails_loudly(sync_session):
    def _join_side(stmt: Select) -> Select:
        return stmt.join(SyncSide, SyncSide.ranked_id == SyncRanked.id)

    joined = fr.combine(
        fr.transform_clause(_join_side), fr.where_clause(SyncSide.id.is_not(None))
    )

    class _JoinSchema(fr.BaseSchema):
        ranked_id: Annotated[int, fr.RefExists(SyncRanked, scope=joined)]

    # the join is dropped with the transform half, so its where would be a
    # cartesian product; the table validation rejects it instead
    with pytest.raises(TypeError, match="not in the statement"):
        _check_ref_exists(sync_session, SyncRanked, _JoinSchema(ranked_id=1))


def test_ref_exists_rejects_a_non_clause_scope():
    with pytest.raises(TypeError, match="where_clause"):
        fr.RefExists(SyncRow, scope=SyncRow.tenant_id == 1)
    with pytest.raises(TypeError, match="ContextParam"):
        fr.RefExists(SyncRow, scope=fr.context_param("x"))
    with pytest.raises(TypeError, match="no predicate"):
        fr.RefExists(SyncRow, scope=fr.transform_clause(_by_rank))


# ---------------------------------------------------------------------------
# fail-loud guards: a scope that cannot filter must not exist quietly
# ---------------------------------------------------------------------------


def test_default_scope_must_carry_a_predicate():
    class _Plain(_SyncBase):
        __tablename__ = "scope_sync_plain"

        id: Mapped[int] = mapped_column(primary_key=True)

    with pytest.raises(TypeError, match="carries no predicate"):

        class _TransformOnly(fr.ClauseNamespace):
            model = _Plain
            default_scope = fr.transform_clause(_by_rank)

    with pytest.raises(TypeError, match="carries no predicate"):

        class _SlotOnly(fr.ClauseNamespace):
            model = _Plain
            default_scope = fr.context_param("z")


def test_subclass_namespace_must_restate_default_scope():
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

    class AnimalClauses(fr.ClauseNamespace):
        model = Animal
        default_scope = fr.where_clause(Animal.tenant_id == _sync_tenant)

    assert _default_scope(Dog) is AnimalClauses.default_scope  # inherited

    # a shadowing namespace must say what happens to the inherited scope
    with pytest.raises(TypeError, match="shadows"):

        class _SilentDogClauses(fr.ClauseNamespace):
            model = Dog
            is_dog = fr.where_clause(Dog.kind == "dog")

    class DogClauses(fr.ClauseNamespace):
        model = Dog
        default_scope = None  # explicit opt-out

    assert _default_scope(Dog) is None

    class CatClauses(fr.ClauseNamespace):
        model = Cat
        default_scope = AnimalClauses.default_scope  # explicit reuse

    assert _default_scope(Cat) is AnimalClauses.default_scope


def test_post_hoc_default_scope_corruption_is_loud():
    class _Row(_SyncBase):
        __tablename__ = "scope_sync_corrupt"

        id: Mapped[int] = mapped_column(primary_key=True)
        tenant_id: Mapped[int]

    class _RowClauses(fr.ClauseNamespace):
        model = _Row
        default_scope = fr.where_clause(_Row.tenant_id == _sync_tenant)

    _RowClauses.default_scope = _Row.tenant_id == 1  # forgot where_clause()
    with pytest.raises(TypeError, match="not a Clause"):
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

    default_scope = fr.where_clause(SyncWeird.tenant_id == _sync_tenant)


def test_scoped_resolution_uses_the_mapper_pk(sync_session):
    sync_session.add(SyncWeird(code=7, tenant_id=1))
    sync_session.flush()

    class _WeirdSchema(fr.BaseSchema):
        thing: fr.IDSchema[SyncWeird]

    with _sync_tenant.bind(tenant_id=1):
        resolved = _resolve_ids_to_sqlalchemy_objects(
            sync_session, _WeirdSchema(thing=7)
        )
        assert resolved["thing"].code == 7
    with _sync_tenant.bind(tenant_id=2):
        with pytest.raises(NotFound, match="thing"):
            _resolve_ids_to_sqlalchemy_objects(sync_session, _WeirdSchema(thing=7))
