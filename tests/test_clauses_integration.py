"""Row-level verification against in-memory SQLite: the promises the unit
suite only asserts as SQL strings, executed for real."""

import threading
from datetime import datetime

import pytest
from sqlalchemy import ColumnElement, ForeignKey, Select, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship
from sqlalchemy.pool import StaticPool

from fastapi_restly.clauses import (
    ClauseNamespace,
    all_of,
    any_of,
    apply_clauses,
    combine,
    none_of,
    transform_clause,
    where_clause,
)
from fastapi_restly.clauses import _context_param as context_param


class Base(DeclarativeBase):
    pass


class Collection(Base):
    __tablename__ = "collection"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int]
    archived_at: Mapped[datetime | None]


class Item(Base):
    __tablename__ = "item"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int]
    collection_id: Mapped[int] = mapped_column(ForeignKey("collection.id"))
    name: Mapped[str]
    created_at: Mapped[datetime]
    deleted_at: Mapped[datetime | None]

    subscriptions: Mapped[list["Subscription"]] = relationship()


class Subscription(Base):
    __tablename__ = "subscription"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("item.id"))
    status: Mapped[str]


class ItemClauses(ClauseNamespace):
    model = Item

    is_deleted = where_clause(Item.deleted_at.is_not(None))

    @where_clause
    def owned_by_tenant(tenant_id: int) -> ColumnElement[bool]:
        return Item.tenant_id == tenant_id

    @where_clause
    def in_period(start: datetime, end: datetime) -> ColumnElement[bool]:
        return Item.created_at.between(start, end)

    has_active_subscription = where_clause(
        Item.subscriptions.any(Subscription.status == "active")
    )

    @transform_clause
    def join_collection(stmt: Select) -> Select:
        return stmt.join(Collection, Collection.id == Item.collection_id)

    with_live_collection = combine(
        join_collection, where_clause(Collection.archived_at.is_(None))
    )

    @transform_clause
    def paged(stmt: Select, limit: int, offset: int) -> Select:
        return stmt.limit(limit).offset(offset)

    visible = all_of(owned_by_tenant, none_of(is_deleted))
    trashed = all_of(owned_by_tenant, is_deleted)


T1, T2 = 1, 2
AUG = datetime(2026, 8, 10)
JUL = datetime(2026, 7, 15)
JUN = datetime(2026, 6, 1)


@pytest.fixture(scope="module")
def engine():
    # StaticPool + check_same_thread=False: one shared in-memory database,
    # also across the threads of the concurrency test
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Collection(id=1, tenant_id=T1, archived_at=None),
                Collection(id=2, tenant_id=T1, archived_at=JUN),
            ]
        )
        session.add_all(
            [
                Item(
                    id=1,
                    tenant_id=T1,
                    collection_id=1,
                    name="red lamp",
                    created_at=AUG,
                    deleted_at=None,
                ),
                Item(
                    id=2,
                    tenant_id=T1,
                    collection_id=1,
                    name="old chair",
                    created_at=JUN,
                    deleted_at=JUL,
                ),
                Item(
                    id=3,
                    tenant_id=T2,
                    collection_id=1,
                    name="blue lamp",
                    created_at=AUG,
                    deleted_at=None,
                ),
                Item(
                    id=4,
                    tenant_id=T1,
                    collection_id=2,
                    name="green sofa",
                    created_at=JUL,
                    deleted_at=None,
                ),
            ]
        )
        session.add_all(
            [
                Subscription(id=1, item_id=1, status="active"),
                Subscription(id=2, item_id=1, status="active"),
                Subscription(id=3, item_id=4, status="cancelled"),
            ]
        )
        session.commit()
    yield engine
    engine.dispose()


def ids(session: Session, stmt) -> set[int]:
    return {item.id for item in session.scalars(stmt)}


def test_tenant_isolation(engine):
    with Session(engine) as s:
        assert ids(s, ItemClauses.visible.select(Item, tenant_id=T1)) == {1, 4}
        assert ids(s, ItemClauses.visible.select(Item, tenant_id=T2)) == {3}


def test_trash_is_the_complement(engine):
    with Session(engine) as s:
        assert ids(s, ItemClauses.trashed.select(Item, tenant_id=T1)) == {2}
        assert ids(s, ItemClauses.trashed.select(Item, tenant_id=T2)) == set()


