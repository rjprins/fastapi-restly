from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from inspect import signature
from typing import get_args

import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr
import fastapi_restly.db as fr_db
import fastapi_restly.testing as fr_testing
from fastapi_restly.db._globals import RestlyContext, _get_restly_context
from fastapi_restly.db._proxy import open_async_session as proxy_open_async_session
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
    assert fr.open_async_session is fr_db.open_async_session
    assert "open_session" in fr.__all__
    assert "open_async_session" in fr.__all__
    assert "async_open_session" not in fr.__all__
    assert "RestlyContext" not in fr.__all__
    assert "get_fr_globals" not in fr.__all__
    assert "FRGlobals" not in fr.__all__
    assert "use_fr_globals" not in fr.__all__
    assert "get_restly_context" not in fr.__all__
    assert "use_restly_context" not in fr.__all__
    assert "open_session" in fr_db.__all__
    assert "open_async_session" in fr_db.__all__
    assert "async_open_session" not in fr_db.__all__
    assert "RestlyContext" not in fr_db.__all__
    assert "get_fr_globals" not in fr_db.__all__
    assert "FRGlobals" not in fr_db.__all__
    assert "fr_globals" not in fr_db.__all__
    assert "use_fr_globals" not in fr_db.__all__
    assert "async_generate_session" not in fr_db.__all__
    assert "generate_session" not in fr_db.__all__
    assert not hasattr(fr, "FRGlobals")
    assert not hasattr(fr, "RestlyContext")
    assert not hasattr(fr, "get_fr_globals")
    assert not hasattr(fr, "async_open_session")
    assert not hasattr(fr_db, "FRGlobals")
    assert not hasattr(fr_db, "RestlyContext")
    assert not hasattr(fr_db, "get_fr_globals")
    assert not hasattr(fr_db, "async_open_session")
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


def test_private_restly_context_is_context_manager():
    original_context = _get_restly_context()
    context = RestlyContext()

    with context as active_context:
        assert active_context is context
        assert _get_restly_context() is context

    assert _get_restly_context() is original_context


def test_restly_context_can_be_used_anonymously():
    original_context = _get_restly_context()

    with RestlyContext() as context:
        assert _get_restly_context() is context

    assert _get_restly_context() is original_context


def test_configure_rejects_noop_calls_and_accepts_a_single_flag():
    with RestlyContext() as context:
        with pytest.raises(TypeError, match="requires at least one setup argument"):
            configure()

        configure(warn_on_uncommitted=False)
        assert context.warn_on_uncommitted is False

        configure(warn_on_uncommitted=True)
        assert context.warn_on_uncommitted is True


def test_configure_rejects_app_only_call_when_handler_install_is_disabled():
    with RestlyContext():
        app = FastAPI()
        with pytest.raises(TypeError, match="requires at least one setup argument"):
            configure(app=app, install_default_exception_handlers=False)


def test_configure_accepts_app_only_call_when_handler_install_is_enabled():
    with RestlyContext():
        configure(app=FastAPI())


def test_session_dependencies_request_function_scope_when_fastapi_supports_it():
    async_dep = get_args(fr.AsyncSessionDep)[1]
    sync_dep = get_args(fr.SessionDep)[1]

    if "scope" in signature(Depends).parameters:
        assert async_dep.scope == "function"
        assert sync_dep.scope == "function"
    else:
        assert not hasattr(async_dep, "scope")
        assert not hasattr(sync_dep, "scope")


def test_restly_context_nested_entries_restore_in_lifo_order():
    outer = RestlyContext()
    inner = RestlyContext()

    with outer:
        assert _get_restly_context() is outer
        with inner:
            assert _get_restly_context() is inner
        assert _get_restly_context() is outer


def test_getters_and_sync_proxy_raise_without_configuration():
    with RestlyContext():
        with pytest.raises(fr.RestlyConfigurationError, match="Call fr.configure\\(\\)"):
            get_engine()

        with pytest.raises(fr.RestlyConfigurationError, match="Call fr.configure\\(\\)"):
            with proxy_open_session():
                pass


def test_generate_session_raises_clear_error_without_configuration():
    with RestlyContext():
        with pytest.raises(
            fr.RestlyConfigurationError,
            match="Call fr.configure\\(\\) before using SessionDep\\.",
        ):
            list(_generate_session())


