"""Regression: a configured ``session_generator`` / ``sync_session_generator``
must not break the shipped session fixtures.

The internal test scope installs an isolated factory, and the test clients
override the generator on the app. Requests and open_session() use the shared
connection without mutating the application's generator. Generator-only
rollback setups are covered by test_configured_session_fixtures.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace

import pytest
from _pytest.outcomes import Skipped
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr
import fastapi_restly._pytest_fixtures as _fixtures
from fastapi_restly._test_setup import NONE
from fastapi_restly.db._globals import RestlyContext, _fr_globals
from fastapi_restly.db._session import _async_generate_session, _generate_session

pytest_plugins = ["pytester"]


class _Base(DeclarativeBase):
    pass


class _Row(_Base):
    __tablename__ = "session_generator_row"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str]


_ASYNC_SESSION_REQUEST = SimpleNamespace(fixturenames=["restly_async_session"])


def test_sync_fixture_isolates_a_generator_configured_project():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    make_session = sessionmaker(bind=engine, expire_on_commit=False)
    # The project's own session source. It has no schema on purpose: if a
    # request reaches it, the write fails loudly instead of disappearing.
    project_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    project_make_session = sessionmaker(bind=project_engine, expire_on_commit=False)
    generator_calls = []

    def project_get_db() -> Iterator[Session]:
        generator_calls.append(1)
        with project_make_session() as session:
            yield session

    try:
        _Base.metadata.create_all(engine)
        with RestlyContext():
            _fr_globals.make_session = make_session
            _fr_globals.sync_session_generator = project_get_db
            with engine.connect() as conn:
                scope = _fixtures._restly_sync_scope.__wrapped__(
                    conn, _fixtures._source_factories()[0]
                )
                isolated_make_session = next(scope)
                gen = _fixtures.restly_session.__wrapped__(isolated_make_session)
                try:
                    session = next(gen)

                    request_gen = _generate_session()
                    request_session = next(request_gen)
                    # No shared identity map now: the request builds its own
                    # session, isolated onto the fixture's pinned connection --
                    # not one from the project generator.
                    assert request_session is not session
                    assert request_session.get_bind() is session.get_bind()

                    row = _Row(name="written in a request")
                    request_session.add(row)
                    request_session.commit()
                    next(request_gen, None)  # run the dependency's teardown

                    assert generator_calls == []
                    # select(), not get(): get() is an identity-map hit that
                    # emits no SQL, so it would pass without the write landing.
                    fetched = session.scalars(select(_Row).where(_Row.id == row.id))
                    assert fetched.first() is not None
                finally:
                    gen.close()
                    scope.close()

            # The fixture restores the generator after the test.
            assert _fr_globals.sync_session_generator is project_get_db
    finally:
        engine.dispose()
        project_engine.dispose()


def test_sync_fixture_skips_generator_only_config_in_none_mode(monkeypatch):
    def project_get_db() -> Iterator[Session]:  # pragma: no cover - never called
        raise AssertionError("the fixture must not call the generator")
        yield

    monkeypatch.setattr(_fixtures, "_cleanup_mode", lambda: NONE)
    with RestlyContext():
        _fr_globals.sync_session_generator = project_get_db
        gen = _fixtures.restly_session.__wrapped__(None)
        try:
            with pytest.raises(Skipped, match="Database connection not set up"):
                next(gen)
        finally:
            gen.close()


@pytest.mark.asyncio
async def test_async_fixture_isolates_a_generator_configured_project():
    async_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    make_session = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    # See the sync test: no schema here on purpose.
    project_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    project_make_session = async_sessionmaker(
        bind=project_engine, expire_on_commit=False
    )
    generator_calls = []

    async def project_get_db() -> AsyncIterator[AsyncSession]:
        generator_calls.append(1)
        async with project_make_session() as session:
            yield session

    try:
        async with async_engine.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)
        with RestlyContext():
            _fr_globals.async_make_session = make_session
            _fr_globals.session_generator = project_get_db
            scope = _fixtures._restly_async_scope.__wrapped__(
                None, _ASYNC_SESSION_REQUEST
            )
            isolated_make_session = await scope.__anext__()
            agen = _fixtures.restly_async_session.__wrapped__(isolated_make_session)
            try:
                session = await agen.__anext__()

                request_gen = _async_generate_session()
                request_session = await request_gen.__anext__()
                # No shared identity map now: the request builds its own session,
                # isolated onto the fixture's pinned connection -- not one from the
                # project generator.
                assert request_session is not session
                assert request_session.get_bind() is session.get_bind()

                row = _Row(name="written in a request")
                request_session.add(row)
                await request_session.commit()
                await anext(request_gen, None)  # run the dependency's teardown

                assert generator_calls == []
                fetched = await session.scalars(select(_Row).where(_Row.id == row.id))
                assert fetched.first() is not None
            finally:
                await agen.aclose()
                await scope.aclose()

            # The fixture restores the generator after the test.
            assert _fr_globals.session_generator is project_get_db
    finally:
        await async_engine.dispose()
        await project_engine.dispose()


@pytest.mark.asyncio
async def test_async_fixture_skips_generator_only_config_in_none_mode(monkeypatch):
    async def project_get_db() -> AsyncIterator[AsyncSession]:  # pragma: no cover
        raise AssertionError("the fixture must not call the generator")
        yield

    monkeypatch.setattr(_fixtures, "_cleanup_mode", lambda: NONE)
    with RestlyContext():
        _fr_globals.session_generator = project_get_db
        agen = _fixtures.restly_async_session.__wrapped__(None)
        try:
            with pytest.raises(Skipped, match="Database connection not set up"):
                await agen.__anext__()
        finally:
            await agen.aclose()


def test_open_session_yields_an_isolated_session_with_a_generator_configured():
    # fr.open_session() reads sync_session_generator the same way SessionDep
    # does, so off-request code also gets an isolated session on the fixture's
    # connection rather than one from the project generator.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    make_session = sessionmaker(bind=engine, expire_on_commit=False)

    def project_get_db() -> Iterator[Session]:  # pragma: no cover - never called
        raise AssertionError("the fixture must not call the generator")
        yield

    try:
        with RestlyContext():
            _fr_globals.make_session = make_session
            _fr_globals.sync_session_generator = project_get_db
            with engine.connect() as conn:
                scope = _fixtures._restly_sync_scope.__wrapped__(
                    conn, _fixtures._source_factories()[0]
                )
                isolated_make_session = next(scope)
                gen = _fixtures.restly_session.__wrapped__(isolated_make_session)
                try:
                    session = next(gen)
                    with fr.open_session() as opened:
                        # A distinct session now, isolated onto the fixture's
                        # connection -- reaching the project generator would raise.
                        assert opened is not session
                        assert opened.get_bind() is session.get_bind()
                finally:
                    gen.close()
                    scope.close()
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_open_async_session_yields_an_isolated_session_with_a_generator_configured():
    async_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    make_session = async_sessionmaker(bind=async_engine, expire_on_commit=False)

    async def project_get_db() -> AsyncIterator[AsyncSession]:  # pragma: no cover
        raise AssertionError("the fixture must not call the generator")
        yield

    try:
        with RestlyContext():
            _fr_globals.async_make_session = make_session
            _fr_globals.session_generator = project_get_db
            scope = _fixtures._restly_async_scope.__wrapped__(
                None, _ASYNC_SESSION_REQUEST
            )
            isolated_make_session = await scope.__anext__()
            agen = _fixtures.restly_async_session.__wrapped__(isolated_make_session)
            try:
                session = await agen.__anext__()
                async with fr.open_async_session() as opened:
                    # A distinct session now, isolated onto the fixture's
                    # connection -- reaching the project generator would raise.
                    assert opened is not session
                    assert opened.get_bind() is session.get_bind()
            finally:
                await agen.aclose()
                await scope.aclose()
    finally:
        await async_engine.dispose()


def test_client_request_is_isolated_with_a_generator_configured(
    pytester: pytest.Pytester,
):
    """TestClient requests use the fixture's pinned connection across threads."""
    pytester.makefile(
        ".toml",
        pyproject="""
[tool.pytest.ini_options]
asyncio_default_fixture_loop_scope = "function"
""",
    )
    pytester.makeconftest(
        """
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Mapped, sessionmaker
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr


class Widget(fr.IDBase):
    name: Mapped[str]


engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
# The project's own session source, deliberately left without the schema: if a
# request reaches it, the write fails loudly instead of disappearing.
project_engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
project_make_session = sessionmaker(bind=project_engine, expire_on_commit=False)


def project_get_db():
    with project_make_session() as session:
        yield session


fr.configure(engine=engine, sync_session_generator=project_get_db)
app = FastAPI()


@app.post("/widgets", status_code=201)
def create_widget(session: fr.SessionDep):
    widget = Widget(name="alpha")
    session.add(widget)
    session.commit()
    return {"id": widget.id}


fr.DataclassBase.metadata.create_all(engine)


@pytest.fixture
def restly_app():
    return app


@pytest.fixture(scope="session", autouse=True)
def _dispose_engines():
    # Without this the pooled sqlite connections are closed by GC, and the
    # ResourceWarning surfaces as a failure in an unrelated later test.
    yield
    engine.dispose()
    project_engine.dispose()
"""
    )
    pytester.makepyfile(
        """
from sqlalchemy import select

from conftest import Widget


def test_request_write_lands_in_the_fixture_session(restly_session, restly_client):
    response = restly_client.post("/widgets")  # asserts 201

    widget_id = response.json()["id"]
    found = restly_session.scalars(select(Widget).where(Widget.id == widget_id))
    assert found.first() is not None
"""
    )

    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=1)
