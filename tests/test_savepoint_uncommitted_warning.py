"""Regression: RestlyUncommittedChangesWarning must not
false-positive under the savepoint test fixtures.

The fixtures use real SQLAlchemy sessions joined to an outer transaction with
``create_savepoint``. A session commit must still fire ``after_commit`` and
clear the detector's "flushed but uncommitted" flag even though the outer test
transaction never commits.

These drive the savepoint fixtures directly (the ``.__wrapped__`` pattern, like
test_testing_fixtures_coverage.py) and assert both halves of the contract:

* a write that commits does NOT warn (no false positive);
* a write that never commits STILL warns (the true positive is preserved -- the
  whole point of choosing this fix over simply disabling the warning in tests).
"""

from __future__ import annotations

import warnings
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

import fastapi_restly._pytest_fixtures as _fixtures
from fastapi_restly.db._globals import RestlyContext, _fr_globals
from fastapi_restly.db._session import _arm_uncommitted_warning, _warn_if_uncommitted
from fastapi_restly.exc import RestlyUncommittedChangesWarning


class _Base(DeclarativeBase):
    pass


class _Row(_Base):
    __tablename__ = "uncommitted_warning_row"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str]


_ASYNC_SESSION_REQUEST = SimpleNamespace(fixturenames=["restly_async_session"])


def _warn_count(check) -> int:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        check()
    return sum(issubclass(w.category, RestlyUncommittedChangesWarning) for w in caught)


def test_savepoint_sync_commit_does_not_false_warn():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    make_session = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        _Base.metadata.create_all(engine)
        with RestlyContext():
            _fr_globals.make_session = make_session
            with engine.connect() as conn:
                scope = _fixtures._restly_sync_scope.__wrapped__(conn)
                isolated_make_session = next(scope)
                gen = _fixtures.restly_session.__wrapped__(isolated_make_session)
                try:
                    session = next(gen)
                    _arm_uncommitted_warning(session)

                    # Writes and commits clear the flag through after_commit.
                    session.add(_Row(name="committed"))
                    session.flush()
                    session.commit()
                    assert _warn_count(lambda: _warn_if_uncommitted(session)) == 0

                    # Flushes but never commits -> still warns (true positive).
                    session.add(_Row(name="forgotten"))
                    session.flush()
                    assert _warn_count(lambda: _warn_if_uncommitted(session)) >= 1
                finally:
                    gen.close()
                    scope.close()
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_savepoint_async_commit_does_not_false_warn():
    async_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    make_session = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    try:
        async with async_engine.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)
        with RestlyContext():
            _fr_globals.async_make_session = make_session
            scope = _fixtures._restly_async_scope.__wrapped__(
                None, _ASYNC_SESSION_REQUEST
            )
            isolated_make_session = await scope.__anext__()
            agen = _fixtures.restly_async_session.__wrapped__(isolated_make_session)
            try:
                session = await agen.__anext__()  # savepoint session; patch active
                _arm_uncommitted_warning(session)

                session.add(_Row(name="committed"))
                await session.flush()
                await session.commit()
                assert _warn_count(lambda: _warn_if_uncommitted(session)) == 0

                session.add(_Row(name="forgotten"))
                await session.flush()
                assert _warn_count(lambda: _warn_if_uncommitted(session)) >= 1
            finally:
                await agen.aclose()
                await scope.aclose()
    finally:
        await async_engine.dispose()
