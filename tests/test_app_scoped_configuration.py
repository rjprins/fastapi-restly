"""App-scoped configuration: ``fr.configure(app, ...)`` snapshots the session
sources onto the app, and requests resolve them app-first.

Resolution order under test (``_resolve_source_context``):

1. a test override on the current context (installed by the pytest fixtures),
2. the app's own context, only when no ``RestlyContext`` is explicitly entered,
3. the current context, exactly as before app-scoping existed.

The two-app tests go through the synchronous ``TestClient`` deliberately: its
portal thread does not inherit the test body's entered context, so requests run
with the ContextVar unset -- the production topology where the app context is
allowed to win.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.requests import HTTPConnection
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr
from fastapi_restly._test_setup import _reset_setup, configure_tests
from fastapi_restly.db._globals import RestlyContext, _get_restly_context
from fastapi_restly.db._session import _async_generate_session, _generate_session
from fastapi_restly.exc import RestlyConfigurationError


@pytest.fixture(autouse=True)
def _forget_setup():
    """Drop any recorded configure_tests() setup so it cannot leak across tests."""
    yield
    _reset_setup()


def _sqlite_engine(path: Path):
    # TestClient runs sync endpoints on worker threads and the test body
    # disposes from the main thread, so the sqlite thread check must be off.
    return create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}
    )


def _add_sync_db_route(app: FastAPI) -> None:
    @app.get("/db")
    def which_database(session: fr.SessionDep) -> dict[str, str]:
        return {"database": Path(str(session.get_bind().engine.url.database)).name}


def _add_async_db_route(app: FastAPI) -> None:
    @app.get("/db")
    async def which_database(session: fr.AsyncSessionDep) -> dict[str, str]:
        return {"database": Path(str(session.get_bind().engine.url.database)).name}


def test_configure_app_snapshots_the_same_factory_onto_the_app():
    """The app context reuses the configured sessionmaker object: no second
    engine, no rebuilt factory."""
    app = FastAPI()
    with RestlyContext() as context:
        fr.configure(app, database_url="sqlite://")
        make_session = context.make_session
        assert make_session is not None
        try:
            app_context = app.state._fr_context
            assert app_context is not context
            assert app_context.make_session is make_session
            assert app_context.database_url == "sqlite://"
            # Test overrides and the configure_tests lock are never app-scoped.
            assert app_context.test_make_session is None
            assert app_context.test_async_make_session is None
            assert app_context.database_configuration_locked is False
        finally:
            make_session.kw["bind"].dispose()


def test_app_only_configure_creates_no_app_context():
    """``configure(app)`` still means "install exception handlers" only."""
    with RestlyContext():
        app = FastAPI()
        fr.configure(app)
        assert getattr(app.state, "_fr_context", None) is None


def test_warn_flag_only_configure_creates_an_app_context():
    with RestlyContext():
        app = FastAPI()
        fr.configure(app, warn_on_misuse=True)
        assert app.state._fr_context.warn_on_misuse is True


def test_each_app_serves_requests_from_its_own_configuration(tmp_path):
    """Two apps in one process, each answering from its own database, even
    though the second configure() re-pointed the context they were built in."""
    app_a, app_b = FastAPI(), FastAPI()
    _add_sync_db_route(app_a)
    _add_sync_db_route(app_b)
    engine_a = _sqlite_engine(tmp_path / "a.db")
    engine_b = _sqlite_engine(tmp_path / "b.db")
    try:
        with RestlyContext():
            fr.configure(app_a, engine=engine_a)
            fr.configure(app_b, engine=engine_b)

        with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
            assert client_a.get("/db").json() == {"database": "a.db"}
            assert client_b.get("/db").json() == {"database": "b.db"}
    finally:
        engine_a.dispose()
        engine_b.dispose()


def test_each_app_serves_async_requests_from_its_own_configuration(tmp_path):
    """Async parity for the two-app scenario, over ``AsyncSessionDep``."""
    app_a, app_b = FastAPI(), FastAPI()
    _add_async_db_route(app_a)
    _add_async_db_route(app_b)
    engine_a = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}")
    engine_b = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}")
    try:
        with RestlyContext():
            fr.configure(app_a, async_engine=engine_a)
            fr.configure(app_b, async_engine=engine_b)

        with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
            assert client_a.get("/db").json() == {"database": "a.db"}
            assert client_b.get("/db").json() == {"database": "b.db"}
    finally:
        asyncio.run(engine_a.dispose())
        asyncio.run(engine_b.dispose())


def test_test_override_on_the_current_context_beats_the_app_context(tmp_path):
    """The fixtures install ``test_make_session`` on the current context; a
    request must keep resolving it even when the app carries its own config."""
    current = _get_restly_context()
    app = FastAPI()
    _add_sync_db_route(app)
    app_engine = _sqlite_engine(tmp_path / "app.db")
    override_engine = _sqlite_engine(tmp_path / "override.db")
    fr.configure(app, engine=app_engine)
    current.test_make_session = sessionmaker(
        bind=override_engine, expire_on_commit=False
    )
    try:
        with TestClient(app) as client:
            assert client.get("/db").json() == {"database": "override.db"}
    finally:
        current.test_make_session = None
        override_engine.dispose()
        app_engine.dispose()


def test_an_entered_context_beats_the_app_context(tmp_path):
    """An explicitly entered ``RestlyContext`` is a more specific scope than
    the app: its sources stay authoritative (pins the pooled-async test
    wrapper's contract)."""
    app = FastAPI()
    app_engine = _sqlite_engine(tmp_path / "app.db")
    entered_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    try:
        with RestlyContext():
            fr.configure(app, engine=app_engine)
        conn = HTTPConnection({"type": "http", "app": app})

        with RestlyContext():
            fr.configure(engine=entered_engine)
            for session in _generate_session(conn):
                assert session.get_bind() is entered_engine
    finally:
        entered_engine.dispose()
        app_engine.dispose()


@pytest.mark.asyncio
async def test_an_entered_context_beats_the_app_context_async(tmp_path):
    app = FastAPI()
    app_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    entered_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        with RestlyContext():
            fr.configure(app, async_engine=app_engine)
        conn = HTTPConnection({"type": "http", "app": app})

        with RestlyContext():
            fr.configure(async_engine=entered_engine)
            async for session in _async_generate_session(conn):
                assert session.get_bind() is entered_engine.sync_engine
    finally:
        await app_engine.dispose()
        await entered_engine.dispose()


def test_a_direct_call_without_a_connection_resolves_the_current_context(tmp_path):
    """``conn=None`` (unit tests, bare generators) behaves exactly as before
    app-scoping: the current context wins, apps notwithstanding."""
    app = FastAPI()
    app_engine = _sqlite_engine(tmp_path / "app.db")
    entered_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    try:
        with RestlyContext():
            fr.configure(app, engine=app_engine)
        with RestlyContext():
            fr.configure(engine=entered_engine)
            for session in _generate_session():
                assert session.get_bind() is entered_engine
    finally:
        entered_engine.dispose()
        app_engine.dispose()


def test_open_session_resolves_the_dual_written_default(tmp_path):
    """Off-HTTP helpers keep working after an app-scoped configure(): the same
    sources are written to the context configure() ran in."""
    app = FastAPI()
    app_engine = _sqlite_engine(tmp_path / "app.db")
    try:
        with RestlyContext():
            fr.configure(app, engine=app_engine)
            with fr.open_session() as session:
                assert session.get_bind() is app_engine
    finally:
        app_engine.dispose()


def test_the_configure_tests_lock_still_rejects_a_second_app(tmp_path):
    """App-scoping does not loosen the freeze: after configure_tests() records
    the database, configuring another app's database still raises."""
    engine = _sqlite_engine(tmp_path / "suite.db")
    try:
        with RestlyContext():
            fr.configure(engine=engine)
            configure_tests(app=FastAPI())
            with pytest.raises(RestlyConfigurationError, match="cannot be changed"):
                fr.configure(FastAPI(), database_url="sqlite://")
    finally:
        engine.dispose()
