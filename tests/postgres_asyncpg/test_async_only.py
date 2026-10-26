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
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from sqlalchemy import DateTime, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.pool import NullPool

import fastapi_restly as fr
from fastapi_restly._integrity import _build_integrity_detail
from fastapi_restly.exc import RestlyConfigurationError

from .._models import SharedBase
from .conftest import ASYNCPG_URL

assert ASYNCPG_URL is not None

engine = create_async_engine(ASYNCPG_URL)
assert not isinstance(engine.pool, NullPool)
fr.configure(async_engine=engine)
CLEANUP = os.environ.get("RESTLY_ASYNCPG_CLEANUP", "rollback")


class AsyncpgNote(fr.TimestampsMixin, fr.IDMixin, SharedBase):
    __tablename__ = "restly_asyncpg_note"

    text: Mapped[str]
    local_time: Mapped[datetime | None] = mapped_column(DateTime(), default=None)
    # a column without a time zone, for UTC
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(), default=None)
    # a column with a time zone that a request body can set
    due_at: Mapped[datetime | None] = mapped_column(default=None)


class AsyncpgNoteSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    text: str
    local_time: datetime | None = None
    starts_at: datetime | None = None
    due_at: datetime | None = None


class AsyncpgTag(fr.IDMixin, SharedBase):
    __tablename__ = "restly_asyncpg_tag"

    name: Mapped[str] = mapped_column(unique=True)


class AsyncpgTagSchema(fr.IDSchema):
    name: str


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


@fr.include_view(app)
class AsyncpgNoteView(fr.AsyncRestView):
    prefix = "/filtered-notes"
    model = AsyncpgNote
    schema = AsyncpgNoteSchema


@fr.include_view(app)
class AsyncpgTagView(fr.AsyncRestView):
    prefix = "/tags"
    model = AsyncpgTag
    schema = AsyncpgTagSchema


fr.testing.configure_tests(
    app=app, base=SharedBase, create_all=True, db_cleanup=CLEANUP
)


@pytest.fixture(scope="session", autouse=True)
def drop_test_table() -> Iterator[None]:
    yield

    async def drop() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(AsyncpgNote.__table__.drop, checkfirst=True)
            await connection.run_sync(AsyncpgTag.__table__.drop, checkfirst=True)
        await engine.dispose()

    asyncio.run(drop())


@pytest.mark.asyncio
async def test_direct_database_access_without_a_public_fixture():
    """The internal scope owns even fixture-less unit-of-work access."""
    async with fr.open_async_session() as session:
        await session.execute(text("select 1"))


@pytest.fixture
def blocked_direct_access() -> RestlyConfigurationError:
    """Try fixture-side DB access while the test's sync client owns the loop."""

    async def access() -> None:
        async with fr.open_async_session() as session:
            await session.execute(text("select 1"))

    with pytest.raises(
        RestlyConfigurationError, match="cannot open an async database session"
    ) as excinfo:
        asyncio.run(access())
    return excinfo.value


def test_sync_client_blocks_fixture_side_async_access(
    blocked_direct_access: RestlyConfigurationError, restly_client
):
    assert "restly_async_client" in str(blocked_direct_access)


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


@pytest.mark.asyncio
async def test_naive_datetime_filter_is_utc_with_asyncpg(
    restly_async_client, restly_async_session: AsyncSession
):
    """A timezone-less API value must not depend on the process timezone."""
    instant = datetime(2024, 6, 15, 12, tzinfo=timezone.utc)
    restly_async_session.add(
        AsyncpgNote(text="known UTC instant", created_at=instant, updated_at=instant)
    )
    await restly_async_session.commit()

    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    try:
        response = await restly_async_client.get(
            "/filtered-notes",
            params={
                "created_at__gte": "2024-06-15T12:00:00",
                "created_at__lte": "2024-06-15T12:00:00",
            },
        )
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()

    assert response.status_code == 200
    assert [item["text"] for item in response.json()["data"]] == ["known UTC instant"]
    serialized = response.json()["data"][0]["created_at"]
    assert datetime.fromisoformat(serialized.replace("Z", "+00:00")).tzinfo is not None


@pytest.mark.asyncio
async def test_explicitly_naive_datetime_filter_stays_naive_with_asyncpg(
    restly_async_client, restly_async_session: AsyncSession
):
    """An explicit DateTime() opt-out keeps wall-clock filter semantics."""
    wall_time = datetime(2024, 7, 1, 9, 30)
    restly_async_session.add(AsyncpgNote(text="known wall time", local_time=wall_time))
    await restly_async_session.commit()

    response = await restly_async_client.get(
        "/filtered-notes",
        params={
            "local_time__gte": "2024-07-01T09:30:00",
            "local_time__lte": "2024-07-01T09:30:00",
        },
    )

    assert response.status_code == 200
    assert [item["text"] for item in response.json()["data"]] == ["known wall time"]
    assert response.json()["data"][0]["local_time"] == "2024-07-01T09:30:00"


