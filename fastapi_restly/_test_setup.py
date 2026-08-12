"""State behind :func:`fastapi_restly.testing.configure_tests`.

Lives at the package root rather than in ``testing/`` so the pytest plugin can
read it without importing ``testing/__init__.py``, which pulls in the HTTP test
client (and with it httpx). Users reach it as ``fr.testing.configure_tests()``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .db import _session
from .db._globals import _fr_globals
from .exc import RestlyConfigurationError

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy import MetaData
    from sqlalchemy.orm import DeclarativeBase

#: Roll each test back through a savepoint. Fastest, and nothing survives a test.
ROLLBACK = "rollback"
#: Empty the tables before each test. Writes commit, so the last test's rows stay.
DELETE = "delete"
#: Clean nothing; the suite owns it. The fallback when neither of the above fits.
NONE = "none"
DB_CLEANUP_MODES = (ROLLBACK, DELETE, NONE)

#: Overrides the ``db_cleanup=`` argument; ``--restly-db-cleanup`` overrides this.
DB_CLEANUP_ENV_VAR = "RESTLY_DB_CLEANUP"


@dataclass(frozen=True)
class _TestSetup:
    """What :func:`configure_tests` recorded, read by the plugin's autouse fixtures."""

    app: Any = None
    base: Any = None
    create_all: bool = False
    alembic_upgrade: bool | str | Path = False
    db_cleanup: str = ROLLBACK
    db_cleanup_exclude: tuple[str, ...] = ()
    #: The database configuration in force when the suite was configured.
    #: Schema setup and isolation use these sources, while consistency checks
    #: make an accidental later call to ``fr.configure(...)`` fail loudly.
    make_session: Any = None
    async_make_session: Any = None
    session_generator: Any = None
    sync_session_generator: Any = None
    database_url: str | None = None
    async_database_url: str | None = None


_setup: _TestSetup | None = None


def _source_factories() -> tuple[Any, Any]:
    """The factories isolation and schema setup build from."""
    if _setup is not None:
        return _setup.make_session, _setup.async_make_session
    return _fr_globals.make_session, _fr_globals.async_make_session


def _current_setup() -> _TestSetup | None:
    """Return the active test setup, or None when ``configure_tests()`` was
    never called.

    The plugin's autouse fixtures are inert while this is None, so a project that
    does not opt in keeps the fixtures-on-request behaviour.
    """
    return _setup


def _reset_setup() -> None:
    """Drop the recorded setup. For Restly's own tests, not for user suites."""
    global _setup
    _setup = None
    _fr_globals.database_configuration_locked = False


