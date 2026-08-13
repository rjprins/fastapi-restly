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
import warnings
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.requests import HTTPConnection
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, inspect, orm
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr
from fastapi_restly._test_setup import _reset_setup, configure_tests
from fastapi_restly.db._globals import RestlyContext, _get_restly_context
from fastapi_restly.db._session import (
    _SCOPE_KEY,
    _AppContextScopeMiddleware,
    _async_generate_session,
    _generate_session,
    _resolve_contexts,
)
from fastapi_restly.exc import (
    RestlyConfigurationError,
    RestlyMisuseWarning,
    RestlyUncommittedChangesWarning,
)


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
        conn = HTTPConnection({"type": "http", _SCOPE_KEY: app.state._fr_context})

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
        conn = HTTPConnection({"type": "http", _SCOPE_KEY: app.state._fr_context})

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


def test_reconfiguring_an_app_merges_instead_of_snapshotting(tmp_path):
    """A later configure(app, ...) call touches only what it sets: a warn-flag
    call must not re-point the app at whatever the process context holds."""
    app_a, app_b = FastAPI(), FastAPI()
    _add_sync_db_route(app_a)
    engine_a = _sqlite_engine(tmp_path / "a.db")
    engine_b = _sqlite_engine(tmp_path / "b.db")
    try:
        with RestlyContext():
            fr.configure(app_a, engine=engine_a)
            fr.configure(app_b, engine=engine_b)
            fr.configure(app_a, warn_on_misuse=True)

        app_context = app_a.state._fr_context
        assert app_context.make_session is not None
        assert app_context.make_session.kw["bind"] is engine_a
        assert app_context.warn_on_misuse is True
        middleware_count = sum(
            1 for m in app_a.user_middleware if m.cls is _AppContextScopeMiddleware
        )
        assert middleware_count == 1

        with TestClient(app_a) as client:
            assert client.get("/db").json() == {"database": "a.db"}
    finally:
        engine_a.dispose()
        engine_b.dispose()


def test_a_process_wide_sync_generator_is_not_inherited_by_the_app(tmp_path):
    """An app-less generator configured for some other purpose must not be
    frozen into an app's context, where it would outrank the app's engine."""

    def foreign_generator():
        raise AssertionError("the process-wide generator must not serve the app")
        yield  # pragma: no cover -- makes this a generator function

    app = FastAPI()
    _add_sync_db_route(app)
    engine = _sqlite_engine(tmp_path / "b.db")
    try:
        with RestlyContext():
            fr.configure(sync_session_generator=foreign_generator)
            fr.configure(app, engine=engine)

        assert app.state._fr_context.sync_session_generator is None
        with TestClient(app) as client:
            assert client.get("/db").json() == {"database": "b.db"}
    finally:
        engine.dispose()


def test_a_process_wide_async_generator_is_not_inherited_by_the_app(tmp_path):
    async def foreign_generator():
        raise AssertionError("the process-wide generator must not serve the app")
        yield  # pragma: no cover -- makes this an async generator function

    app = FastAPI()
    _add_async_db_route(app)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}")
    try:
        with RestlyContext():
            fr.configure(session_generator=foreign_generator)
            fr.configure(app, async_engine=engine)

        assert app.state._fr_context.session_generator is None
        with TestClient(app) as client:
            assert client.get("/db").json() == {"database": "b.db"}
    finally:
        asyncio.run(engine.dispose())


def test_a_mounted_sub_app_inherits_the_parents_configuration(tmp_path):
    """Starlette re-stamps scope["app"] with the innermost app; the middleware
    stamp is what lets a mounted, unconfigured sub-app keep answering from its
    parent's configuration instead of the drifting process default."""
    parent, sub = FastAPI(), FastAPI()
    _add_sync_db_route(parent)
    _add_sync_db_route(sub)
    parent.mount("/sub", sub)
    engine_a = _sqlite_engine(tmp_path / "a.db")
    drift_engine = _sqlite_engine(tmp_path / "drift.db")
    try:
        with RestlyContext():
            fr.configure(parent, engine=engine_a)
            fr.configure(engine=drift_engine)  # later app-less re-point

        with TestClient(parent) as client:
            assert client.get("/db").json() == {"database": "a.db"}
            assert client.get("/sub/db").json() == {"database": "a.db"}
    finally:
        engine_a.dispose()
        drift_engine.dispose()


