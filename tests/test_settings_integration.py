from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession as SAAsyncSession
from sqlalchemy.orm import Session as SASession

from fastapi_restly._settings import settings
from fastapi_restly.db import (
    async_generate_session,
    fr_globals,
    generate_session,
    setup_async_database_connection,
    setup_database_connection,
)


@pytest.fixture(autouse=True)
def restore_settings_generators():
    original_async_url = settings.async_database_url
    original_sync_url = settings.database_url
    original_async_generator = settings.session_generator
    original_sync_generator = settings.sync_session_generator
    yield
    settings.async_database_url = original_async_url
    settings.database_url = original_sync_url
    settings.session_generator = original_async_generator
    settings.sync_session_generator = original_sync_generator


def test_setup_async_database_connection_uses_settings_default_url():
    settings.async_database_url = "sqlite+aiosqlite:///from-settings.db"

    setup_async_database_connection()

    assert fr_globals.async_database_url == "sqlite+aiosqlite:///from-settings.db"


def test_setup_database_connection_uses_settings_default_url():
    settings.database_url = "sqlite+pysqlite:///from-settings.db"

    setup_database_connection()

    assert fr_globals.database_url == "sqlite+pysqlite:///from-settings.db"


@pytest.mark.asyncio
async def test_async_generate_session_uses_configured_settings_generator():
    yielded = []

    class DummyAsyncSession:
        is_active = False

    async def my_generator() -> AsyncIterator[SAAsyncSession]:
        yielded.append("called")
        yield DummyAsyncSession()  # type: ignore[misc]

    settings.session_generator = my_generator

    sessions = []
    async for session in async_generate_session():
        sessions.append(session)

    assert yielded == ["called"]
    assert len(sessions) == 1


def test_generate_session_uses_configured_settings_generator():
    yielded = []

    class DummySyncSession:
        is_active = False

    def my_generator() -> Iterator[SASession]:
        yielded.append("called")
        yield DummySyncSession()  # type: ignore[misc]

    settings.sync_session_generator = my_generator

    sessions = list(generate_session())

    assert yielded == ["called"]
    assert len(sessions) == 1
