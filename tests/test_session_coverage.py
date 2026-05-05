from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr
import fastapi_restly.db as fr_db
import fastapi_restly.testing as fr_testing
from fastapi_restly.db._globals import RestlyContext, _get_restly_context
from fastapi_restly.db._proxy import async_open_session as proxy_async_open_session
from fastapi_restly.db._proxy import open_session as proxy_open_session
from fastapi_restly.db._session import (
    _async_generate_session,
    _generate_session,
    _setup_async_database_connection,
    _setup_database_connection,
    activate_savepoint_only_mode,
    configure,
    deactivate_savepoint_only_mode,
    get_async_engine,
    get_engine,
)


def test_public_session_context_manager_exports_use_open_names():
    assert fr.open_session is fr_db.open_session
    assert fr.async_open_session is fr_db.async_open_session
    assert fr.RestlyContext is fr_db.RestlyContext
    assert "open_session" in fr.__all__
    assert "async_open_session" in fr.__all__
    assert "RestlyContext" in fr.__all__
    assert "FRGlobals" not in fr.__all__
    assert "use_fr_globals" not in fr.__all__
    assert "get_restly_context" not in fr.__all__
    assert "use_restly_context" not in fr.__all__
    assert "open_session" in fr_db.__all__
    assert "async_open_session" in fr_db.__all__
    assert "RestlyContext" in fr_db.__all__
    assert "FRGlobals" not in fr_db.__all__
    assert "fr_globals" not in fr_db.__all__
    assert "use_fr_globals" not in fr_db.__all__
    assert "async_generate_session" not in fr_db.__all__
    assert "generate_session" not in fr_db.__all__
    assert not hasattr(fr, "FRGlobals")
    assert not hasattr(fr_db, "FRGlobals")
    assert not hasattr(fr_db, "fr_globals")
    assert not hasattr(fr_db, "use_fr_globals")
    assert not hasattr(fr_db, "async_generate_session")
    assert not hasattr(fr_db, "generate_session")
    assert "get_restly_context" not in fr_db.__all__
    assert "use_restly_context" not in fr_db.__all__
    assert not hasattr(fr, "get_restly_context")
    assert not hasattr(fr, "use_restly_context")
    assert not hasattr(fr, "session")
    assert not hasattr(fr, "async_session")
    assert "session" not in fr.__all__
    assert "async_session" not in fr.__all__
    assert not hasattr(fr, "activate_savepoint_only_mode")
    assert not hasattr(fr, "deactivate_savepoint_only_mode")
    assert "activate_savepoint_only_mode" not in fr.__all__
    assert "deactivate_savepoint_only_mode" not in fr.__all__
    assert fr_testing.activate_savepoint_only_mode is activate_savepoint_only_mode
    assert fr_testing.deactivate_savepoint_only_mode is deactivate_savepoint_only_mode
    assert "activate_savepoint_only_mode" in fr_testing.__all__
    assert "deactivate_savepoint_only_mode" in fr_testing.__all__


def test_restly_context_is_public_context_manager():
    original_context = _get_restly_context()
    context = fr.RestlyContext()

    with context as active_context:
        assert active_context is context
        assert _get_restly_context() is context
        assert fr.get_fr_globals() is context

    assert _get_restly_context() is original_context


def test_restly_context_can_be_used_anonymously():
    original_context = _get_restly_context()

    with fr.RestlyContext() as context:
        assert _get_restly_context() is context

    assert _get_restly_context() is original_context


def test_restly_context_nested_entries_restore_in_lifo_order():
    outer = fr.RestlyContext()
    inner = fr.RestlyContext()

    with outer:
        assert _get_restly_context() is outer
        with inner:
            assert _get_restly_context() is inner
        assert _get_restly_context() is outer


def test_getters_and_sync_proxy_raise_without_configuration():
    with RestlyContext():
        with pytest.raises(RuntimeError, match="Call fr.configure\\(\\)"):
            get_engine()

        with pytest.raises(RuntimeError, match="Call fr.configure\\(\\)"):
            with proxy_open_session():
                pass


@pytest.mark.asyncio
async def test_async_getter_and_proxy_raise_without_configuration():
    with RestlyContext():
        with pytest.raises(RuntimeError, match="Call fr.configure\\(\\)"):
            get_async_engine()

        with pytest.raises(RuntimeError, match="Call fr.configure\\(\\)"):
            async with proxy_async_open_session():
                pass


