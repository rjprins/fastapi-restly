"""fznb.12: the session fixtures isolate through ``create_savepoint`` on a
pinned connection rather than by patching ``Session`` / ``AsyncSession``.

Each request during a test builds its own real session that joins a
never-committed outer transaction via a SAVEPOINT. That gives two guarantees the
old class-patching mechanism could not:

- Cross-request visibility: a write committed in one request is visible to a
  later request in the same test (they share the pinned connection).
- Production-faithful ``rollback()``: a request that rolls back discards only its
  own work, not everything back to the last commit.

These tests also cover fznb.3: with the factory bound to a pinned Connection,
``get_engine`` / ``create_all`` still resolve the real engine and create tables
where the test can see them, instead of silently no-opping on a MagicMock.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr
import fastapi_restly._pytest_fixtures as _fixtures
from fastapi_restly.db._globals import RestlyContext, _fr_globals
from fastapi_restly.db._session import _async_generate_session, _generate_session


class _Base(DeclarativeBase):
    pass


class _Row(_Base):
    __tablename__ = "create_savepoint_row"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str]


def test_get_engine_and_create_all_work_inside_the_sync_fixture():
    # fznb.3: the isolated factory is bound to a pinned Connection, yet
    # get_engine() still returns the real Engine and create_all() lands on the
    # pinned connection instead of silently no-opping.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    make_session = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with RestlyContext():
            _fr_globals.make_session = make_session
            shared_gen = _fixtures._shared_connection.__wrapped__()
            conn = next(shared_gen)
            gen = _fixtures.restly_session.__wrapped__(conn)
            session = next(gen)
            try:
                assert fr.db.get_engine() is engine

                fr.db.create_all(_Base)
                session.add(_Row(name="created"))
                session.flush()
                found = session.scalars(select(_Row).where(_Row.name == "created"))
                assert found.first() is not None
            finally:
                gen.close()
                shared_gen.close()
    finally:
        engine.dispose()


def test_sync_request_write_is_visible_to_a_later_request():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    make_session = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with RestlyContext():
            _fr_globals.make_session = make_session
            shared_gen = _fixtures._shared_connection.__wrapped__()
            conn = next(shared_gen)
            gen = _fixtures.restly_session.__wrapped__(conn)
            next(gen)
            try:
                _Base.metadata.create_all(conn)

                # Request 1 writes and commits.
                req1 = _generate_session()
                s1 = next(req1)
                s1.add(_Row(name="req1"))
                s1.commit()
                next(req1, None)

                # Request 2 is a separate session on the same connection; it sees
                # the committed row.
                req2 = _generate_session()
                s2 = next(req2)
                assert s2 is not s1
                found = s2.scalars(select(_Row).where(_Row.name == "req1"))
                assert found.first() is not None
                next(req2, None)
            finally:
                gen.close()
                shared_gen.close()
    finally:
        engine.dispose()


def test_sync_request_rollback_discards_only_its_own_work():
    # The old mechanism rolled back to the last patched commit; create_savepoint
    # rolls back only the request's own savepoint, matching production.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    make_session = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with RestlyContext():
            _fr_globals.make_session = make_session
            shared_gen = _fixtures._shared_connection.__wrapped__()
            conn = next(shared_gen)
            gen = _fixtures.restly_session.__wrapped__(conn)
            next(gen)
            try:
                _Base.metadata.create_all(conn)

                req1 = _generate_session()
                s1 = next(req1)
                s1.add(_Row(name="keep"))
                s1.commit()
                next(req1, None)

                req2 = _generate_session()
                s2 = next(req2)
                s2.add(_Row(name="discard"))
                s2.flush()
                s2.rollback()
                next(req2, None)

                req3 = _generate_session()
                s3 = next(req3)
                names = set(s3.scalars(select(_Row.name)))
                assert "keep" in names
                assert "discard" not in names
                next(req3, None)
            finally:
                gen.close()
                shared_gen.close()
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_get_async_engine_and_async_create_all_work_inside_the_async_fixture():
    async_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    make_session = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    try:
        with RestlyContext():
            _fr_globals.async_make_session = make_session
            agen = _fixtures.restly_async_session.__wrapped__(None)
            session = await agen.__anext__()
            try:
                assert fr.db.get_async_engine() is async_engine

                await fr.db.async_create_all(_Base)
                session.add(_Row(name="created"))
                await session.flush()
                found = await session.scalars(
                    select(_Row).where(_Row.name == "created")
                )
                assert found.first() is not None
            finally:
                await agen.aclose()
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_async_request_write_is_visible_to_a_later_request():
    async_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    make_session = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    try:
        with RestlyContext():
            _fr_globals.async_make_session = make_session
            agen = _fixtures.restly_async_session.__wrapped__(None)
            await agen.__anext__()
            try:
                await fr.db.async_create_all(_Base)

                req1 = _async_generate_session()
                s1 = await req1.__anext__()
                s1.add(_Row(name="req1"))
                await s1.commit()
                await anext(req1, None)

                req2 = _async_generate_session()
                s2 = await req2.__anext__()
                assert s2 is not s1
                found = await s2.scalars(select(_Row).where(_Row.name == "req1"))
                assert found.first() is not None
                await anext(req2, None)
            finally:
                await agen.aclose()
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_async_request_rollback_discards_only_its_own_work():
    async_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    make_session = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    try:
        with RestlyContext():
            _fr_globals.async_make_session = make_session
            agen = _fixtures.restly_async_session.__wrapped__(None)
            await agen.__anext__()
            try:
                await fr.db.async_create_all(_Base)

                req1 = _async_generate_session()
                s1 = await req1.__anext__()
                s1.add(_Row(name="keep"))
                await s1.commit()
                await anext(req1, None)

                req2 = _async_generate_session()
                s2 = await req2.__anext__()
                s2.add(_Row(name="discard"))
                await s2.flush()
                await s2.rollback()
                await anext(req2, None)

                req3 = _async_generate_session()
                s3 = await req3.__anext__()
                names = set(await s3.scalars(select(_Row.name)))
                assert "keep" in names
                assert "discard" not in names
                await anext(req3, None)
            finally:
                await agen.aclose()
    finally:
        await async_engine.dispose()