@contextmanager
def _process_time_zone(name: str) -> Iterator[None]:
    """Run the block with ``name`` as the local time zone of the process."""
    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()


@pytest.mark.asyncio
async def test_a_body_time_without_an_offset_is_utc_with_asyncpg(restly_async_client):
    """asyncpg reads a time without an offset, for a column with a time zone,
    in the time zone of the process. Restly sends it as UTC, so the filter
    finds the stored time."""
    with _process_time_zone("Asia/Tokyo"):
        created = await restly_async_client.post(
            "/filtered-notes",
            json={"text": "due at noon UTC", "due_at": "2024-08-01T12:00:00"},
        )
        response = await restly_async_client.get(
            "/filtered-notes",
            params={
                "due_at__gte": "2024-08-01T12:00:00",
                "due_at__lte": "2024-08-01T12:00:00",
            },
        )

    assert created.json()["due_at"] == "2024-08-01T12:00:00Z"
    assert [item["text"] for item in response.json()["data"]] == ["due at noon UTC"]


@pytest.mark.asyncio
async def test_a_time_with_an_offset_is_utc_for_a_column_without_one_with_asyncpg(
    restly_async_client,
):
    """asyncpg rejects a time with an offset for a column without a time
    zone. Restly sends the time in UTC, without the offset, in a body and in
    a filter."""
    created = await restly_async_client.post(
        "/filtered-notes",
        json={"text": "starts in Berlin", "starts_at": "2024-09-01T10:30:00+02:00"},
    )
    response = await restly_async_client.get(
        "/filtered-notes",
        params={
            "starts_at__gte": "2024-09-01T10:30:00+02:00",
            "starts_at__lte": "2024-09-01T08:30:00Z",
        },
    )

    assert created.json()["starts_at"] == "2024-09-01T08:30:00"
    assert response.status_code == 200
    assert [item["text"] for item in response.json()["data"]] == ["starts in Berlin"]


@pytest.mark.asyncio
async def test_a_time_out_of_range_in_utc_is_a_client_error_with_asyncpg(
    restly_async_client,
):
    """asyncpg turns a time with an offset into UTC for a column with a time
    zone, and fails for a time that UTC cannot hold. Restly rejects the time
    first, in a body and in a filter."""
    late = "9999-12-31T23:30:00-01:00"
    await restly_async_client.post(
        "/filtered-notes",
        json={"text": "too late", "due_at": late},
        assert_status_code=422,
    )
    await restly_async_client.get(
        "/filtered-notes", params={"due_at__gte": late}, assert_status_code=400
    )


@pytest.mark.asyncio
async def test_a_unique_violation_names_the_constraint_with_asyncpg(
    restly_async_client,
):
    """asyncpg has no psycopg ``diag``: the 409 still names the constraint."""
    await restly_async_client.post("/tags", json={"name": "taken"})

    response = await restly_async_client.post(
        "/tags", json={"name": "taken"}, assert_status_code=409
    )

    assert response.json()["detail"] == (
        "Unique constraint violated: restly_asyncpg_tag_name_key"
    )


@pytest.mark.asyncio
async def test_a_not_null_violation_names_the_column_with_asyncpg(
    restly_async_session: AsyncSession,
):
    """The session drives the violation: a NOT NULL column is a required
    request field, so a request would get a 422 first."""
    restly_async_session.add(AsyncpgTag(name=None))  # type: ignore[arg-type]
    with pytest.raises(IntegrityError) as excinfo:
        await restly_async_session.flush()

    assert _build_integrity_detail(excinfo.value) == (
        "Not-null constraint violated on column 'name'"
    )


class _NoteText(fr.ContextNamespace):
    text: fr.ContextParam[str]


class _OtherNoteText(fr.ContextNamespace):
    """A second namespace that picks the same member name."""

    text: fr.ContextParam[str]


# Last in the module on purpose: the count tests above expect exact row counts.
@pytest.mark.asyncio
async def test_same_name_members_bind_separately_with_asyncpg(
    restly_async_session: AsyncSession,
):
    """asyncpg prepares statements with $1, $2: each placeholder needs its own."""
    restly_async_session.add_all(
        [AsyncpgNote(text=f"member-{letter}") for letter in "abc"]
    )
    await restly_async_session.flush()
    either = fr.where_clause(
        or_(AsyncpgNote.text == _NoteText.text, AsyncpgNote.text == _OtherNoteText.text)
    )
    with _NoteText.bind(text="member-a"), _OtherNoteText.bind(text="member-c"):
        stmt = fr.apply_clauses(select(AsyncpgNote.text).distinct(), either)

    rows = (await restly_async_session.scalars(stmt)).all()
    assert sorted(rows) == ["member-a", "member-c"]