def test_setup_database_connection_creates_sessionmaker_and_proxy_open_session():
    with RestlyContext():
        make_session = _setup_database_connection(
            "sqlite://",
            engine=create_engine(
                "sqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            ),
        )

        assert get_engine() is make_session.kw["bind"]

        with proxy_open_session() as session:
            assert isinstance(session, Session)

        make_session.kw["bind"].dispose()


@pytest.mark.asyncio
async def test_setup_async_database_connection_creates_sessionmaker_and_proxy_open_session():
    async_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        with RestlyContext():
            make_session = _setup_async_database_connection(
                "sqlite+aiosqlite:///:memory:", async_engine=async_engine
            )

            assert get_async_engine() is async_engine

            async with proxy_async_open_session() as session:
                assert isinstance(session, AsyncSession)

            assert make_session.kw["bind"] is async_engine
    finally:
        await async_engine.dispose()


def test_configure_accepts_explicit_sessionmakers():
    sync_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sync_make_session = sessionmaker(bind=sync_engine, expire_on_commit=False)
    async_make_session = async_sessionmaker(bind=async_engine, expire_on_commit=False)

    try:
        with RestlyContext():
            configure(
                make_session=sync_make_session, async_make_session=async_make_session
            )

            assert get_engine() is sync_engine
            assert get_async_engine() is async_engine
    finally:
        sync_engine.dispose()
        import asyncio

        asyncio.run(async_engine.dispose())


def test_activate_and_deactivate_savepoint_only_mode_for_sync_sessionmaker():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    make_session = sessionmaker(bind=engine, expire_on_commit=False)
    original_connect = engine.connect

    try:
        activate_savepoint_only_mode(make_session)
        assert engine.connect is not original_connect
        assert hasattr(engine.connect, "_original_connect")
        assert make_session.kw["join_transaction_mode"] == "create_savepoint"

        activate_savepoint_only_mode(make_session)
        assert hasattr(engine.connect, "_original_connect")

        deactivate_savepoint_only_mode(make_session)
        assert not hasattr(engine.connect, "_original_connect")
        assert make_session.kw["join_transaction_mode"] is None
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_activate_and_deactivate_savepoint_only_mode_for_async_sessionmaker():
    async_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    make_session = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    original_connect = async_engine.sync_engine.connect

    try:
        activate_savepoint_only_mode(make_session)
        assert async_engine.sync_engine.connect is not original_connect
        assert hasattr(async_engine.sync_engine.connect, "_original_connect")
        assert make_session.kw["join_transaction_mode"] == "create_savepoint"

        deactivate_savepoint_only_mode(make_session)
        assert not hasattr(async_engine.sync_engine.connect, "_original_connect")
        assert make_session.kw["join_transaction_mode"] is None
    finally:
        await async_engine.dispose()


def test_generate_session_commits_and_rolls_back_on_failure():
    class DummySyncSession:
        def __init__(self, fail_commit: bool = False):
            self.fail_commit = fail_commit
            self.is_active = True
            self.committed = 0
            self.rolled_back = 0

        def commit(self):
            self.committed += 1
            if self.fail_commit:
                raise RuntimeError("boom")

        def rollback(self):
            self.rolled_back += 1

    class DummySyncContext:
        def __init__(self, session: DummySyncSession):
            self.session = session

        def __enter__(self):
            return self.session

        def __exit__(self, exc_type, exc, tb):
            return False

    class DummySyncMaker:
        def __init__(self, session: DummySyncSession):
            self.session = session

        def __call__(self):
            return DummySyncContext(self.session)

    with RestlyContext():
        successful = DummySyncSession()
        configure(make_session=DummySyncMaker(successful))  # type: ignore[arg-type]
        yielded = list(_generate_session())
        assert yielded == [successful]
        assert successful.committed == 1
        assert successful.rolled_back == 0

    with RestlyContext():
        failing = DummySyncSession(fail_commit=True)
        configure(make_session=DummySyncMaker(failing))  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="boom"):
            list(_generate_session())
        assert failing.committed == 1
        assert failing.rolled_back == 1


@pytest.mark.asyncio
async def test_async_generate_session_commits_and_rolls_back_on_failure():
    class DummyAsyncSession:
        def __init__(self, fail_commit: bool = False):
            self.fail_commit = fail_commit
            self.is_active = True
            self.committed = 0
            self.rolled_back = 0

        async def commit(self):
            self.committed += 1
            if self.fail_commit:
                raise RuntimeError("boom")

        async def rollback(self):
            self.rolled_back += 1

    class DummyAsyncContext:
        def __init__(self, session: DummyAsyncSession):
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummyAsyncMaker:
        def __init__(self, session: DummyAsyncSession):
            self.session = session

        def __call__(self):
            return DummyAsyncContext(self.session)

    with RestlyContext():
        successful = DummyAsyncSession()
        configure(
            async_make_session=DummyAsyncMaker(successful)  # type: ignore[arg-type]
        )

        yielded = []
        async for session in _async_generate_session():
            yielded.append(session)

        assert yielded == [successful]
        assert successful.committed == 1
        assert successful.rolled_back == 0

    with RestlyContext():
        failing = DummyAsyncSession(fail_commit=True)
        configure(async_make_session=DummyAsyncMaker(failing))  # type: ignore[arg-type]

        with pytest.raises(RuntimeError, match="boom"):
            async for _session in _async_generate_session():
                pass

        assert failing.committed == 1
        assert failing.rolled_back == 1
