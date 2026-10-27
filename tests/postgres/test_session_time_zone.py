"""An engine that Restly builds from a URL puts each session in UTC, on psycopg.

A session that starts in another time zone gets ``SET TIME ZONE 'UTC'``.
PostgreSQL then reads a time without an offset as UTC, as Restly does, and
psycopg gives back each time in UTC. An engine that you pass in keeps the
time zone of its sessions. The tests start each session in New York, with a
listener that runs before Restly's.
"""

import pytest
from sqlalchemy import Engine, create_engine, event, text

import fastapi_restly as fr
from fastapi_restly.db._config import RestlyContext
from fastapi_restly.db._engine_defaults import _session_time_zone

from .conftest import PG_URL

# conftest gates collection on a PostgreSQL URL, so this is always set here.
assert PG_URL is not None
# str(URL) masks the password; the driver needs the real one.
URL = PG_URL.render_as_string(hide_password=False)
ASYNC_URL = PG_URL.set(drivername="postgresql+psycopg_async").render_as_string(
    hide_password=False
)


def _start_in_new_york(engine: Engine) -> None:
    """Start each new session of ``engine`` in New York, before Restly's own
    listener runs."""

    @event.listens_for(engine, "connect", insert=True)
    def _new_york(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("SET TIME ZONE 'America/New_York'")
        cursor.close()
        dbapi_connection.commit()


def test_an_engine_from_a_url_puts_the_session_in_utc():
    """Also after a rollback: the SET is committed."""
    with RestlyContext():
        fr.configure(database_url=URL)
        engine = fr.db.get_engine()
        _start_in_new_york(engine)
        try:
            with engine.connect() as connection:
                assert connection.execute(text("SHOW TIME ZONE")).scalar() == "UTC"
                connection.rollback()
                assert connection.execute(text("SHOW TIME ZONE")).scalar() == "UTC"
        finally:
            engine.dispose()


@pytest.mark.asyncio
async def test_an_async_engine_from_a_url_puts_the_session_in_utc():
    with RestlyContext():
        fr.configure(async_database_url=ASYNC_URL)
        engine = fr.db.get_async_engine()
        _start_in_new_york(engine.sync_engine)
        try:
            async with engine.connect() as connection:
                zone = await connection.execute(text("SHOW TIME ZONE"))
                assert zone.scalar() == "UTC"
        finally:
            await engine.dispose()


def test_an_engine_that_you_pass_in_keeps_the_time_zone_of_its_session():
    own = create_engine(URL)
    _start_in_new_york(own)
    try:
        with RestlyContext():
            fr.configure(engine=own)
            with fr.db.get_engine().connect() as connection:
                zone = connection.execute(text("SHOW TIME ZONE")).scalar()
                assert zone == "America/New_York"
    finally:
        own.dispose()


def test_the_time_zone_that_the_server_reports_is_the_one_of_the_session():
    """Restly reads it without a query, so a session in UTC gets no SET."""
    own = create_engine(URL)
    try:
        with own.connect() as connection:
            reported = _session_time_zone(connection.connection.dbapi_connection)
            shown = connection.execute(text("SHOW TIME ZONE")).scalar()
            assert reported == shown
    finally:
        own.dispose()
