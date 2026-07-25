"""State behind :func:`fastapi_restly.testing.configure_tests`.

Lives at the package root rather than in ``testing/`` so the pytest plugin can
read it without importing ``testing/__init__.py``, which pulls in the HTTP test
client (and with it httpx). Users reach it as ``fr.testing.configure_tests()``.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .db import _session
from .db._globals import _fr_globals
from .exc import RestlyConfigurationError

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy import Engine, MetaData
    from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
    from sqlalchemy.orm import DeclarativeBase, sessionmaker

#: Roll each test back through a savepoint. Fastest, and nothing survives a test.
ROLLBACK = "rollback"
#: Empty the tables before each test. Writes commit, so the last test's rows stay.
TRUNCATE = "truncate"
#: Clean nothing; the suite owns it. The fallback when neither of the above fits.
NONE = "none"
DB_CLEANUP_MODES = (ROLLBACK, TRUNCATE, NONE)

#: Overrides the ``db_cleanup=`` argument; ``--restly-db-cleanup`` overrides this.
DB_CLEANUP_ENV_VAR = "RESTLY_DB_CLEANUP"

#: Alembic's bookkeeping table is never test data, and emptying it would strand
#: the database at no revision for the rest of the session.
_NEVER_TRUNCATED = frozenset({"alembic_version"})


@dataclass(frozen=True)
class _TestSetup:
    """What :func:`configure_tests` recorded, read by the plugin's autouse fixtures."""

    app: Any
    create_all_from: Any
    alembic_upgrade: bool | str | Path
    db_cleanup: str
    db_cleanup_exclude: tuple[str, ...]


_setup: _TestSetup | None = None


def _current_setup() -> _TestSetup | None:
    """Return the active test setup, or None when ``configure()`` was never called.

    The plugin's autouse fixtures are inert while this is None, so a project that
    does not opt in keeps the fixtures-on-request behaviour.
    """
    return _setup


def _reset_setup() -> None:
    """Drop the recorded setup. For Restly's own tests, not for user suites."""
    global _setup, _cached_for
    _setup = None
    _cached_for = None


