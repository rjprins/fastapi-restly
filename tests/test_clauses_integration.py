"""Row-level verification against in-memory SQLite: the promises the unit
suite only asserts as SQL strings, executed for real."""

import threading
from datetime import datetime

import pytest
from sqlalchemy import ForeignKey, create_engine, delete, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship
from sqlalchemy.pool import StaticPool

from fastapi_restly.clauses import (
    UNSCOPED,
    ClauseNamespace,
    ContextNamespace,
    ContextParam,
    WhereClause,
    all_of,
    any_of,
    apply_clauses,
    none_of,
    where_clause,
)


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

    collection: Mapped[Collection] = relationship()
    subscriptions: Mapped[list["Subscription"]] = relationship()


class Subscription(Base):
    __tablename__ = "subscription"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("item.id"))
    status: Mapped[str]


class Ctx(ContextNamespace):
    tenant_id: ContextParam[int]


class Wanted(ContextNamespace):
    tenants: ContextParam[list[int]]


class SubscriptionFilter(ContextNamespace):
    """A local namespace: only the subscription report binds it."""

    status: ContextParam[str]


class ItemClauses(ClauseNamespace):
    model = Item

    is_deleted = where_clause(Item.deleted_at.is_not(None))
    owned_by_tenant = where_clause(Item.tenant_id == Ctx.tenant_id)

    has_active_subscription = where_clause(
        Item.subscriptions.any(Subscription.status == "active")
    )

    in_live_collection = where_clause(
        Item.collection.has(Collection.archived_at.is_(None))
    )

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


def seen_by(session: Session, tenant_id: int, clause: WhereClause) -> set[int]:
    # the statement carries the bound value: it runs after the bind ended
    with Ctx.bind(tenant_id=tenant_id):
        stmt = apply_clauses(select(Item), clause)
    return ids(session, stmt)


def test_tenant_isolation(engine):
    with Session(engine) as s:
        assert seen_by(s, T1, ItemClauses.visible) == {1, 4}
        assert seen_by(s, T2, ItemClauses.visible) == {3}


def test_trash_is_the_complement(engine):
    with Session(engine) as s:
        assert seen_by(s, T1, ItemClauses.trashed) == {2}
        assert seen_by(s, T2, ItemClauses.trashed) == set()


def test_restore_respects_tenant_on_update(engine):
    def restore_item_2(tenant_id: int):
        with Ctx.bind(tenant_id=tenant_id):
            stmt = apply_clauses(update(Item), ItemClauses.trashed)
        return stmt.where(Item.id == 2).values(deleted_at=None)

    with Session(engine) as s:
        assert s.execute(restore_item_2(T2)).rowcount == 0
        assert s.execute(restore_item_2(T1)).rowcount == 1
        assert seen_by(s, T1, ItemClauses.visible) == {1, 2, 4}
        s.rollback()


def test_exists_does_not_multiply_rows(engine):
    # item 1 has TWO active subscriptions; a join would return it twice
    q = all_of(ItemClauses.visible, ItemClauses.has_active_subscription)
    with Ctx.bind(tenant_id=T1):
        stmt = apply_clauses(select(Item), q)
    with Session(engine) as s:
        assert [item.id for item in s.scalars(stmt)] == [1]


def test_has_on_a_related_table_excludes_archived_collection(engine):
    with Session(engine) as s:
        q = all_of(ItemClauses.visible, ItemClauses.in_live_collection)
        # 4 lives in an archived collection
        assert seen_by(s, T1, q) == {1}


def test_in_subquery_passes_validation_and_runs(engine):
    in_live_collection = where_clause(
        Item.collection_id.in_(
            select(Collection.id).where(Collection.archived_at.is_(None))
        )
    )
    with Session(engine) as s:
        q = all_of(ItemClauses.visible, in_live_collection)
        assert seen_by(s, T1, q) == {1}


def test_interop_call_in_raw_where(engine):
    with Ctx.bind(tenant_id=T1):
        stmt = select(Item).where(
            ItemClauses.owned_by_tenant(), ItemClauses.is_deleted()
        )
    with Session(engine) as s:
        assert ids(s, stmt) == {2}


def test_concurrent_tenant_binds(engine):
    results: dict[int, set[int]] = {}
    barrier = threading.Barrier(2)

    def worker(tenant_id: int):
        with Ctx.bind(tenant_id=tenant_id):
            barrier.wait()  # both binds active at the same time
            stmt = apply_clauses(select(Item), ItemClauses.visible)
            with Session(engine) as s:
                results[tenant_id] = ids(s, stmt)

    threads = [threading.Thread(target=worker, args=(t,)) for t in (T1, T2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == {T1: {1, 4}, T2: {3}}


def test_embedded_member_returns_tenant_rows(engine):
    with Session(engine) as s:
        assert seen_by(s, T1, ItemClauses.owned_by_tenant) == {1, 2, 4}


def test_embedded_member_in_list_executes(engine):
    cond = where_clause(Item.tenant_id.in_(Wanted.tenants))
    with Wanted.bind(tenants=[T1]):
        stmt = apply_clauses(select(Item), cond)
    with Session(engine) as s:
        assert ids(s, stmt) == {1, 2, 4}


def test_local_member_inside_exists_runs_after_its_bind_ended(engine):
    subscribed = where_clause(
        Item.subscriptions.any(Subscription.status == SubscriptionFilter.status)
    )

    def report_stmt(status: str):
        # the local shape: bind tightly, hand the statement to the caller
        with Ctx.bind(tenant_id=T1), SubscriptionFilter.bind(status=status):
            return apply_clauses(select(Item), ItemClauses.visible, subscribed)

    with Session(engine) as s:
        assert ids(s, report_stmt("active")) == {1}
        assert ids(s, report_stmt("cancelled")) == {4}
        assert ids(s, report_stmt("paused")) == set()


def test_bind_resets_after_exception(engine):
    with pytest.raises(RuntimeError):
        with Ctx.bind(tenant_id=T1):
            raise RuntimeError("boom")
    with pytest.raises(LookupError):
        apply_clauses(select(Item), ItemClauses.visible)  # unbound again


@pytest.mark.parametrize(
    "op, expected", [(all_of, {2}), (any_of, {1, 2, 3, 4}), (none_of, set())]
)
@pytest.mark.parametrize("unscoped_first", [True, False])
def test_unscoped_boolean_composition_returns_the_expected_rows(
    engine, op, expected, unscoped_first
):
    args = (UNSCOPED, ItemClauses.is_deleted)
    if not unscoped_first:
        args = tuple(reversed(args))
    scope = op(*args)
    with Session(engine) as session:
        assert ids(session, apply_clauses(select(Item), scope)) == expected


def test_negated_unscoped_cannot_update_or_delete_rows(engine):
    scope = none_of(UNSCOPED)
    with Session(engine) as session:
        updated = session.execute(
            apply_clauses(update(Item), scope).values(name="changed")
        )
        deleted = session.execute(apply_clauses(delete(Item), scope))
        assert updated.rowcount == 0
        assert deleted.rowcount == 0
        assert ids(session, select(Item)) == {1, 2, 3, 4}
        session.rollback()