@pytest.mark.asyncio
async def test_async_getter_and_proxy_raise_without_configuration():
    with RestlyContext():
        with pytest.raises(fr.RestlyConfigurationError, match="Call fr.configure\\(\\)"):
            get_async_engine()

        with pytest.raises(fr.RestlyConfigurationError, match="Call fr.configure\\(\\)"):
            async with proxy_open_async_session():
                pass


@pytest.mark.asyncio
async def test_async_generate_session_raises_clear_error_without_configuration():
    with RestlyContext():
        with pytest.raises(
            fr.RestlyConfigurationError,
            match="Call fr.configure\\(\\) before using AsyncSessionDep\\.",
        ):
            async for _session in _async_generate_session():
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
        assert make_session.kw["autoflush"] is True
        assert make_session.kw["expire_on_commit"] is False

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
            assert make_session.kw["autoflush"] is False
            assert make_session.kw["expire_on_commit"] is False

            async with proxy_open_async_session() as session:
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


def test_generate_session_does_not_commit_and_exits_context_on_failure():
    """The session dependency yields the session and never commits -- the
    commit is owned by ``handle_<verb>`` (the handle design). Lifecycle
    (close/rollback) is delegated to the session context manager, which is
    entered on yield and exited (with the exception, if any) afterwards."""

    class DummySyncSession:
        def __init__(self):
            self.is_active = True
            self.committed = 0

        def commit(self):
            self.committed += 1

    class DummySyncContext:
        def __init__(self, session: DummySyncSession):
            self.session = session
            self.exit_exc: object = "unset"

        def __enter__(self):
            return self.session

        def __exit__(self, exc_type, exc, tb):
            self.exit_exc = exc_type
            return False

    class DummySyncMaker:
        def __init__(self, session: DummySyncSession):
            self.session = session
            self.context: DummySyncContext | None = None

        def __call__(self):
            self.context = DummySyncContext(self.session)
            return self.context

    # Success: yields the session, never commits, exits cleanly.
    with RestlyContext():
        session = DummySyncSession()
        maker = DummySyncMaker(session)
        configure(make_session=maker)  # type: ignore[arg-type]
        yielded = list(_generate_session())
        assert yielded == [session]
        assert session.committed == 0
        assert maker.context is not None and maker.context.exit_exc is None

    # An exception thrown into the dependency (a failing endpoint) propagates,
    # and the session context manager sees it (a real Session rolls back).
    with RestlyContext():
        session = DummySyncSession()
        maker = DummySyncMaker(session)
        configure(make_session=maker)  # type: ignore[arg-type]
        gen = _generate_session()
        next(gen)
        with pytest.raises(RuntimeError, match="boom"):
            gen.throw(RuntimeError("boom"))
        assert session.committed == 0
        assert maker.context is not None and maker.context.exit_exc is RuntimeError


@pytest.mark.asyncio
async def test_async_generate_session_does_not_commit_and_exits_context_on_failure():
    class DummyAsyncSession:
        def __init__(self):
            self.is_active = True
            self.committed = 0

        async def commit(self):
            self.committed += 1

    class DummyAsyncContext:
        def __init__(self, session: DummyAsyncSession):
            self.session = session
            self.exit_exc: object = "unset"

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, exc_type, exc, tb):
            self.exit_exc = exc_type
            return False

    class DummyAsyncMaker:
        def __init__(self, session: DummyAsyncSession):
            self.session = session
            self.context: DummyAsyncContext | None = None

        def __call__(self):
            self.context = DummyAsyncContext(self.session)
            return self.context

    with RestlyContext():
        session = DummyAsyncSession()
        maker = DummyAsyncMaker(session)
        configure(async_make_session=maker)  # type: ignore[arg-type]
        yielded = [s async for s in _async_generate_session()]
        assert yielded == [session]
        assert session.committed == 0
        assert maker.context is not None and maker.context.exit_exc is None

    with RestlyContext():
        session = DummyAsyncSession()
        maker = DummyAsyncMaker(session)
        configure(async_make_session=maker)  # type: ignore[arg-type]
        gen = _async_generate_session()
        await gen.__anext__()
        with pytest.raises(RuntimeError, match="boom"):
            await gen.athrow(RuntimeError("boom"))
        assert session.committed == 0
        assert maker.context is not None and maker.context.exit_exc is RuntimeError
