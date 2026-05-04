from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession as SAAsyncSession
from sqlalchemy.orm import Session as SASession

from fastapi_restly.db import async_generate_session, configure, generate_session
from fastapi_restly.db._globals import _fr_globals


@pytest.fixture(autouse=True)
def restore_session_generators():
    original_async_generator = _fr_globals.session_generator
    original_sync_generator = _fr_globals.sync_session_generator
    yield
    _fr_globals.session_generator = original_async_generator
    _fr_globals.sync_session_generator = original_sync_generator


def test_configure_sets_async_database_url():
    configure(async_database_url="sqlite+aiosqlite:///from-configure.db")

    assert _fr_globals.async_database_url == "sqlite+aiosqlite:///from-configure.db"


def test_configure_sets_sync_database_url():
    configure(database_url="sqlite+pysqlite:///from-configure.db")

    assert _fr_globals.database_url == "sqlite+pysqlite:///from-configure.db"


@pytest.mark.asyncio
async def test_async_generate_session_uses_configured_session_generator():
    yielded = []

    class DummyAsyncSession:
        is_active = False

    async def my_generator() -> AsyncIterator[SAAsyncSession]:
        yielded.append("called")
        yield DummyAsyncSession()  # type: ignore[misc]

    configure(session_generator=my_generator)

    sessions = []
    async for session in async_generate_session():
        sessions.append(session)

    assert yielded == ["called"]
    assert len(sessions) == 1


def test_generate_session_uses_configured_sync_session_generator():
    yielded = []

    class DummySyncSession:
        is_active = False

    def my_generator() -> Iterator[SASession]:
        yielded.append("called")
        yield DummySyncSession()  # type: ignore[misc]

    configure(sync_session_generator=my_generator)

    sessions = list(generate_session())

    assert yielded == ["called"]
    assert len(sessions) == 1
