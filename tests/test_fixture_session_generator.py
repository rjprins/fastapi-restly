"""Regression: a configured ``session_generator`` / ``sync_session_generator``
must not break the shipped session fixtures.

``SessionDep`` and ``AsyncSessionDep`` resolve the generator before the session
factory the fixtures swap. A project that configured one got no isolation: the
request built its own real session from the generator, outside the fixture's
transaction, so its write escaped the rollback.

The fixtures now clear the generator for the duration of a test and restore it
afterwards, so the request is built from the fixture's isolated factory on the
shared connection. Without a sessionmaker to build from, the fixture raises
instead of skipping with a misleading "Database connection not set up".
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from _pytest.outcomes import Skipped
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr
import fastapi_restly._pytest_fixtures as _fixtures
from fastapi_restly.db._globals import RestlyContext, _fr_globals
from fastapi_restly.db._session import _async_generate_session, _generate_session
from fastapi_restly.exc import RestlyConfigurationError

pytest_plugins = ["pytester"]


class _Base(DeclarativeBase):
    pass


class _Row(_Base):
    __tablename__ = "session_generator_row"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str]


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
                gen = _fixtures.restly_session.__wrapped__(conn)
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

            # The fixture restores the generator after the test.
            assert _fr_globals.sync_session_generator is project_get_db
    finally:
        engine.dispose()
        project_engine.dispose()


def test_sync_fixture_raises_when_only_a_generator_is_configured():
    def project_get_db() -> Iterator[Session]:  # pragma: no cover - never called
        raise AssertionError("the fixture must not call the generator")
        yield

    with RestlyContext():
        _fr_globals.sync_session_generator = project_get_db
        gen = _fixtures.restly_session.__wrapped__(None)
        try:
            with pytest.raises(
                RestlyConfigurationError, match="sync_session_generator"
            ):
                try:
                    next(gen)
                except Skipped as skip:
                    # A revert to pytest.skip() would otherwise mark this test
                    # skipped, not failed, and slip through CI green.
                    raise AssertionError(
                        f"fixture skipped instead of raising: {skip}"
                    ) from skip
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
            agen = _fixtures.restly_async_session.__wrapped__(None)
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

            # The fixture restores the generator after the test.
            assert _fr_globals.session_generator is project_get_db
    finally:
        await async_engine.dispose()
        await project_engine.dispose()


@pytest.mark.asyncio
async def test_async_fixture_raises_when_only_a_generator_is_configured():
    async def project_get_db() -> AsyncIterator[AsyncSession]:  # pragma: no cover
        raise AssertionError("the fixture must not call the generator")
        yield

    with RestlyContext():
        _fr_globals.session_generator = project_get_db
        agen = _fixtures.restly_async_session.__wrapped__(None)
        try:
            # Match the fixture name: "session_generator" alone is a substring
            # of "sync_session_generator" and would pass on the wrong message.
            with pytest.raises(RestlyConfigurationError, match="restly_async_session"):
                try:
                    await agen.__anext__()
                except Skipped as skip:
                    # A revert to pytest.skip() would otherwise mark this test
                    # skipped, not failed, and slip through CI green.
                    raise AssertionError(
                        f"fixture skipped instead of raising: {skip}"
                    ) from skip
        finally:
            await agen.aclose()


def test_open_session_yields_an_isolated_session_with_a_generator_configured():
    # fr.open_session() reads sync_session_generator the same way SessionDep
    # does, so off-request code also gets an isolated session on the fixture's
    # connection rather than one from the (cleared) project generator.
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
                gen = _fixtures.restly_session.__wrapped__(conn)
                try:
                    session = next(gen)
                    with fr.open_session() as opened:
                        # A distinct session now, isolated onto the fixture's
                        # connection -- reaching the project generator would raise.
                        assert opened is not session
                        assert opened.get_bind() is session.get_bind()
                finally:
                    gen.close()
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
            agen = _fixtures.restly_async_session.__wrapped__(None)
            try:
                session = await agen.__anext__()
                async with fr.open_async_session() as opened:
                    # A distinct session now, isolated onto the fixture's
                    # connection -- reaching the project generator would raise.
                    assert opened is not session
                    assert opened.get_bind() is session.get_bind()
            finally:
                await agen.aclose()
    finally:
        await async_engine.dispose()


def test_client_request_is_isolated_with_a_generator_configured(
    pytester: pytest.Pytester,
):
    """End-to-end: the bug's real shape is a request through RestlyTestClient.

    The tests above drive the dependency inline, in the calling thread. The
    test client runs the app in its own portal thread, which is what makes the
    global mutation (rather than a ContextVar) the load-bearing mechanism.
    """
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


app = FastAPI()


@app.post("/widgets", status_code=201)
def create_widget(session: fr.SessionDep):
    widget = Widget(name="alpha")
    session.add(widget)
    session.commit()
    return {"id": widget.id}


fr.configure(engine=engine, sync_session_generator=project_get_db)
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

    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1)