def configure_tests(
    *,
    app: FastAPI | None = None,
    base: type[DeclarativeBase] | MetaData | None = None,
    create_all: bool = False,
    alembic_upgrade: bool | str | Path = False,
    db_cleanup: str = ROLLBACK,
    db_cleanup_exclude: Sequence[str] = (),
) -> None:
    """Add Restly's managed testing behaviour to an already-configured app.

    Call it from ``conftest.py`` after the application has configured Restly for
    its test database::

        import fastapi_restly as fr
        from myapp.main import app
        from myapp.models import Base

        fr.testing.configure_tests(
            app=app,
            base=Base,
            create_all=True,
        )

    ``configure_tests()`` never chooses, creates or replaces a database engine.
    The application owns that configuration through :func:`fastapi_restly.configure`;
    this function records the session factories already in force. Point the
    application at a disposable test database before importing it, or construct
    it from explicit test settings. Database configuration performed afterwards
    is rejected so schema setup, cleanup and requests cannot disagree.

    ``app`` becomes what the ``restly_app`` fixture returns. The schema is
    optionally built once before tests start, and every test gets a clean
    database by the strategy ``db_cleanup`` names. Client-only tests are covered
    too.

    ``base=`` names the models the suite works with. It is what delete mode empties
    between tests, and what ``create_all`` builds. Pass your declarative base, or
    its ``MetaData``.

    Who builds the schema is a separate choice, and the two are mutually
    exclusive:

    * ``create_all=True`` builds it straight from ``base``, as
      :func:`fastapi_restly.db.create_all` does.
    * ``alembic_upgrade=True`` runs ``alembic upgrade head`` through ``alembic.ini``
      next to your project root; pass a path to point at a different config.
      Restly reads the URL from the application's configured engine and sets
      ``sqlalchemy.url`` on the config. It preserves that URL's driver, so an
      async-only application needs an Alembic ``env.py`` created from the async
      template, or otherwise adapted to run migrations with an async DBAPI.
    * Passing neither leaves the schema to you, which is the right choice when
      your suite already builds it or your migrations are not Alembic.

    The application is responsible for selecting a database that tests may
    modify. In particular, ``create_all=True`` and ``db_cleanup="delete"`` must
    only be used against a disposable test database. One call configures the
    whole pytest process; a second call raises.

    ``db_cleanup`` chooses how each test gets a clean database:

    * ``"rollback"`` (the default) wraps every test in a transaction that is
      rolled back through a savepoint when the test ends. Nothing is ever
      committed, which makes it the fastest option and the reason no other
      process can see a test's data, not even after a failure.
    * ``"delete"`` empties the tables *before* each test instead, and lets
      writes commit for real. It is slower, but the last test's rows are still in
      the database when the run ends, so you can inspect them with ordinary
      tools; run with ``-k`` and the last test is the one you are looking at.
      Tests still cannot see each other's data, and it needs a database of its
      own: two suites deleting from one database will fight. A normally pooled
      async engine is supported: Restly disposes its checked-in connections at
      the event-loop boundaries the test fixtures create.
    * ``"none"`` cleans nothing and leaves it to you. Reach for it when neither
      of the others fits: tests that drive a browser or a second process
      (nothing uncommitted is visible to those), or parallel workers sharing one
      database, whose cleaning would collide.

    ``RESTLY_DB_CLEANUP`` overrides this argument, and ``--restly-db-cleanup``
    overrides both, so a debugging run can switch mode without editing the suite.

    ``db_cleanup_exclude`` names tables cleaning must leave alone. Reference
    data seeded by a migration is the usual reason: cleaning would empty those
    tables before the first test and nothing would put the rows back. A table
    under a non-default schema is named with its qualifier, ``"tenant.item"``.
    Naming a table that does not exist raises, so a typo cannot silently drop
    the protection. Excluded tables are shared by every test, so writes to them
    do leak between tests.
    """
    global _setup

    if _setup is not None:
        raise RestlyConfigurationError(
            "fr.testing.configure_tests() was already called in this process. "
            "One setup serves the whole run: a second call would silently move "
            "every test onto its app, database and cleanup mode. In a monorepo, "
            "run each sub-project's suite as its own pytest invocation; a "
            "nested in-process run (pytester.runpytest) shares the setup too, "
            "so use runpytest_subprocess there."
        )

    if db_cleanup not in DB_CLEANUP_MODES:
        raise RestlyConfigurationError(
            f"fr.testing.configure_tests() got db_cleanup="
            f"{db_cleanup!r}; expected one of "
            f"{', '.join(repr(mode) for mode in DB_CLEANUP_MODES)}."
        )

    if create_all and alembic_upgrade:
        raise RestlyConfigurationError(
            "fr.testing.configure_tests() got both create_all= and "
            "alembic_upgrade=. They are two ways to build the same schema: pass "
            "create_all=True to create the tables from your models, or "
            "alembic_upgrade=True to run your migrations, not both."
        )

    if create_all and base is None:
        raise RestlyConfigurationError(
            "fr.testing.configure_tests(create_all=True) needs base=<your "
            "declarative base> to know which tables to create."
        )

    setup = _TestSetup(
        app=app,
        base=base,
        create_all=create_all,
        alembic_upgrade=alembic_upgrade,
        db_cleanup=db_cleanup,
        db_cleanup_exclude=tuple(db_cleanup_exclude),
        make_session=_fr_globals.make_session,
        async_make_session=_fr_globals.async_make_session,
        session_generator=_fr_globals.session_generator,
        sync_session_generator=_fr_globals.sync_session_generator,
        database_url=_fr_globals.database_url,
        async_database_url=_fr_globals.async_database_url,
    )
    _reject_split_databases(setup, db_cleanup)
    _setup = setup
    _fr_globals.database_configuration_locked = True


