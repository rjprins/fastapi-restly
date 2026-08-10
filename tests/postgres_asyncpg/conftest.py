"""Gate the async-only PostgreSQL fixture tests behind an explicit server URL.

Run this subtree on its own.  It configures Restly once at module collection,
which is deliberately incompatible with the root suite's per-test SQLite reset.
CI supplies ``RESTLY_TEST_DATABASE_URL`` through a PostgreSQL service; locally,
``scripts/with_postgres.sh`` supplies a throwaway server when none is configured.
"""

import os

import pytest
from sqlalchemy import make_url

_RAW_URL = os.environ.get("RESTLY_TEST_DATABASE_URL", "")
if _RAW_URL.startswith("postgresql"):
    if os.environ.get("RESTLY_DB_CLEANUP") is not None:
        raise pytest.UsageError(
            "tests/postgres_asyncpg uses RESTLY_ASYNCPG_CLEANUP for its explicit "
            "mode legs; unset RESTLY_DB_CLEANUP before running this subtree."
        )
    ASYNCPG_URL = make_url(_RAW_URL).set(drivername="postgresql+asyncpg")
else:
    ASYNCPG_URL = None
    collect_ignore_glob = ["*"]


@pytest.fixture(autouse=True)
def setup_database_connection():
    """Override the root suite's per-test SQLite configuration."""
    yield


@pytest.fixture(autouse=True)
def reset_metadata():
    """Keep the module-level PostgreSQL model registered for the whole run."""
    yield