def test_restore_respects_tenant_on_update(engine):
    with Session(engine) as s:
        wrong_tenant = s.execute(
            ItemClauses.trashed.update(Item, tenant_id=T2)
            .where(Item.id == 2)
            .values(deleted_at=None)
        )
        assert wrong_tenant.rowcount == 0

        right_tenant = s.execute(
            ItemClauses.trashed.update(Item, tenant_id=T1)
            .where(Item.id == 2)
            .values(deleted_at=None)
        )
        assert right_tenant.rowcount == 1
        assert ids(s, ItemClauses.visible.select(Item, tenant_id=T1)) == {1, 2, 4}
        s.rollback()


def test_exists_does_not_multiply_rows(engine):
    # item 1 has TWO active subscriptions; a join would return it twice
    with Session(engine) as s:
        q = all_of(ItemClauses.visible, ItemClauses.has_active_subscription)
        rows = [item.id for item in s.scalars(q.select(Item, tenant_id=T1))]
        assert rows == [1]


def test_join_bundle_excludes_archived_collection(engine):
    with Session(engine) as s:
        q = all_of(ItemClauses.visible, ItemClauses.with_live_collection)
        assert ids(s, q.select(Item, tenant_id=T1)) == {
            1
        }  # 4 lives in an archived collection


def test_alias_two_periods(engine):
    august = ItemClauses.in_period.alias("integration_august")
    july = ItemClauses.in_period.alias("integration_july")
    q = all_of(ItemClauses.owned_by_tenant, any_of(august, july))
    with (
        august.bind(start=datetime(2026, 8, 1), end=datetime(2026, 8, 31)),
        july.bind(start=datetime(2026, 7, 1), end=datetime(2026, 7, 31)),
    ):
        with Session(engine) as s:
            assert ids(s, q.select(Item, tenant_id=T1)) == {1, 4}


def test_ephemeral_transform_binding(engine):
    q = combine(ItemClauses.visible, ItemClauses.paged)
    with Session(engine) as s:
        rows = list(s.scalars(q.select(Item, tenant_id=T1, limit=1, offset=0)))
        assert len(rows) == 1


def test_in_subquery_passes_validation_and_runs(engine):
    in_live_collection = where_clause(
        Item.collection_id.in_(
            select(Collection.id).where(Collection.archived_at.is_(None))
        )
    )
    with Session(engine) as s:
        q = all_of(ItemClauses.visible, in_live_collection)
        assert ids(s, q.select(Item, tenant_id=T1)) == {1}


def test_interop_call_in_raw_where(engine):
    with Session(engine) as s:
        stmt = select(Item).where(
            ItemClauses.owned_by_tenant(tenant_id=T1), ItemClauses.is_deleted()
        )
        assert ids(s, stmt) == {2}


def test_concurrent_tenant_binds(engine):
    results: dict[int, set[int]] = {}
    barrier = threading.Barrier(2)

    def worker(tenant_id: int):
        with ItemClauses.visible.bind(tenant_id=tenant_id):
            barrier.wait()  # both binds active at the same time
            with Session(engine) as s:
                results[tenant_id] = ids(s, ItemClauses.visible.select(Item))

    threads = [threading.Thread(target=worker, args=(t,)) for t in (T1, T2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == {T1: {1, 4}, T2: {3}}


def test_embedded_shared_slot_returns_tenant_rows(engine):
    slot = context_param("it_tenant")
    owned = where_clause(Item.tenant_id == slot)
    with slot.bind(it_tenant=T1):
        with Session(engine) as s:
            assert ids(s, apply_clauses(select(Item), owned)) == {1, 2, 4}


def test_embedded_slot_in_list_executes(engine):
    slot = context_param("wanted_tenants")
    cond = where_clause(Item.tenant_id.in_(slot))
    with slot.bind(wanted_tenants=[T1]):
        with Session(engine) as s:
            assert ids(s, apply_clauses(select(Item), cond)) == {1, 2, 4}


def test_bind_resets_after_exception(engine):
    with pytest.raises(RuntimeError):
        with ItemClauses.visible.bind(tenant_id=T1):
            raise RuntimeError("boom")
    with pytest.raises(TypeError):
        ItemClauses.visible.select(Item)  # unbound again
