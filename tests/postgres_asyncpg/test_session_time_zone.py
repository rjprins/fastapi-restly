"""An engine that Restly builds from a URL puts each session in UTC, on asyncpg.

The psycopg leg has the same tests; see ``tests/postgres``. asyncpg shows
the time zone of the session in its settings, behind SQLAlchemy's adapter.
"""

import pytest
from sqlalchemy import Engine, event, text
from sqlalchemy.ext.asyncio import create_async_engine

import fastapi_restly as fr
from fastapi_restly.db._config import RestlyContext
from fastapi_restly.db._engine_defaults import _session_time_zone

from .conftest import ASYNCPG_URL

assert ASYNCPG_URL is not None
# str(URL) masks the password; the driver needs the real one.
URL = ASYNCPG_URL.render_as_string(hide_password=False)


def _start_in_new_york(engine: Engine) -> None:
    """Start each new session of ``engine`` in New York, before Restly's own
    listener runs."""

    @event.listens_for(engine, "connect", insert=True)
    def _new_york(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("SET TIME ZONE 'America/New_York'")
        cursor.close()
        dbapi_connection.commit()


@pytest.mark.asyncio
async def test_an_engine_from_a_url_puts_the_session_in_utc_with_asyncpg():
    """Also after a rollback: the SET is committed."""
    with RestlyContext():
        fr.configure(async_database_url=URL)
        engine = fr.db.get_async_engine()
        _start_in_new_york(engine.sync_engine)
        try:
            async with engine.connect() as connection:
                zone = await connection.execute(text("SHOW TIME ZONE"))
                assert zone.scalar() == "UTC"
                await connection.rollback()
                zone = await connection.execute(text("SHOW TIME ZONE"))
                assert zone.scalar() == "UTC"
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_an_engine_that_you_pass_in_keeps_the_time_zone_with_asyncpg():
    own = create_async_engine(URL)
    _start_in_new_york(own.sync_engine)
    try:
        async with own.connect() as connection:
            zone = await connection.execute(text("SHOW TIME ZONE"))
            assert zone.scalar() == "America/New_York"
    finally:
        await own.dispose()


@pytest.mark.asyncio
async def test_an_engine_of_your_own_puts_the_session_in_utc_with_the_docs_recipe():
    """The recipe of the docs for asyncpg: a server setting at the start. It
    matters on a server whose sessions start in another zone, as the one of
    ``scripts/with_postgres.sh`` and CI does."""
    own = create_async_engine(
        URL, connect_args={"server_settings": {"timezone": "UTC"}}
    )
    try:
        async with own.connect() as connection:
            zone = await connection.execute(text("SHOW TIME ZONE"))
            assert zone.scalar() == "UTC"
    finally:
        await own.dispose()


@pytest.mark.asyncio
async def test_the_time_zone_that_asyncpg_reports_is_the_one_of_the_session():
    """Restly reads it without a query, so a session in UTC gets no SET."""
    own = create_async_engine(URL)
    try:
        async with own.connect() as connection:
            raw = await connection.get_raw_connection()
            reported = _session_time_zone(raw.dbapi_connection)
            shown = await connection.execute(text("SHOW TIME ZONE"))
            assert reported == shown.scalar()
    finally:
        await own.dispose()