def configure_tests(
    *,
    app: FastAPI | None = None,
    database_url: str | None = None,
    async_database_url: str | None = None,
    engine: Engine | None = None,
    async_engine: AsyncEngine | None = None,
    make_session: sessionmaker[Any] | None = None,
    async_make_session: async_sessionmaker[Any] | None = None,
    create_all_from: type[DeclarativeBase] | MetaData | None = None,
    alembic_upgrade: bool | str | Path = False,
    db_cleanup: str = ROLLBACK,
    db_cleanup_exclude: Sequence[str] = (),
) -> None:
    """Configure a Restly test suite in one call, from ``conftest.py``.

    The testing counterpart to :func:`fastapi_restly.configure`. Call it at
    ``conftest.py`` import time::

        import fastapi_restly as fr
        from myapp.main import app
        from myapp.models import Base

        fr.testing.configure_tests(
            app=app,
            async_database_url="sqlite+aiosqlite:///./test.db",
            create_all_from=Base,
        )

    That is the whole setup. Every test then runs against ``app`` on the given
    database and starts from a clean one, including tests that only drive
    ``restly_client``. How that cleaning happens is ``db_cleanup`` below; by
    default each test is rolled back and nothing is ever committed.

    Four things happen, in this order:

    1. The database arguments are forwarded to :func:`fastapi_restly.configure`,
       under the same names. They replace only the legs they name, so if your
       application configured a sync database and you name only the async one,
       this raises: the leg you left out would still be your application's, and
       every route resolving through it would read and write there.
    2. ``app`` becomes what the ``restly_app`` fixture returns, so ``restly_client``
       wraps your application without an override fixture.
    3. The schema is created once per session, before any test runs, from
       ``create_all_from`` or ``alembic_upgrade`` (see below).
    4. Every test gets a clean database, by the strategy ``db_cleanup`` names.

    Schema setup is optional and the two options are mutually exclusive:

    * ``create_all_from=Base`` builds the schema straight from your models, as
      :func:`fastapi_restly.db.create_all` does.
    * ``alembic_upgrade=True`` runs ``alembic upgrade head`` through ``alembic.ini``
      next to your project root; pass a path to point at a different config.
      Restly sets ``sqlalchemy.url`` on the config to the database configured
      here, so a stock ``env.py`` that reads it migrates the test database rather
      than your development one.
    * Passing neither leaves the schema to you, which is the right choice when
      your suite already builds it or your migrations are not Alembic.

    Always name the database. If one is already configured (your application
    module calls :func:`fastapi_restly.configure` on import) and you pass no
    database argument, this raises rather than guess: that database is usually
    the development one, and the schema step would create tables in it.

    ``db_cleanup`` chooses how each test gets a clean database:

    * ``"rollback"`` (the default) wraps every test in a transaction that is
      rolled back through a savepoint when the test ends. Nothing is ever
      committed, which makes it the fastest option and the reason no other
      process can see a test's data, not even after a failure.
    * ``"truncate"`` empties the tables *before* each test instead, and lets
      writes commit for real. It is slower, but the last test's rows are still in
      the database when the run ends, so you can inspect them with ordinary tools;
      run with ``-k`` and the last test is the one you are looking at. Tests still cannot see each other's data, and it needs a database of
      its own: two suites truncating one database will fight.
    * ``"none"`` cleans nothing and leaves it to you. Reach for it when neither of
      the others fits: tests that drive a browser or a second process (nothing
      uncommitted is visible to those, and truncation across parallel workers
      collides), or a database user without the rights to truncate.

    ``RESTLY_DB_CLEANUP`` overrides this argument, and ``--restly-db-cleanup``
    overrides both, so a debugging run can switch mode without editing the suite.

    ``db_cleanup_exclude`` names tables truncation must leave alone. Reference
    data seeded by a migration is the usual reason: truncation would empty those
    tables before the first test and nothing would put the rows back. Naming a
    table that does not exist raises, so a typo cannot silently drop the
    protection. Excluded tables are shared by every test, so writes to them do
    leak between tests.
    """
    global _setup

    if db_cleanup not in DB_CLEANUP_MODES:
        raise RestlyConfigurationError(
            f"fr.testing.configure_tests() got db_cleanup="
            f"{db_cleanup!r}; expected one of "
            f"{', '.join(repr(mode) for mode in DB_CLEANUP_MODES)}."
        )

    if create_all_from is not None and alembic_upgrade:
        raise RestlyConfigurationError(
            "fr.testing.configure_tests() got both create_all_from= and "
            "alembic_upgrade=. They are two ways to build the same schema: pass "
            "create_all_from=<Base> to create the tables from your models, or "
            "alembic_upgrade=True to run your migrations, not both."
        )

    # Which legs the application had before we touch anything: fr.configure()
    # replaces only the leg it is given, so an unnamed one survives into the tests.
    inherited_sync = _fr_globals.make_session is not None
    inherited_async = _fr_globals.async_make_session is not None

    names_sync = any(
        argument is not None for argument in (database_url, engine, make_session)
    )
    names_async = any(
        argument is not None
        for argument in (async_database_url, async_engine, async_make_session)
    )
    if names_sync or names_async:
        _session.configure(
            app=app,
            database_url=database_url,
            async_database_url=async_database_url,
            engine=engine,
            async_engine=async_engine,
            make_session=make_session,
            async_make_session=async_make_session,
        )
        _reject_unnamed_legs(
            names_sync=names_sync,
            names_async=names_async,
            inherited_sync=inherited_sync,
            inherited_async=inherited_async,
        )
    else:
        _reject_inherited_database()

    _setup = _TestSetup(
        app=app,
        create_all_from=create_all_from,
        alembic_upgrade=alembic_upgrade,
        db_cleanup=db_cleanup,
        db_cleanup_exclude=tuple(db_cleanup_exclude),
    )


