"""Regression (fznb.2): the session fixtures must not hijack unrelated sessions.

``restly_session`` / ``restly_async_session`` patch ``commit`` and the context
exit on the *class* (``Session`` / ``AsyncSession``), so those overrides fire for
every session in the process for the duration of a test. The old overrides
ignored ``self`` and operated on the fixture's own session, so an unrelated
session -- on a different engine and database the fixture knows nothing about --
had its ``commit()`` silently redirected into the fixture session and its context
exit never closed it.

The overrides now dispatch on identity: only the fixture's own session gets the
savepoint treatment; every other session behaves normally.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

import fastapi_restly._pytest_fixtures as _fixtures
from fastapi_restly.db._globals import RestlyContext, _fr_globals


class _Base(DeclarativeBase):
    pass


class _Row(_Base):
    __tablename__ = "unrelated_session_row"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str]


def test_unrelated_sync_session_is_not_hijacked_by_restly_session():
    fixture_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    make_session = sessionmaker(bind=fixture_engine, expire_on_commit=False)
    # A second engine/database the fixture knows nothing about.
    other_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    OtherSession = sessionmaker(bind=other_engine, expire_on_commit=False)

    try:
        _Base.metadata.create_all(fixture_engine)
        _Base.metadata.create_all(other_engine)
        with RestlyContext():
            _fr_globals.make_session = make_session
            with fixture_engine.connect() as conn:
                gen = _fixtures.restly_session.__wrapped__(conn)
                fixture_session = next(gen)
                try:
                    # An unrelated session commits for real.
                    with OtherSession() as committer:
                        committer.add(_Row(name="committed"))
                        committer.commit()
                        assert not committer.in_transaction()

                    # An unrelated session's context exit closes it (real
                    # __exit__), so an uncommitted flush is rolled back.
                    with OtherSession() as roller:
                        roller.add(_Row(name="rolled-back"))
                        roller.flush()
                    assert not roller.in_transaction()

                    with OtherSession() as check:
                        names = set(check.scalars(select(_Row.name)))
                    assert "committed" in names
                    assert "rolled-back" not in names

                    # The fixture's own session still gets savepoint isolation,
                    # not a real commit: its write is visible within the test.
                    fixture_session.add(_Row(name="fixture"))
                    fixture_session.commit()
                    found = fixture_session.scalars(
                        select(_Row).where(_Row.name == "fixture")
                    )
                    assert found.first() is not None
                finally:
                    gen.close()
    finally:
        fixture_engine.dispose()
        other_engine.dispose()


@pytest.mark.asyncio
async def test_unrelated_async_session_is_not_hijacked_by_restly_async_session():
    fixture_engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    make_session = async_sessionmaker(bind=fixture_engine, expire_on_commit=False)
    other_engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    OtherSession = async_sessionmaker(bind=other_engine, expire_on_commit=False)

    try:
        async with fixture_engine.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)
        async with other_engine.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)
        with RestlyContext():
            _fr_globals.async_make_session = make_session
            agen = _fixtures.restly_async_session.__wrapped__(None)
            fixture_session = await agen.__anext__()
            try:
                # An unrelated async session commits for real.
                async with OtherSession() as committer:
                    committer.add(_Row(name="committed"))
                    await committer.commit()
                    assert not committer.in_transaction()

                # An unrelated async session's context exit closes it (real
                # __aexit__), so an uncommitted flush is rolled back.
                async with OtherSession() as roller:
                    roller.add(_Row(name="rolled-back"))
                    await roller.flush()
                assert not roller.in_transaction()

                async with OtherSession() as check:
                    names = set(await check.scalars(select(_Row.name)))
                assert "committed" in names
                assert "rolled-back" not in names

                # The fixture's own session still gets savepoint isolation.
                fixture_session.add(_Row(name="fixture"))
                await fixture_session.commit()
                found = await fixture_session.scalars(
                    select(_Row).where(_Row.name == "fixture")
                )
                assert found.first() is not None
            finally:
                await agen.aclose()
    finally:
        await fixture_engine.dispose()
        await other_engine.dispose()
