"""Run consumer suites to check generator overrides and rollback isolation."""

import pytest

pytest_plugins = ["pytester"]


@pytest.mark.parametrize(
    ("asynchronous", "async_client", "managed"),
    [
        pytest.param(False, False, False, id="sync-fixtures"),
        pytest.param(False, True, False, id="sync-fixtures-async-client"),
        pytest.param(True, False, False, id="async-fixtures-sync-client"),
        pytest.param(True, True, False, id="async-fixtures"),
        pytest.param(False, False, True, id="sync-managed"),
        pytest.param(False, True, True, id="sync-managed-async-client"),
        pytest.param(True, True, True, id="async-managed"),
    ],
)
@pytest.mark.parametrize(
    "with_factory", [False, True], ids=["generator-only", "factory"]
)
def test_generator_requests_share_the_isolated_session_source(
    pytester, asynchronous, async_client, managed, with_factory
):
    if asynchronous:
        database_setup = """
engine = create_async_engine("sqlite+aiosqlite:///test.db")
factory = async_sessionmaker(engine, expire_on_commit=False)

async def get_db():
    calls.append(1)
    async with factory() as session:
        await session.execute(text("SELECT 1"))
        yield session

fr.configure(session_generator=get_db, **({"async_make_session": factory} if WITH_FACTORY else {}))
SessionType = AsyncSession
ViewBase = fr.AsyncRestView
"""
        create_method = """
    async def create(self, data):
        assert self.session is self.native
        return await super().create(data)
"""
        session_fixture = "restly_async_session"
        dispose = "asyncio.run(engine.dispose())"
    else:
        database_setup = """
engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
factory = sessionmaker(engine, expire_on_commit=False)

def get_db():
    calls.append(1)
    with factory() as session:
        session.execute(text("SELECT 1"))
        yield session

fr.configure(sync_session_generator=get_db, **({"make_session": factory} if WITH_FACTORY else {}))
SessionType = Session
ViewBase = fr.RestView
"""
        create_method = """
    def create(self, data):
        assert self.session is self.native
        return super().create(data)
"""
        session_fixture = "restly_session"
        dispose = "engine.dispose()"

    client_fixture = "restly_async_client" if async_client else "restly_client"
    test_prefix = (
        "@pytest.mark.asyncio\nasync def" if asynchronous or async_client else "def"
    )
    await_http = "await " if async_client else ""
    await_database = "await " if asynchronous else ""
    setup = (
        "fr.testing.configure_tests(app=app, base=fr.DataclassBase)"
        if managed
        else "@pytest.fixture\ndef restly_app():\n    return app"
    )
    client_only_tests = (
        """
def test_c_client_only_write(restly_client):
    response = restly_client.post("/notes", json={"text": "client-only"})
    assert response.status_code == 201

def test_d_client_only_clean(restly_client):
    response = restly_client.get("/notes")
    assert response.json()["total_count"] == 0
"""
        if managed
        else ""
    )
    pytester.makeconftest(f"""
import asyncio
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, Session, sessionmaker
from sqlalchemy.pool import StaticPool
import fastapi_restly as fr

WITH_FACTORY = {with_factory!r}
calls = []
{database_setup}

class Note(fr.IDBase):
    text: Mapped[str]

class NoteSchema(fr.IDSchema):
    text: str

app = FastAPI()

@fr.include_view(app)
class NoteView(ViewBase):
    prefix = "/notes"
    model = Note
    schema = NoteSchema
    native: Annotated[SessionType, Depends(get_db)]
{create_method}

schema_engine = create_engine("sqlite:///test.db") if {asynchronous!r} else engine
fr.DataclassBase.metadata.create_all(schema_engine)
if {asynchronous!r}:
    schema_engine.dispose()

{setup}

@pytest.fixture(scope="session", autouse=True)
def cleanup():
    yield
    assert get_db not in app.dependency_overrides
    {dispose}
""")
    pytester.makepyfile(f"""
import pytest
from sqlalchemy import select
from conftest import Note, calls

{test_prefix} test_a_write({client_fixture}, {session_fixture}):
    before = len(calls)
    response = {await_http}{client_fixture}.post("/notes", json={{"text": "isolated"}})
    assert response.status_code == 201
    assert len(calls) == before
    found = {await_database}{session_fixture}.scalars(select(Note))
    assert [note.text for note in found] == ["isolated"]

{test_prefix} test_b_clean({session_fixture}, {client_fixture}):
    response = {await_http}{client_fixture}.get("/notes")
    assert response.json()["total_count"] == 0

{client_only_tests}
""")
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=4 if managed else 2)