def _resolve_db_cleanup(setup: _TestSetup, flag: str | None) -> str:
    """Return the mode in force, honouring the flag over the environment over the
    argument. Both overrides exist so a debugging run can switch mode without
    touching the suite."""
    for source, value in (
        ("--restly-db-cleanup", flag),
        (DB_CLEANUP_ENV_VAR, os.environ.get(DB_CLEANUP_ENV_VAR)),
    ):
        if not value:
            continue
        if value not in DB_CLEANUP_MODES:
            raise RestlyConfigurationError(
                f"{source} is {value!r}; expected one of "
                f"{', '.join(repr(mode) for mode in DB_CLEANUP_MODES)}."
            )
        return value
    return setup.db_cleanup


#: The table list for the setup it was computed under. The schema is built once
#: per session, so reflecting it again before every test only costs round trips.
_cached_for: _TestSetup | None = None
_cached_tables: list[Any] = []


def _tables_to_clean(setup: _TestSetup, bind: Any) -> list[Any]:
    """Return the tables truncation should empty, parents last, computing the
    list once per setup rather than before every test."""
    global _cached_for, _cached_tables
    if _cached_for is not setup:
        _cached_tables = _resolve_tables_to_clean(setup, bind)
        _cached_for = setup
    return _cached_tables


def _resolve_tables_to_clean(setup: _TestSetup, bind: Any) -> list[Any]:
    """Work out the tables truncation should empty, parents last.

    ``create_all_from`` already names the metadata. Migrations do not, so the
    database is reflected instead, which also picks up tables no model declares.
    """
    from sqlalchemy import MetaData

    if setup.create_all_from is not None:
        metadata = _session._resolve_metadata(setup.create_all_from)
    else:
        metadata = MetaData()
        # views=False keeps views out of the list; emptying one is an error.
        metadata.reflect(bind=bind, views=False)
    known = {table.name for table in metadata.sorted_tables}
    unknown = sorted(set(setup.db_cleanup_exclude) - known)
    if unknown:
        # A typo here would silently drop the protection and empty the very table
        # the caller was trying to keep, so refuse instead.
        raise RestlyConfigurationError(
            "fr.testing.configure_tests(db_cleanup_exclude=...) names "
            f"{', '.join(repr(name) for name in unknown)}, which "
            f"{'is' if len(unknown) == 1 else 'are'} not among the tables it would "
            f"empty: {', '.join(sorted(known))}."
        )

    spared = _NEVER_TRUNCATED | set(setup.db_cleanup_exclude)
    # sorted_tables puts parents first, so deleting in reverse respects foreign
    # keys on databases that enforce them without CASCADE.
    return [
        table for table in reversed(metadata.sorted_tables) if table.name not in spared
    ]


def _clean_tables(connection: Any, tables: list[Any]) -> None:
    """Empty ``tables`` on ``connection``, by whatever the dialect supports."""
    from sqlalchemy import text

    if not tables:
        return

    if connection.dialect.name == "postgresql":
        # One statement rather than a delete and a sequence reset per table, which
        # matters when this runs before every test. Truncating the tables together
        # satisfies the foreign keys among them, and RESTART IDENTITY makes ids
        # repeatable. Deliberately not CASCADE: that would silently empty tables
        # outside this list that reference these, so let PostgreSQL raise instead.
        # The dialect renders the names: it keeps the schema on a qualified table,
        # which hand-quoting drops, and escapes anything needing it.
        preparer = connection.dialect.identifier_preparer
        names = ", ".join(preparer.format_table(table) for table in tables)
        connection.exec_driver_sql(f"TRUNCATE {names} RESTART IDENTITY")
        return

    for table in tables:
        connection.execute(table.delete())
    if connection.dialect.name == "sqlite":
        # SQLite keeps AUTOINCREMENT counters here, and only for tables declared
        # with sqlite_autoincrement. Restart the ones being emptied, and leave an
        # excluded table's counter alone: its rows are still there.
        has_sequence = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        ).first()
        if has_sequence:
            placeholders = ", ".join(f":name_{index}" for index in range(len(tables)))
            connection.execute(
                text(f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})"),
                {f"name_{index}": table.name for index, table in enumerate(tables)},
            )