def _validate_database_sources(setup: _TestSetup, mode: str) -> None:
    """Reject source combinations the selected isolation mode cannot manage."""
    # The CLI or environment may have changed the effective mode since
    # configure_tests() recorded the setup. In particular, two private in-memory
    # SQLite engines are only unified by rollback mode's pinned sync connection.
    _reject_split_databases(setup, mode)
    if mode == ROLLBACK:
        if setup.sync_session_generator is not None and setup.make_session is None:
            raise RestlyConfigurationError(
                'fr.testing.configure_tests(db_cleanup="rollback") cannot '
                "isolate the configured sync_session_generator without a sync "
                "sessionmaker. Configure the application with database_url=, "
                "engine= or make_session= as well."
            )
        if setup.session_generator is not None and setup.async_make_session is None:
            raise RestlyConfigurationError(
                'fr.testing.configure_tests(db_cleanup="rollback") cannot '
                "isolate the configured session_generator without an async "
                "sessionmaker. Configure the application with "
                "async_database_url=, async_engine= or async_make_session= as "
                "well."
            )
    elif mode == DELETE and (
        setup.sync_session_generator is not None or setup.session_generator is not None
    ):
        raise RestlyConfigurationError(
            'fr.testing.configure_tests(db_cleanup="delete") cannot verify that '
            "a custom session generator uses the same database as the configured "
            "sessionmaker it would clean. Use the sessionmaker as the application "
            'session source, or choose db_cleanup="none".'
        )


def _resolve_db_cleanup(setup: _TestSetup, flag: str | None) -> str:
    """Return the mode in force: the override if a run set one, else the argument.

    The flag and the environment are read once, in ``pytest_configure``, and reach
    here already resolved. Reading the environment again per test would let the
    mode announced in the header and the mode enforced during the run disagree.
    """
    if not flag:
        return setup.db_cleanup
    if flag not in DB_CLEANUP_MODES:
        raise RestlyConfigurationError(
            f"{flag!r} is not a cleanup mode; expected one of "
            f"{', '.join(repr(mode) for mode in DB_CLEANUP_MODES)}."
        )
    return flag


def _tables_to_clean(setup: _TestSetup) -> list[Any]:
    """The tables delete mode empties, children first.

    Taken from the models the suite named, never from the database: reflecting a
    schema before every test costs a round trip per table on a real server, and
    picks up tables that are not the suite's to empty.
    """
    metadata = _session._resolve_metadata(setup.base)
    # table.key carries the schema qualifier (``tenant.item``), so two same-named
    # tables in different schemas stay individually excludable.
    known = {table.key for table in metadata.sorted_tables}
    unknown = sorted(set(setup.db_cleanup_exclude) - known)
    if unknown:
        # A typo would silently drop the protection and empty the very table the
        # caller was trying to keep, so refuse instead.
        raise RestlyConfigurationError(
            "fr.testing.configure_tests(db_cleanup_exclude=...) names "
            f"{', '.join(repr(name) for name in unknown)}, which "
            f"{'is' if len(unknown) == 1 else 'are'} not among the tables it would "
            f"empty: {', '.join(sorted(known))}."
        )

    spared = set(setup.db_cleanup_exclude)
    # sorted_tables puts parents first, so reversing respects foreign keys on the
    # databases that enforce them.
    return [
        table for table in reversed(metadata.sorted_tables) if table.key not in spared
    ]


def _delete_rows(connection: Any, tables: list[Any]) -> None:
    """Empty ``tables`` on ``connection``.

    A plain DELETE per table rather than the dialect's bulk statement. SQLAlchemy
    renders the table name, so the schema qualifier and any quoting are its
    problem rather than ours, and on the small tables a suite accumulates DELETE
    is usually the faster of the two anyway.
    """
    for table in tables:
        connection.execute(table.delete())


def _require_cleanable(setup: _TestSetup) -> list[Any] | None:
    """The tables to empty, or None when there is no database to empty them in."""
    _validate_database_sources(setup, DELETE)
    make_session, async_make_session = _source_factories()
    if make_session is None and async_make_session is None:
        # No database configured, which configure_tests() allows. Nothing to
        # empty.
        return None
    # Both legs, not just the one that happens to clean: a binds= factory on
    # either would keep its routed models' rows between tests, silently.
    for factory in (make_session, async_make_session):
        if factory is not None:
            _reject_binds_for_cleaning(factory)
    if setup.base is None:
        raise RestlyConfigurationError(
            'fr.testing.configure_tests(db_cleanup="delete") needs to know which '
            "tables to empty. Pass base=<your declarative base>, the same one your "
            "models are declared on."
        )
    return _tables_to_clean(setup)


