"""Engine defaults for the engines Restly builds itself.

:func:`~fastapi_restly.db.configure` builds an engine only when it is given a
URL. These defaults apply to that engine and no other: an engine handed to
``configure(engine=...)`` is the caller's, and Restly does not touch it. Dropping
to the engine rung is therefore how you decline everything here.

The defaults exist because SQLAlchemy's own are tuned for a library talking to a
database, while Restly always runs under a web server: many threads or tasks,
long-lived pools, and connections that outlive the peer that opened them.

They stop at the connection. Restly configures the connections it opens and does
not modify the database those connections reach, which is what keeps a default
from leaving a permanent mark on data the caller owns.
"""

from typing import Any

from sqlalchemy import Engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import StaticPool

#: Discard a pooled connection older than this instead of reusing it. Proxies
#: (pgbouncer, RDS Proxy) and servers drop idle connections without telling the
#: client, and pre-ping only notices once the connection is already handed out.
_POOL_RECYCLE_SECONDS = 1800


def is_memory_sqlite(url: URL) -> bool:
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


def engine_options(url: str | URL) -> dict[str, Any]:
    """Keyword arguments for ``create_engine`` / ``create_async_engine``."""
    backend = make_url(url).get_backend_name()
    if backend == "sqlite":
        if not is_memory_sqlite(make_url(url)):
            return {}
        # An in-memory database lives inside its connection, so a pool that
        # opens a second one quietly serves a second, empty database: the sync
        # default (SingletonThreadPool) does exactly that per thread, and
        # FastAPI runs `def` endpoints on a thread pool. Keep every session on
        # the one connection that holds the schema, which then needs sqlite3's
        # same-thread check off to cross those threads.
        return {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
    if backend == "postgresql":
        return {"pool_pre_ping": True, "pool_recycle": _POOL_RECYCLE_SECONDS}
    return {}


#: Set on every SQLite connection Restly opens. foreign_keys is off by default
#: in SQLite, which silently drops every foreign key constraint the models
#: declare: no ondelete, and no IntegrityError for the 409 handler to translate.
#:
#: Only connection state belongs here. journal_mode=WAL was considered and
#: deliberately left out: it is stamped into the database file and outlives the
#: process, so a framework default would permanently change data the caller
#: owns. Its absence also fails loudly ("database is locked") rather than
#: silently, which is what the pragmas here are for.
_SQLITE_PRAGMAS = ("PRAGMA foreign_keys=ON",)


def apply_connect_hooks(engine: Engine | AsyncEngine) -> None:
    """Register per-connection setup for engines that need it."""
    sync_engine = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
    if sync_engine.url.get_backend_name() != "sqlite":
        return

    # Per connection, not once per engine: these are connection state, so every
    # new connection the pool opens starts without them.
    @event.listens_for(sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            for pragma in _SQLITE_PRAGMAS:
                cursor.execute(pragma)
        finally:
            cursor.close()
