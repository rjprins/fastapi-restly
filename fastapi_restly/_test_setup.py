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
from typing import TYPE_CHECKING, Any, NoReturn

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
    #: The session factories and URLs in force when the suite was configured.
    #: Everything test-side builds from these rather than from the live globals,
    #: so an application reconfiguring Restly later cannot move the tests.
    make_session: Any = None
    async_make_session: Any = None
    database_url: str | None = None
    async_database_url: str | None = None


_setup: _TestSetup | None = None


def _source_factories() -> tuple[Any, Any]:
    """The factories the fixtures build from: the suite's recorded pair once
    ``configure_tests()`` ran, otherwise whatever is configured now.

    The recorded pair is authoritative even when a slot is empty: a suite that
    named no database on a leg gets no database there, not whatever an
    application module configured after the suite was set up.
    """
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


def configure_tests(
    *,
    app: FastAPI | None = None,
    database_url: str | None = None,
    async_database_url: str | None = None,
    engine: Engine | None = None,
    async_engine: AsyncEngine | None = None,
    make_session: sessionmaker[Any] | None = None,
    async_make_session: async_sessionmaker[Any] | None = None,
    base: type[DeclarativeBase] | MetaData | None = None,
    create_all: bool = False,
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
            base=Base,
            create_all=True,
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
       ``create_all`` or ``alembic_upgrade`` (see below).
    4. Every test gets a clean database, by the strategy ``db_cleanup`` names.

    ``base=`` names the models the suite works with. It is what delete mode empties
    between tests, and what ``create_all`` builds. Pass your declarative base, or
    its ``MetaData``.

    Who builds the schema is a separate choice, and the two are mutually
    exclusive:

    * ``create_all=True`` builds it straight from ``base``, as
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

    The databases named here are final. A database your application configures
    afterwards -- in its lifespan, or in a module imported during collection --
    is not used: requests, cleaning and the schema step all stay on the
    recorded one, and a leg the suite never named refuses to serve sessions
    rather than adopt what arrived later. One call configures the whole
    process; a second call raises.

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
      own: two suites deleting from one database will fight. An async database is
      best named by URL here; a supplied ``async_engine=`` should be built with
      ``poolclass=NullPool``, because test code hops event loops and a pooled
      async connection does not survive that on drivers like asyncpg.
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

    names_sync = any(
        argument is not None for argument in (database_url, engine, make_session)
    )
    names_async = any(
        argument is not None
        for argument in (async_database_url, async_engine, async_make_session)
    )
    # Validate against the application's configuration before replacing any of
    # it, so a rejected call leaves the globals exactly as it found them.
    if names_sync or names_async:
        _reject_unnamed_legs(names_sync=names_sync, names_async=names_async)
        if (
            async_database_url is not None
            and async_engine is None
            and async_make_session is None
        ):
            # Built here rather than left to fr.configure(): the test engine
            # gets NullPool, which fr.configure() rightly does not impose.
            async_engine = _test_async_engine(async_database_url)
        _session.configure(
            app=app,
            database_url=database_url,
            async_database_url=async_database_url,
            engine=engine,
            async_engine=async_engine,
            make_session=make_session,
            async_make_session=async_make_session,
        )
        # After configure(), when the engines exist to compare. Raising here
        # aborts the suite at conftest import, so nothing runs on the half-state.
        if names_sync and names_async:
            _reject_split_databases()
    else:
        _reject_inherited_database()

    # The guards above guarantee the live globals now hold only what this call
    # named, so recording them records the suite's own configuration.
    _setup = _TestSetup(
        app=app,
        base=base,
        create_all=create_all,
        alembic_upgrade=alembic_upgrade,
        db_cleanup=db_cleanup,
        db_cleanup_exclude=tuple(db_cleanup_exclude),
        make_session=_fr_globals.make_session,
        async_make_session=_fr_globals.async_make_session,
        database_url=_fr_globals.database_url if names_sync else None,
        async_database_url=_fr_globals.async_database_url if names_async else None,
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
    make_session, async_make_session = _source_factories()
    if make_session is None and async_make_session is None:
        # No database recorded, which configure_tests() allows. Nothing to
        # empty. (A generator-only application was rejected at configure time,
        # and a source arriving later is tripwired, not cleaned.)
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
        or url.query.get("mode") == "memory"
    )


def _test_async_engine(async_database_url: str) -> Any:
    """The engine behind an async test database named by URL.

    Built with ``NullPool``: every checkout is a fresh connection on the calling
    loop, and every checkin really closes it. Async test code hops loops -- the
    schema step's ``asyncio.run``, pytest-asyncio's per-function loop, the test
    client's portal thread -- and a pooled connection created on one loop fails
    on the next for drivers like asyncpg that bind their futures at connect
    time. In-memory SQLite keeps its default pool: closing its only connection
    would discard the database, and aiosqlite has no loop affinity anyway.
    """
    from sqlalchemy import make_url
    from sqlalchemy.ext.asyncio import create_async_engine

    if _is_memory_sqlite(make_url(async_database_url)):
        return create_async_engine(async_database_url)

    from sqlalchemy.pool import NullPool

    return create_async_engine(async_database_url, poolclass=NullPool)


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


def _safe_url(url: str | None) -> str:
    """Render ``url`` without its password, for messages that reach CI logs."""
    if not url:
        return "the configured database"
    try:
        from sqlalchemy import make_url

        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "the configured database"


def _reject_unnamed_legs(*, names_sync: bool, names_async: bool) -> None:
    """Refuse a leg the application configured and this call did not name.

    :func:`fastapi_restly.configure` replaces only the leg it is passed, so naming
    just one here leaves the other pointing wherever the application left it,
    usually the development database. Requests that resolve through the unnamed
    leg would then read and write there. A generator is a session source like any
    other: on a leg the suite named, the test override outranks it, but an
    unnamed leg has nothing to outrank it with.
    """
    if not names_sync and (
        _fr_globals.make_session is not None
        or _fr_globals.sync_session_generator is not None
    ):
        leg, other, argument = "sync", "async", "database_url="
        url = _fr_globals.database_url
        generator_name = "sync_session_generator"
        factory_missing = _fr_globals.make_session is None
    elif not names_async and (
        _fr_globals.async_make_session is not None
        or _fr_globals.session_generator is not None
    ):
        leg, other, argument = "async", "sync", "async_database_url="
        url = _fr_globals.async_database_url
        generator_name = "session_generator"
        factory_missing = _fr_globals.async_make_session is None
    else:
        return

    source = (
        f"a {generator_name}"
        if factory_missing
        else f"a {leg} database ({_safe_url(url)})"
    )
    raise RestlyConfigurationError(
        f"fr.testing.configure_tests() named the {other} database but not the "
        f"{leg} one, and your application already configured {source}. "
        f"fr.configure() replaces only the leg it is given, so that one would "
        f"survive into the tests: {leg} routes would read and write there. Pass "
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
    generator = _fr_globals.session_generator or _fr_globals.sync_session_generator
    if (
        _fr_globals.make_session is None
        and _fr_globals.async_make_session is None
        and generator is None
    ):
        return  # No database anywhere: a suite that never touches one is fine.

    if generator is not None and (
        _fr_globals.make_session is None and _fr_globals.async_make_session is None
    ):
        raise RestlyConfigurationError(
            "fr.testing.configure_tests() got no database argument, but your "
            "application configured a session_generator, which is where its "
            "requests get their database. The fixtures cannot isolate a generator "
            "they know nothing about, so the tests would read and write whatever "
            "it opens, normally the development database. Configure a sessionmaker "
            "for the tests: pass database_url=, engine= or make_session= (or their "
            "async forms)."
        )

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


class _UnnamedLegTripwire:
    """The test-session override for a leg the suite never named.

    The session dependencies consult the test override before every other
    source. Leaving the slot empty for an unnamed leg would let a source the
    application configures after ``configure_tests()`` ran -- in its lifespan,
    or in a module imported during collection -- serve a test's requests from
    its own database. This fills the slot and refuses on use instead.
    """

    def __init__(self, leg: str, arguments: str) -> None:
        self._leg = leg
        self._arguments = arguments

    def _refuse(self) -> NoReturn:
        raise RestlyConfigurationError(
            f"A test needed a {self._leg} database session, but "
            f"fr.testing.configure_tests() named no {self._leg} database. A "
            f"{self._leg} session source configured after the suite was set up "
            "-- by your application's lifespan, or a module imported during "
            "collection -- is deliberately not used: it is normally the "
            f"development database. Pass {self._arguments} to "
            "fr.testing.configure_tests()."
        )

    def __call__(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._refuse()

    @property
    def kw(self) -> NoReturn:
        self._refuse()


_SYNC_LEG_TRIPWIRE = _UnnamedLegTripwire(
    "sync", "database_url=, engine= or make_session="
)
_ASYNC_LEG_TRIPWIRE = _UnnamedLegTripwire(
    "async", "async_database_url=, async_engine= or async_make_session="
)


def _reject_split_databases() -> None:
    """Refuse sync and async legs that point at different databases.

    The suite treats the two legs as one database: rollback mode serves async
    requests over the sync leg's pinned connection, and deletion cleans
    through one leg on the assumption the other sees it. Split legs would break
    both, silently. In-memory SQLite records no comparable location and is not
    checked.
    """
    sync_engine = _resolve_engine(_fr_globals.make_session.kw.get("bind"))
    async_engine = _resolve_engine(_fr_globals.async_make_session.kw.get("bind"))
    sync_url = getattr(sync_engine, "url", None)
    async_url = getattr(async_engine, "url", None)
    if sync_url is None or async_url is None:
        return
    if _is_memory_sqlite(sync_url) or _is_memory_sqlite(async_url):
        return
    if sync_url.database is None or async_url.database is None:
        return

    if sync_url.get_backend_name() == "sqlite" and (
        async_url.get_backend_name() == "sqlite"
    ):
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
        "fr.testing.configure_tests() named a sync database "
        f"({_safe_url(sync_url.render_as_string())}) and an async database "
        f"({_safe_url(async_url.render_as_string())}) that are not the same "
        "database. The suite treats the two legs as one: rollback mode serves "
        "async requests over the sync leg's connection, and deletion cleans "
        "through one leg only. Point both at the same database, through its "
        "sync and async drivers. The comparison is textual, so spell the "
        "host, port and database identically on both legs."
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
                from sqlalchemy.pool import StaticPool

                engine = _resolve_engine(async_make_session.kw["bind"])
                try:
                    async with engine.begin() as connection:
                        await connection.run_sync(metadata.create_all)
                finally:
                    # The loop asyncio.run() opened dies with this call, and a
                    # connection kept in the pool would resurface on a test's
                    # own loop. Restly's URL-built engines hold nothing
                    # (NullPool), but a user-supplied engine may pool. A
                    # StaticPool engine is the exception either way: its one
                    # connection IS the database for in-memory SQLite, so
                    # disposing it would discard the schema just built.
                    if not isinstance(engine.sync_engine.pool, StaticPool):
                        await engine.dispose()

            asyncio.run(_create())
        else:
            raise RestlyConfigurationError(
                "fr.testing.configure_tests(create_all=True) needs a configured "
                "database. Pass database_url= or async_database_url= to "
                "fr.testing.configure_tests()."
            )
    elif setup.alembic_upgrade:
        _run_alembic_upgrade(setup.alembic_upgrade, root, url=_configured_url(setup))


def _configured_url(setup: _TestSetup) -> str | None:
    """The URL of the database the tests were pointed at, however it was given.

    From the recorded setup, never the live globals: Alembic runs DDL, which
    survives everything, so a reconfiguration must not be able to point it at
    another database. ``configure_tests(engine=...)`` and the sessionmaker forms
    record no URL, so read it back off the bind. Prefer the sync leg: Alembic
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
            "migrate that instead. Configure the tests with database_url= or "
            "async_database_url=, or with an engine Restly can read a URL from."
        )
    # Alembic stores this in a ConfigParser with interpolation on, where a bare %
    # is a syntax error. Percent-encoded passwords are ordinary, and the resulting
    # ValueError would echo the whole URL into the log.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