def _reject_binds_for_cleaning(factory: Any) -> None:
    """Refuse to clean through a factory whose ``binds=`` routes models elsewhere.

    Cleaning opens one connection on the factory's single bind. Models routed to
    their own engines would keep their rows between tests (or a same-named empty
    table on the main bind would be deleted instead), and nothing would say so.
    """
    if factory.kw.get("binds"):
        raise RestlyConfigurationError(
            'fr.testing.configure_tests(db_cleanup="delete") empties the '
            "tables over the session factory's single bind, and this factory "
            "routes some models to their own engines with binds=. Their tables "
            "would silently keep their rows between tests. Configure the tests "
            "with a single-bind sessionmaker."
        )


def _clean_database_sync(setup: _TestSetup) -> bool:
    """Empty the tables over the sync leg. Returns False if there is no sync leg."""
    tables = _require_cleanable(setup)
    if tables is None:
        return True
    make_session, _ = _source_factories()
    if make_session is None:
        return False
    engine = _resolve_engine(make_session.kw["bind"])
    with engine.begin() as connection:
        _delete_rows(connection, tables)
    return True


async def _clean_database_async(setup: _TestSetup) -> None:
    """Empty the tables over the async leg, on the caller's event loop.

    Driven from an async fixture rather than a fresh ``asyncio.run`` per test: a
    pooled connection handed to a loop that has since closed is how asyncpg fails.
    """
    tables = _require_cleanable(setup)
    if tables is None:
        return
    _, async_make_session = _source_factories()
    if async_make_session is None:
        return
    engine = _resolve_engine(async_make_session.kw["bind"])
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync_conn: _delete_rows(sync_conn, tables))