def _resolve_engine(bind: Any) -> Any:
    """Return the engine behind ``bind``, which a caller may have set to a
    Connection just as ``fr.configure(make_session=...)`` allows.

    Only a Connection is unwrapped. ``AsyncEngine`` also carries an ``engine``
    attribute, but it is the sync engine underneath, which cannot be awaited.
    """
    from sqlalchemy import Connection
    from sqlalchemy.ext.asyncio import AsyncConnection

    if isinstance(bind, (Connection, AsyncConnection)):
        return bind.engine
    return bind


def _clean_database(setup: _TestSetup) -> None:
    """Empty every table before a test runs, for ``db_cleanup="truncate"``.

    Runs before rather than after, so whatever the last test wrote is still in
    the database when the run ends and can be inspected.
    """
    if _fr_globals.make_session is not None:
        engine = _resolve_engine(_fr_globals.make_session.kw["bind"])
        with engine.begin() as connection:
            _clean_tables(connection, _tables_to_clean(setup, connection))
        return

    if _fr_globals.async_make_session is None:
        if (
            _fr_globals.session_generator is not None
            or _fr_globals.sync_session_generator is not None
        ):
            raise RestlyConfigurationError(
                'fr.testing.configure_tests(db_cleanup="truncate") builds its own '
                "connection to empty the tables, and a session_generator does not "
                "give it one. Configure a sessionmaker for the tests as well: pass "
                "database_url=, engine= or make_session= (or their async forms)."
            )
        # No database at all, which configure_tests() allows. Nothing to empty.
        return

    async_engine = _resolve_engine(_fr_globals.async_make_session.kw["bind"])

    async def clean() -> None:
        async with async_engine.begin() as connection:
            tables = await connection.run_sync(
                lambda sync_connection: _tables_to_clean(setup, sync_connection)
            )
            await connection.run_sync(
                lambda sync_conn: _clean_tables(sync_conn, tables)
            )

    # Safe from a sync fixture: pytest sets fixtures up outside the loop it runs
    # the test coroutine in, so no loop is running here.
    asyncio.run(clean())


def _safe_url(url: str | None) -> str:
    """Render ``url`` without its password, for messages that reach CI logs."""
    if not url:
        return "the configured database"
    try:
        from sqlalchemy import make_url

        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "the configured database"


def _reject_unnamed_legs(
    *, names_sync: bool, names_async: bool, inherited_sync: bool, inherited_async: bool
) -> None:
    """Refuse a leg the application configured and this call did not name.

    :func:`fastapi_restly.configure` replaces only the leg it is passed, so naming
    just one here leaves the other pointing wherever the application left it,
    usually the development database. Requests that resolve through the unnamed
    leg would then read and write there, and truncation would empty it.
    """
    if inherited_sync and not names_sync:
        leg, argument, url = "sync", "database_url=", _fr_globals.database_url
    elif inherited_async and not names_async:
        leg, argument, url = (
            "async",
            "async_database_url=",
            _fr_globals.async_database_url,
        )
    else:
        return

    raise RestlyConfigurationError(
        f"fr.testing.configure_tests() named the {'async' if leg == 'sync' else 'sync'} "
        f"database but not the {leg} one, and your application already configured a "
        f"{leg} database ({_safe_url(url)}). fr.configure() replaces only the leg it "
        f"is given, so that one would survive into the tests: {leg} routes would read "
        "and write there, and truncation would empty it. Pass "
        f"{argument} as well, pointing at the same test database."
    )


def _reject_inherited_database() -> None:
    """Refuse to run the tests against a database nobody named here.

    Reached when ``configure_tests()`` got no database argument but one is already
    configured, which normally means the application module configured it on
    import. Silently inheriting it would point the schema step at that database,
    and ``create_all``/``alembic upgrade`` is DDL: it survives the per-test
    rollback.
    """
    if _fr_globals.make_session is None and _fr_globals.async_make_session is None:
        return  # No database anywhere: a suite that never touches one is fine.

    configured = _fr_globals.database_url or _fr_globals.async_database_url
    named = f" ({_safe_url(configured)})" if configured else ""
    raise RestlyConfigurationError(
        f"fr.testing.configure_tests() got no database argument, but a database"
        f"{named} is already configured -- usually the development one, "
        "configured when your application module was imported. Restly will not "
        "run the tests against it: creating the schema there would leave tables "
        "behind, since DDL survives the per-test rollback. Pass the test "
        "database explicitly, e.g. database_url= or async_database_url=. If the "
        "configured database really is the test one, pass the same URL here."
    )


