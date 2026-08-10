"""Async-only PostgreSQL must be the managed fixtures' ordinary happy path.

These tests intentionally use asyncpg's normal pool.  They pin the two loop
topologies Restly has to own:

* ``restly_client`` keeps synchronous test functions, so its rollback
  transaction must live on the client's ASGI portal loop.
* ``restly_async_client`` and ``restly_async_session`` both live on pytest's
  loop, so direct database setup and requests share the same transaction.

The tests also run the application's lifespan against the database.  Passing
the request assertions but crossing loops during startup or teardown is still
a failure.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped
from sqlalchemy.pool import NullPool

import fastapi_restly as fr

from .conftest import ASYNCPG_URL

assert ASYNCPG_URL is not None

engine = create_async_engine(ASYNCPG_URL)
assert not isinstance(engine.pool, NullPool)
fr.configure(async_engine=engine)
CLEANUP = os.environ.get("RESTLY_ASYNCPG_CLEANUP", "rollback")


class AsyncpgNote(fr.IDBase):
    __tablename__ = "restly_asyncpg_note"

    text: Mapped[str]


LIFESPAN_EVENTS: list[str] = []


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    async with fr.open_async_session() as session:
        await session.execute(text("select 1"))
    LIFESPAN_EVENTS.append("startup")
    yield
    async with fr.open_async_session() as session:
        await session.execute(text("select 1"))
    LIFESPAN_EVENTS.append("shutdown")


app = FastAPI(lifespan=lifespan)


@app.post("/notes", status_code=201)
async def create_note(session: fr.AsyncSessionDep) -> dict[str, object]:
    note = AsyncpgNote(text="created through the API")
    session.add(note)
    await session.commit()
    return {"id": note.id, "text": note.text}


@app.get("/notes/count")
async def count_notes(session: fr.AsyncSessionDep) -> dict[str, int]:
    count = await session.scalar(select(func.count()).select_from(AsyncpgNote))
    return {"count": count or 0}


fr.testing.configure_tests(
    app=app, base=fr.DataclassBase, create_all=True, db_cleanup=CLEANUP
)


@pytest.fixture(scope="session", autouse=True)
def drop_test_table() -> Iterator[None]:
    yield

    async def drop() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(AsyncpgNote.__table__.drop, checkfirst=True)
        await engine.dispose()

    asyncio.run(drop())


@pytest.mark.asyncio
async def test_direct_database_access_without_a_public_fixture():
    """The internal scope owns even fixture-less unit-of-work access."""
    async with fr.open_async_session() as session:
        await session.execute(text("select 1"))


def test_sync_client_uses_asyncpg_and_commits_inside_the_test(restly_client):
    response = restly_client.post("/notes")

    assert response.json()["text"] == "created through the API"
    assert restly_client.get("/notes/count").json() == {"count": 1}
    assert LIFESPAN_EVENTS[-1] == "startup"


def test_sync_client_reuses_the_engine_on_a_fresh_loop(restly_client):
    expected = 1 if CLEANUP == "none" else 0
    assert restly_client.get("/notes/count").json() == {"count": expected}
    assert "shutdown" in LIFESPAN_EVENTS


@pytest.mark.asyncio
async def test_async_client_and_direct_session_share_the_transaction(
    restly_async_client, restly_async_session: AsyncSession
):
    restly_async_session.add(AsyncpgNote(text="created directly"))
    await restly_async_session.commit()

    response = await restly_async_client.get("/notes/count")

    expected = 2 if CLEANUP == "none" else 1
    assert response.json() == {"count": expected}


@pytest.mark.asyncio
async def test_async_client_reuses_the_engine_on_a_fresh_loop(restly_async_client):
    response = await restly_async_client.get("/notes/count")

    expected = 2 if CLEANUP == "none" else 0
    assert response.json() == {"count": expected}
