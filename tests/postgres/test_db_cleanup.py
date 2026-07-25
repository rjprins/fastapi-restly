"""``db_cleanup="truncate"`` against a real PostgreSQL server.

Truncation is the only dialect-specific SQL Restly emits: on SQLite it takes an
entirely different branch (``DELETE FROM`` per table), so the single ``TRUNCATE``
statement, its identifier rendering, the identity restart and the refusal to
cascade are invisible to the rest of the suite.

The tables live on a ``MetaData`` of this module's own rather than on
``fr.DataclassBase``. The declarative registry is shared with the sibling test
module, and creating or dropping through it would take that module's tables with
it. Cleanup is called directly instead of through the autouse fixtures, so
nothing here depends on the configuration installed at import there.
"""

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, text

import fastapi_restly as fr
from fastapi_restly._test_setup import (
    TRUNCATE,
    _clean_database_sync,
    _create_schema,
    _current_setup,
    _reset_setup,
    configure_tests,
)
from fastapi_restly.db._globals import RestlyContext
from fastapi_restly.exc import RestlyConfigurationError

from .conftest import PG_URL

# conftest gates collection on a PostgreSQL URL, so this is always set here.
assert PG_URL is not None

SCHEMA = "cleanup_tenant"
metadata = MetaData()

widget = Table(
    "cleanup_widget",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String),
)
reference = Table(
    "cleanup_reference",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String),
)
churn = Table(
    "cleanup_churn",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String),
)
parent = Table(
    "cleanup_parent",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String),
)
# Schema-qualified on purpose: rendering the name by hand drops the schema, so the
# statement resolves through search_path to a different table, or errors.
tenant_item = Table(
    "item",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String),
    schema=SCHEMA,
)


@pytest.fixture
def pg_context():
    """A Restly context configured for truncate against this module's tables."""

    def configure(**kwargs):
        configure_tests(
            # str(URL) masks the password; the driver needs the real one.
            database_url=PG_URL.render_as_string(hide_password=False),
            base=metadata,
            create_all=True,
            db_cleanup=TRUNCATE,
            **kwargs,
        )
        setup = _current_setup()
        assert setup is not None
        with fr.db.get_engine().begin() as connection:
            connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        _create_schema(setup)
        return setup

    with RestlyContext():
        try:
            yield configure
        finally:
            # _setup is a process global while the database config is context-local,
            # so leaving the context would otherwise arm the autouse fixtures for
            # every later test with nothing configured behind them.
            _reset_setup()
            engine = fr.db.get_engine()
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE IF EXISTS cleanup_outsider"))
            metadata.drop_all(engine)
            with engine.begin() as connection:
                connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            engine.dispose()


def _count(table: str) -> int:
    with fr.db.get_engine().connect() as connection:
        return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar() or 0


def _insert(table: str, name: str) -> None:
    with fr.db.get_engine().begin() as connection:
        connection.execute(text(f"INSERT INTO {table} (name) VALUES ('{name}')"))


def test_truncate_empties_every_table_at_once(pg_context):
    setup = pg_context()
    _insert("cleanup_widget", "a")
    _insert("cleanup_churn", "b")
    assert _count("cleanup_widget") == 1
    assert _count("cleanup_churn") == 1

    _clean_database_sync(setup)

    assert _count("cleanup_widget") == 0
    assert _count("cleanup_churn") == 0


def test_cleaning_does_not_reset_sequences(pg_context):
    """Cleaning deletes rows; it does not touch sequences.

    Restarting identity needs TRUNCATE, whose bulk form has to render table names
    itself and takes a heavier lock. Rows are what a test needs cleaned, so ids
    simply keep counting, as they do in production.
    """
    setup = pg_context()
    _insert("cleanup_widget", "a")

    _clean_database_sync(setup)

    with fr.db.get_engine().begin() as connection:
        following = connection.execute(
            text("INSERT INTO cleanup_widget (name) VALUES ('b') RETURNING id")
        ).scalar()
    assert following > 1


def test_truncate_keeps_the_schema_qualifier(pg_context):
    setup = pg_context()
    _insert(f"{SCHEMA}.item", "x")
    assert _count(f"{SCHEMA}.item") == 1

    _clean_database_sync(setup)

    assert _count(f"{SCHEMA}.item") == 0


def test_excluded_tables_survive_truncation(pg_context):
    setup = pg_context(db_cleanup_exclude=["cleanup_reference"])
    _insert("cleanup_reference", "seed")
    _insert("cleanup_churn", "data")

    _clean_database_sync(setup)

    assert _count("cleanup_reference") == 1
    assert _count("cleanup_churn") == 0


def test_an_unknown_excluded_table_is_rejected(pg_context):
    setup = pg_context(db_cleanup_exclude=["cleanup_refrence"])
    with pytest.raises(RestlyConfigurationError, match="cleanup_refrence"):
        _clean_database_sync(setup)


def test_cleaning_refuses_to_orphan_a_row_it_does_not_know_about(pg_context):
    """A table outside base= may reference one inside it. Deleting the parent row
    raises, rather than removing something the suite never declared."""
    setup = pg_context()
    # A referencing table with a row, as a migration-created table might have.
    with fr.db.get_engine().begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE cleanup_outsider ("
                "id serial PRIMARY KEY, "
                "parent_id integer REFERENCES cleanup_parent(id))"
            )
        )
    _insert("cleanup_parent", "referenced")
    with fr.db.get_engine().begin() as connection:
        connection.execute(
            text(
                "INSERT INTO cleanup_outsider (parent_id) "
                "SELECT id FROM cleanup_parent LIMIT 1"
            )
        )

    with pytest.raises(Exception) as excinfo:
        _clean_database_sync(setup)
    assert "cleanup_outsider" in str(excinfo.value)