def test_a_configured_mounted_sub_app_wins_over_its_parent(tmp_path):
    parent, sub = FastAPI(), FastAPI()
    _add_sync_db_route(parent)
    _add_sync_db_route(sub)
    parent.mount("/sub", sub)
    engine_a = _sqlite_engine(tmp_path / "a.db")
    engine_c = _sqlite_engine(tmp_path / "c.db")
    try:
        with RestlyContext():
            fr.configure(parent, engine=engine_a)
            fr.configure(sub, engine=engine_c)

        with TestClient(parent) as client:
            assert client.get("/db").json() == {"database": "a.db"}
            assert client.get("/sub/db").json() == {"database": "c.db"}
    finally:
        engine_a.dispose()
        engine_c.dispose()


def test_a_missing_source_kind_raises_instead_of_borrowing(tmp_path):
    """An app configured for one session kind must not silently serve the
    other kind from the process default (another app's database)."""
    sync_app = FastAPI()
    sync_engine = _sqlite_engine(tmp_path / "sync.db")
    async_app = FastAPI()
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'async.db'}")
    try:
        with RestlyContext():
            fr.configure(sync_app, engine=sync_engine)
            fr.configure(async_app, async_engine=async_engine)

        sync_conn = HTTPConnection(
            {"type": "http", _SCOPE_KEY: sync_app.state._fr_context}
        )
        with pytest.raises(RestlyConfigurationError, match="async session source"):
            _resolve_contexts(sync_conn, sync=False)

        async_conn = HTTPConnection(
            {"type": "http", _SCOPE_KEY: async_app.state._fr_context}
        )
        with pytest.raises(
            RestlyConfigurationError, match="synchronous session source"
        ):
            _resolve_contexts(async_conn, sync=True)
    finally:
        sync_engine.dispose()
        asyncio.run(async_engine.dispose())


def test_a_missing_source_kind_raises_through_a_real_request(tmp_path):
    app = FastAPI()
    _add_async_db_route(app)  # AsyncSessionDep on a sync-only app
    engine = _sqlite_engine(tmp_path / "sync.db")
    try:
        with RestlyContext():
            fr.configure(app, engine=engine)
        with TestClient(app) as client:
            with pytest.raises(
                RestlyConfigurationError, match="async session source"
            ):
                client.get("/db")
    finally:
        engine.dispose()


def test_a_flags_only_app_context_scopes_flags_but_not_sources():
    app = FastAPI()
    with RestlyContext():
        fr.configure(app, warn_on_uncommitted=False)
    app_context = app.state._fr_context
    conn = HTTPConnection({"type": "http", _SCOPE_KEY: app_context})

    sources, flags = _resolve_contexts(conn, sync=True)

    assert flags is app_context
    assert sources is _get_restly_context()
    assert flags.warn_on_uncommitted is False


class ScratchNote(fr.IDBase):
    title: orm.Mapped[str]


def _create_scratch_note_table(engine) -> None:
    table = ScratchNote.__table__
    assert isinstance(table, Table)
    table.create(engine)


def _add_uncommitted_route(app: FastAPI) -> None:
    @app.post("/leave-uncommitted")
    def leave_uncommitted(session: fr.SessionDep) -> dict[str, bool]:
        session.add(ScratchNote(title="pending"))
        session.flush()
        return {"ok": True}


def test_app_scoped_uncommitted_optout_survives_the_process_flag(tmp_path):
    """configure(app, warn_on_uncommitted=False) governs the app's requests
    even while the process-wide flag stays on."""
    app = FastAPI()
    _add_uncommitted_route(app)
    engine = _sqlite_engine(tmp_path / "app.db")
    _create_scratch_note_table(engine)
    try:
        with RestlyContext():
            fr.configure(app, engine=engine, warn_on_uncommitted=False)
            fr.configure(warn_on_uncommitted=True)

        with TestClient(app) as client:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                client.post("/leave-uncommitted")
        assert not [
            w
            for w in caught
            if issubclass(w.category, RestlyUncommittedChangesWarning)
        ]
    finally:
        engine.dispose()


def test_the_uncommitted_warning_still_fires_without_the_app_optout(tmp_path):
    app = FastAPI()
    _add_uncommitted_route(app)
    engine = _sqlite_engine(tmp_path / "app.db")
    _create_scratch_note_table(engine)
    try:
        with RestlyContext():
            fr.configure(app, engine=engine)

        with TestClient(app) as client:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                client.post("/leave-uncommitted")
        assert [
            w
            for w in caught
            if issubclass(w.category, RestlyUncommittedChangesWarning)
        ]
    finally:
        engine.dispose()