def _create_schema(setup: _TestSetup, root: Path | None = None) -> None:
    """Build the schema described by ``setup``, once, before the first test.

    ``root`` anchors relative Alembic paths; the plugin passes pytest's rootdir so
    the config is found no matter which directory pytest was invoked from.
    """
    if setup.create_all_from is not None:
        # Prefer the sync leg: it needs no event loop. Either leg creates the
        # tables the other one sees, since both point at the same database.
        if _fr_globals.make_session is not None:
            _session.create_all(setup.create_all_from)
        elif _fr_globals.async_make_session is not None:
            asyncio.run(_session.async_create_all(setup.create_all_from))
        else:
            raise RestlyConfigurationError(
                "fr.testing.configure_tests(create_all_from=...) needs a configured "
                "database. Pass database_url= or async_database_url= to "
                "fr.testing.configure_tests(), or call fr.configure() before it."
            )
    elif setup.alembic_upgrade:
        _run_alembic_upgrade(setup.alembic_upgrade, root)


def _configured_url() -> str | None:
    """The URL of the database the tests were pointed at, however it was given.

    ``configure_tests(engine=...)`` and the sessionmaker forms record no URL, so
    read it back off the bind. Prefer the sync leg: Alembic drives a sync engine,
    and an async URL is one a stock ``env.py`` cannot open.
    """
    for recorded, factory in (
        (_fr_globals.database_url, _fr_globals.make_session),
        (_fr_globals.async_database_url, _fr_globals.async_make_session),
    ):
        if recorded is not None:
            return recorded
        if factory is None:
            continue
        engine = _resolve_engine(factory.kw.get("bind"))
        if engine is not None and getattr(engine, "url", None) is not None:
            return engine.url.render_as_string(hide_password=False)
    return None


def _run_alembic_upgrade(
    alembic_upgrade: bool | str | Path, root: Path | None = None
) -> None:
    """Run ``alembic upgrade head`` against the configured test database."""
    try:
        from alembic import command
        from alembic.config import Config
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on the env
        raise ModuleNotFoundError(
            "fr.testing.configure_tests(alembic_upgrade=...) requires Alembic. "
            "Install it with: pip install alembic",
            name="alembic",
        ) from exc

    base = root if root is not None else Path.cwd()
    given = Path("alembic.ini" if alembic_upgrade is True else str(alembic_upgrade))
    # Anchor to the project, not the directory pytest happened to run from, so the
    # suite migrates the same schema wherever it is invoked.
    ini_path = given if given.is_absolute() else base / given
    if not ini_path.exists():
        raise RestlyConfigurationError(
            f"fr.testing.configure_tests(alembic_upgrade=...) found no Alembic config at "
            f"{str(ini_path)!r}. Pass a path relative to your project root, e.g. "
            "alembic_upgrade='backend/alembic.ini'."
        )

    config = Config(str(ini_path))
    # script_location is normally relative to the config file; resolve it here so
    # the migrations are found regardless of the working directory.
    script_location = config.get_main_option("script_location")
    if script_location and not Path(script_location).is_absolute():
        config.set_main_option(
            "script_location", str((ini_path.parent / script_location).resolve())
        )
    # Point Alembic at the database configured for the tests. Without this the
    # upgrade runs against whatever env.py resolves on its own, typically the
    # development database, leaving the test database unmigrated.
    url = _configured_url()
    if url is None:
        raise RestlyConfigurationError(
            "fr.testing.configure_tests(alembic_upgrade=...) could not work out "
            "which database to migrate. It would otherwise fall through to the URL "
            "in your alembic.ini, which is normally the development one, and "
            "migrate that instead. Configure the tests with database_url= or "
            "async_database_url=, or with an engine Restly can read a URL from."
        )
    # Alembic stores this in a ConfigParser with interpolation on, where a bare %
    # is a syntax error. Percent-encoded passwords are ordinary, and the resulting
    # ValueError would echo the whole URL into the log.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
