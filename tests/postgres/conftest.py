"""fznb.6 -- gating and fixture overrides for the PostgreSQL leg.

The rest of the suite runs on SQLite, where the dialect-divergent surface is
invisible: SQLite's ``LIKE`` is ASCII-case-insensitive, so ``contains`` and
``icontains`` cannot be told apart, and its default ``NULL`` ordering is the
opposite of PostgreSQL's. This subtree pins that surface on a real PostgreSQL
server, plus the psycopg cross-session connection sharing the fixtures document
but no SQLite run can reach.

Skipped entirely unless ``RESTLY_TEST_DATABASE_URL`` names a PostgreSQL
database. The CI ``postgres`` job sets it; local runs and every other CI leg
leave it unset and never touch PostgreSQL. Run locally with, e.g.::

    RESTLY_TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/restly_test \\
        uv run --with 'psycopg[binary]' pytest tests/postgres/

Run this subtree on its own (as CI and ``make test-postgres`` do). Running the
whole ``tests/`` tree with the variable set lets a preceding SQLite test's parent
fixtures reset the global config out from under these tests; it fails loudly, but
it is not a supported invocation.
"""

import os

import pytest
from sqlalchemy import make_url

_RAW_URL = os.environ.get("RESTLY_TEST_DATABASE_URL", "")

if _RAW_URL.startswith("postgresql"):
    # One DBAPI (psycopg3) drives both the sync and async engines, so the
    # cross-session sharing test can wrap the pinned sync connection in an async
    # one. Normalising here lets the env var carry any postgresql URL.
    PG_URL = make_url(_RAW_URL).set(drivername="postgresql+psycopg")
else:
    PG_URL = None
    # conftest.py is imported even when the subtree is skipped (it must be, to
    # set this), so keep it import-light: psycopg is only pulled in when the
    # collected-only test module is imported (its module-level create_engine
    # imports the driver), which this glob prevents when no URL is set.
    collect_ignore_glob = ["*"]


@pytest.fixture(autouse=True)
def setup_database_connection():
    """Override the parent's per-test SQLite reset.

    This subtree runs on the PostgreSQL engines configured once for the whole
    session, so leave ``_fr_globals`` untouched between tests.
    """
    yield


@pytest.fixture(autouse=True)
def reset_metadata():
    """Override the parent's per-test registry wipe.

    The PostgreSQL models are declared once at module import and reused across
    the subtree, so the shared metadata must survive between tests.
    """
    yield