def test_app_scoped_warn_on_misuse_governs_registration_on_that_app():
    """The registration lint follows the mount target's app context: a
    configured app lints while the process flag is off, and an unconfigured
    app stays silent."""

    class LintArticle(fr.IDBase):
        title: orm.Mapped[str]

    class LintArticleView(fr.AsyncRestView):
        prefix = "/lint-articles"
        model = LintArticle

    with RestlyContext():
        linted_app, silent_app = FastAPI(), FastAPI()
        fr.configure(linted_app, warn_on_misuse=True)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fr.include_view(linted_app, LintArticleView)
            fr.include_view(linted_app, LintArticleView)  # duplicate -> lint
        assert [
            w for w in caught if issubclass(w.category, RestlyMisuseWarning)
        ]

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fr.include_view(silent_app, LintArticleView)
            fr.include_view(silent_app, LintArticleView)
        assert not [
            w for w in caught if issubclass(w.category, RestlyMisuseWarning)
        ]


def test_app_scoped_warn_on_misuse_lints_the_view_class_itself():
    """The class-level lint (route-shell override) also follows the app."""

    class ShellNote(fr.IDBase):
        title: orm.Mapped[str]

    class ShellNoteView(fr.AsyncRestView):
        prefix = "/shell-notes"
        model = ShellNote

        @fr.get("/")
        async def get_many_endpoint(self, page: int = 1):
            return []

    with RestlyContext():
        app = FastAPI()
        fr.configure(app, warn_on_misuse=True)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fr.include_view(app, ShellNoteView)
    messages = [
        str(w.message) for w in caught if issubclass(w.category, RestlyMisuseWarning)
    ]
    assert any("get_many_endpoint" in message for message in messages)


def test_db_helpers_accept_an_app_argument(tmp_path):
    """get_engine/create_all answer for the app you name, not for whichever
    app configured the process default last."""
    app_a, app_b = FastAPI(), FastAPI()
    engine_a = _sqlite_engine(tmp_path / "a.db")
    engine_b = _sqlite_engine(tmp_path / "b.db")
    metadata = MetaData()
    Table("app_scoped_helper_table", metadata, Column("id", Integer, primary_key=True))
    try:
        with RestlyContext():
            fr.configure(app_a, engine=engine_a)
            fr.configure(app_b, engine=engine_b)

            assert fr.db.get_engine(app=app_a) is engine_a
            assert fr.db.get_engine(app=app_b) is engine_b
            assert fr.db.get_engine() is engine_b  # process default: last write

            fr.db.create_all(metadata, app=app_a)
            assert inspect(engine_a).has_table("app_scoped_helper_table")
            assert not inspect(engine_b).has_table("app_scoped_helper_table")
    finally:
        engine_a.dispose()
        engine_b.dispose()


@pytest.mark.asyncio
async def test_async_db_helpers_accept_an_app_argument(tmp_path):
    app_a, app_b = FastAPI(), FastAPI()
    engine_a = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}")
    engine_b = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}")
    metadata = MetaData()
    Table("app_scoped_helper_table", metadata, Column("id", Integer, primary_key=True))
    try:
        with RestlyContext():
            fr.configure(app_a, async_engine=engine_a)
            fr.configure(app_b, async_engine=engine_b)

            assert fr.db.get_async_engine(app=app_a) is engine_a
            assert fr.db.get_async_engine(app=app_b) is engine_b
            assert fr.db.get_async_engine() is engine_b

            await fr.db.async_create_all(metadata, app=app_a)
            async with engine_a.connect() as conn_a:
                assert await conn_a.run_sync(
                    lambda sync_conn: inspect(sync_conn).has_table(
                        "app_scoped_helper_table"
                    )
                )
            async with engine_b.connect() as conn_b:
                assert not await conn_b.run_sync(
                    lambda sync_conn: inspect(sync_conn).has_table(
                        "app_scoped_helper_table"
                    )
                )
    finally:
        await engine_a.dispose()
        await engine_b.dispose()


def test_configuring_an_app_after_its_first_request_raises(tmp_path):
    """The app-scoping middleware cannot be installed once the middleware
    stack is built, so a late first configure(app, ...) fails loudly instead
    of silently staying process-scoped."""
    app = FastAPI()
    with TestClient(app):
        pass  # builds the middleware stack
    engine = _sqlite_engine(tmp_path / "late.db")
    try:
        with RestlyContext():
            with pytest.raises(
                RestlyConfigurationError, match="before the application"
            ):
                fr.configure(app, engine=engine)
    finally:
        engine.dispose()