def _is_memory_sqlite(url: Any) -> bool:
    """Whether ``url`` names an in-memory SQLite database, in any spelling
    SQLAlchemy accepts: no database at all, ``:memory:``, or a ``file:`` URI
    with ``mode=memory``."""
    if url.get_backend_name() != "sqlite":
        return False
    return (
        not url.database
        or url.database == ":memory:"
        or url.database == "file::memory:"
        or url.query.get("mode") == "memory"
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


def _reject_split_databases(setup: _TestSetup, mode: str) -> None:
    """Refuse sync and async legs that point at different databases.

    The suite treats the two legs as one database: rollback mode serves async
    requests over the sync leg's pinned connection, schema setup runs once, and
    deletion cleans through one leg on the assumption the other sees it. Split
    legs would break those guarantees silently. Two in-memory SQLite legs are
    therefore only an exception in rollback mode, where the pinned connection
    actually bridges them.
    """
    if setup.make_session is None or setup.async_make_session is None:
        return

    sync_engine = _resolve_engine(setup.make_session.kw.get("bind"))
    async_engine = _resolve_engine(setup.async_make_session.kw.get("bind"))
    sync_url = getattr(sync_engine, "url", None)
    async_url = getattr(async_engine, "url", None)
    if sync_url is None or async_url is None:
        return
    memory_sync = _is_memory_sqlite(sync_url)
    memory_async = _is_memory_sqlite(async_url)
    if memory_sync and memory_async:
        if mode == ROLLBACK:
            # Two in-memory legs record no location to compare, but rollback
            # makes them one database by serving async requests over the sync
            # leg's pinned connection.
            return
        raise RestlyConfigurationError(
            "The sync and async session factories configured through "
            "fr.configure() use two separate in-memory SQLite databases. "
            f"db_cleanup={mode!r} leaves those engines separate, so schema setup "
            "and application requests could see different databases. Use "
            'db_cleanup="rollback", or point both legs at the same located test '
            "database through its sync and async drivers."
        )

    if memory_sync or memory_async:
        # Exactly one in-memory leg needs no comparison: a private in-memory
        # database is provably not the other leg's file or server database.
        same = False
    elif sync_url.get_backend_name() != async_url.get_backend_name():
        same = False
    elif sync_url.database is None or async_url.database is None:
        return
    elif sync_url.get_backend_name() == "sqlite":
        # The same file spelled two ways ("./test.db" and "test.db") is one
        # database; resolve before comparing.
        same = Path(sync_url.database).resolve() == Path(async_url.database).resolve()
    else:
        same = (sync_url.host, sync_url.port, sync_url.database) == (
            async_url.host,
            async_url.port,
            async_url.database,
        )
    if same:
        return

    raise RestlyConfigurationError(
        "The sync and async session factories configured through fr.configure() "
        "are not the same database. The suite treats the two legs as "
        "one: rollback mode serves "
        "async requests over the sync leg's connection, and deletion cleans "
        "through one leg only. Point both at the same database, through its "
        "sync and async drivers. The backend, host, port and database must "
        "match; the sync and async driver names may differ."
    )


def _create_schema(setup: _TestSetup, root: Path | None = None) -> None:
    """Build the schema described by ``setup``, once, before the first test.

    ``root`` anchors relative Alembic paths; the plugin passes pytest's rootdir so
    the config is found no matter which directory pytest was invoked from.
    """
    if setup.create_all:
        # From the factories the suite recorded, not the live ones: an application
        # module imported during collection reconfigures Restly, and the schema
        # must land in the same database the tests will read.
        make_session, async_make_session = _source_factories()
        metadata = _session._resolve_metadata(setup.base)
        # Prefer the sync leg: it needs no event loop. With both legs pointing
        # at one database -- the split check enforces that for every database
        # with a location -- either leg creates the tables the other one sees.
        if make_session is not None:
            metadata.create_all(_resolve_engine(make_session.kw["bind"]))
        elif async_make_session is not None:

            async def _create() -> None:
                engine = _resolve_engine(async_make_session.kw["bind"])
                try:
                    async with engine.begin() as connection:
                        await connection.run_sync(metadata.create_all)
                finally:
                    # The loop asyncio.run() opened dies with this call, and a
                    # connection kept in the pool would resurface on a test's
                    # own loop, so the application's engine must not keep it.
                    # In-memory SQLite is the exception: its pool's one
                    # connection IS the database, so disposing it would discard
                    # the schema just built (and aiosqlite has no loop affinity
                    # to guard against).
                    if not _is_memory_sqlite(engine.url):
                        await engine.dispose()

            asyncio.run(_create())
        else:
            raise RestlyConfigurationError(
                "fr.testing.configure_tests(create_all=True) needs a configured "
                "application sessionmaker. Call "
                "fr.configure() with the test database before configure_tests()."
            )
    elif setup.alembic_upgrade:
        _run_alembic_upgrade(setup.alembic_upgrade, root, url=_configured_url(setup))


def _configured_url(setup: _TestSetup) -> str | None:
    """The URL of the database the tests were pointed at, however it was given.

    From the recorded setup, never the live globals: Alembic runs DDL, which
    survives everything, so a reconfiguration must not be able to point it at
    another database. Engine and sessionmaker forms record no URL, so read it
    back off the bind. Prefer the sync leg: Alembic
    drives a sync engine, and an async URL is one a stock ``env.py`` cannot open.
    """
    for recorded, factory in (
        (setup.database_url, setup.make_session),
        (setup.async_database_url, setup.async_make_session),
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
    alembic_upgrade: bool | str | Path, root: Path | None = None, *, url: str | None
) -> None:
    """Run ``alembic upgrade head`` against ``url``, the recorded test database."""
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
    if url is None:
        raise RestlyConfigurationError(
            "fr.testing.configure_tests(alembic_upgrade=...) could not work out "
            "which database to migrate. It would otherwise fall through to the URL "
            "in your alembic.ini, which is normally the development one, and "
            "migrate that instead. Configure the application for its test "
            "database before calling configure_tests(), using a URL or an "
            "engine Restly can read a URL from."
        )
    # Alembic stores this in a ConfigParser with interpolation on, where a bare %
    # is a syntax error. Percent-encoded passwords are ordinary, and the resulting
    # ValueError would echo the whole URL into the log.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
