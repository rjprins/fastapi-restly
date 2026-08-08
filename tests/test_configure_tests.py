"""``fr.testing.configure_tests()``: the one-call setup for a Restly test suite.

The unit tests below pin the validation and the schema step. The subprocess tests
at the bottom run a generated project through a real pytest session, which is the
only way to exercise the autouse fixtures: they are already resolved for the test
that would assert on them.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Mapped, sessionmaker

import fastapi_restly as fr
from fastapi_restly import _pytest_fixtures as _fixtures
from fastapi_restly._test_setup import (
    DB_CLEANUP_ENV_VAR,
    DELETE,
    NONE,
    ROLLBACK,
    _clean_database_sync,
    _create_schema,
    _current_setup,
    _reset_setup,
    _resolve_db_cleanup,
    _run_alembic_upgrade,
    _tables_to_clean,
    _TestSetup,
    _validate_database_sources,
    configure_tests,
)
from fastapi_restly.db._globals import RestlyContext
from fastapi_restly.exc import RestlyConfigurationError


@pytest.fixture(autouse=True)
def _forget_setup():
    """Drop the recorded setup so one test cannot arm the autouse fixtures for the next."""
    yield
    _reset_setup()


@contextlib.contextmanager
def _isolated_config():
    """Fresh Restly globals, with every engine configured inside disposed on exit.

    ``RestlyContext`` only swaps the globals; an engine built inside it would
    outlive the block and surface later as an unclosed-connection ResourceWarning,
    which this suite treats as an error.
    """
    with RestlyContext() as context:
        try:
            yield context
        finally:
            for factory in (context.make_session, context.async_make_session):
                bind = factory.kw.get("bind") if factory is not None else None
                if bind is None:
                    continue
                # AsyncEngine.dispose() is a coroutine; its sync engine is not.
                engine = getattr(bind, "sync_engine", bind)
                if hasattr(engine, "dispose"):
                    engine.dispose()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_the_two_schema_options_are_mutually_exclusive():
    with pytest.raises(RestlyConfigurationError, match="not both"):
        configure_tests(base=fr.DataclassBase, create_all=True, alembic_upgrade=True)


def test_configure_tests_records_the_database_the_application_already_configured():
    """The application owns database setup; the test helper only records it."""
    with _isolated_config() as context:
        fr.configure(database_url="sqlite://")
        configured_factory = context.make_session

        configure_tests(app=FastAPI())

        setup = _current_setup()
        assert setup is not None
        assert setup.make_session is configured_factory
        assert context.make_session is configured_factory


@pytest.mark.parametrize(
    "argument",
    [
        "database_url",
        "async_database_url",
        "engine",
        "async_engine",
        "make_session",
        "async_make_session",
    ],
)
def test_configure_tests_does_not_accept_database_configuration(argument: str):
    """Database construction belongs to fr.configure(), never to this helper."""
    with _isolated_config():
        with pytest.raises(TypeError, match=argument):
            configure_tests(**{argument: "unused"})  # type: ignore[arg-type]


def test_a_suite_with_no_database_at_all_is_allowed():
    with _isolated_config():
        configure_tests(app=FastAPI())
        assert _current_setup() is not None


def test_both_application_database_paths_are_recorded():
    with _isolated_config():
        fr.configure(
            database_url="sqlite:///./test.db",
            async_database_url="sqlite+aiosqlite:///./test.db",
        )
        configure_tests()
        assert _current_setup() is not None


# ---------------------------------------------------------------------------
# What the fixtures read back
# ---------------------------------------------------------------------------


def test_restly_app_returns_the_configured_app():
    app = FastAPI()
    with _isolated_config():
        configure_tests(app=app)
        assert _fixtures.restly_app.__wrapped__() is app


def test_restly_app_falls_back_to_a_bare_app_when_unconfigured():
    _reset_setup()
    produced = _fixtures.restly_app.__wrapped__()
    assert isinstance(produced, FastAPI)


# ---------------------------------------------------------------------------
# Schema step
# ---------------------------------------------------------------------------


def test_create_all_builds_the_schema(tmp_path: Path):
    database = tmp_path / "schema.db"
    with _isolated_config():

        class Sprocket(fr.IDBase):
            name: Mapped[str]

        fr.configure(database_url=f"sqlite:///{database}")
        configure_tests(base=fr.DataclassBase, create_all=True)
        _create_schema(_current_setup())  # type: ignore[arg-type]

    engine = create_engine(f"sqlite:///{database}")
    try:
        assert "sprocket" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_create_all_without_a_database_raises():
    with _isolated_config():
        setup = _TestSetup(
            app=None,
            base=fr.DataclassBase,
            create_all=True,
            alembic_upgrade=False,
            db_cleanup=ROLLBACK,
            db_cleanup_exclude=(),
        )
        with pytest.raises(RestlyConfigurationError, match="needs a configured"):
            _create_schema(setup)


def test_neither_schema_option_does_nothing():
    with _isolated_config():
        # No database configured either: the no-op must not reach for one.
        _create_schema(
            _TestSetup(
                app=None,
                base=None,
                alembic_upgrade=False,
                db_cleanup=ROLLBACK,
                db_cleanup_exclude=(),
            )
        )


def test_missing_alembic_config_names_the_path_it_looked_for(tmp_path: Path):
    setup = _TestSetup(
        app=None,
        base=None,
        alembic_upgrade=True,
        db_cleanup=ROLLBACK,
        db_cleanup_exclude=(),
    )
    with pytest.raises(RestlyConfigurationError) as excinfo:
        _create_schema(setup, root=tmp_path)
    assert str(tmp_path / "alembic.ini") in str(excinfo.value)


def test_alembic_config_is_resolved_against_the_root_not_the_cwd(tmp_path: Path):
    """The path is anchored to the project, so the invocation directory cannot
    decide which config is found (the bug ``restly_project_root`` also had)."""
    (tmp_path / "alembic.ini").write_text("[alembic]\nscript_location = migrations\n")
    setup = _TestSetup(
        app=None,
        base=None,
        alembic_upgrade=True,
        db_cleanup=ROLLBACK,
        db_cleanup_exclude=(),
        database_url="sqlite://",
    )

    # Resolution gets far enough to find the config and fail inside Alembic on the
    # missing script directory, rather than failing to locate alembic.ini at all.
    with pytest.raises(Exception) as excinfo:
        _create_schema(setup, root=tmp_path)
    assert not isinstance(excinfo.value, RestlyConfigurationError)


# ---------------------------------------------------------------------------
# Async schema setup and event loops
# ---------------------------------------------------------------------------


def test_an_in_memory_async_suite_works_end_to_end(tmp_path: Path):
    """Schema setup must not dispose an in-memory application's database."""
    _write_project(
        tmp_path,
        "import fastapi_restly as fr\nfrom myapp import app\n\n"
        'fr.configure(async_database_url="sqlite+aiosqlite://")\n'
        "fr.testing.configure_tests(\n"
        "    app=app,\n"
        "    base=fr.DataclassBase,\n"
        "    create_all=True,\n"
        ")\n",
        _TESTS,
    )
    result = _run_pytest(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 passed" in result.stdout


def test_create_all_disposes_the_connection_it_pooled(tmp_path: Path):
    """asyncio.run()'s loop dies with the schema step; a connection left in a
    user-supplied engine's pool would resurface on a test's own loop, which is
    how asyncpg fails."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'made.db'}")
    with _isolated_config():

        class Doohickey(fr.IDBase):
            name: Mapped[str]

        fr.configure(async_engine=engine)
        configure_tests(base=fr.DataclassBase, create_all=True)
        _create_schema(_current_setup())  # type: ignore[arg-type]
        assert engine.pool.checkedin() == 0


# ---------------------------------------------------------------------------
# Cleanup mode
# ---------------------------------------------------------------------------


def test_an_unknown_cleanup_mode_is_rejected():
    with pytest.raises(RestlyConfigurationError, match="expected one of"):
        configure_tests(db_cleanup="vacuum")


class _FakeConfig:
    """Just enough pytest Config for the hooks under test."""

    def __init__(self, flag: str | None) -> None:
        self._flag = flag

    def getoption(self, name: str, default: object = None) -> object:
        return self._flag


def _setup_with(mode: str) -> _TestSetup:
    return _TestSetup(
        app=None,
        base=None,
        alembic_upgrade=False,
        db_cleanup=mode,
        db_cleanup_exclude=(),
    )


def test_the_argument_decides_when_nothing_overrides_it(monkeypatch):
    monkeypatch.delenv(DB_CLEANUP_ENV_VAR, raising=False)
    assert _resolve_db_cleanup(_setup_with(DELETE), None) == DELETE


def test_the_environment_overrides_the_argument(monkeypatch):
    """Read once, in pytest_configure: a per-call read would let the mode the
    header announced and the mode enforced during the run disagree."""
    monkeypatch.setenv(DB_CLEANUP_ENV_VAR, DELETE)
    monkeypatch.setattr(_fixtures, "_db_cleanup_override", None)
    monkeypatch.setattr(_fixtures, "_override_stack", [])
    _fixtures.pytest_configure(_FakeConfig(None))  # type: ignore[arg-type]
    assert _fixtures._db_cleanup_override == DELETE
    assert _resolve_db_cleanup(_setup_with(ROLLBACK), DELETE) == DELETE


def test_the_flag_overrides_the_environment(monkeypatch):
    """The precedence the docs promise has to go through pytest_configure, where
    both sources are read; by the time _resolve_db_cleanup runs, the environment
    is already out of the picture, so asserting on the resolver alone proves
    nothing about it."""
    monkeypatch.setenv(DB_CLEANUP_ENV_VAR, DELETE)
    monkeypatch.setattr(_fixtures, "_db_cleanup_override", None)
    monkeypatch.setattr(_fixtures, "_override_stack", [])
    _fixtures.pytest_configure(_FakeConfig(ROLLBACK))  # type: ignore[arg-type]
    assert _fixtures._db_cleanup_override == ROLLBACK


def test_an_unknown_mode_from_the_environment_is_rejected(monkeypatch):
    monkeypatch.setenv(DB_CLEANUP_ENV_VAR, "vacuum")
    monkeypatch.setattr(_fixtures, "_db_cleanup_override", None)
    monkeypatch.setattr(_fixtures, "_override_stack", [])
    with pytest.raises(pytest.UsageError, match=DB_CLEANUP_ENV_VAR):
        _fixtures.pytest_configure(_FakeConfig(None))  # type: ignore[arg-type]


def test_excluded_tables_keep_their_rows(tmp_path: Path):
    """The reason exclusion exists: reference data a migration seeded would
    otherwise be emptied before the first test, with nothing to put it back."""
    database = tmp_path / "seeded.db"
    with _isolated_config():

        class Region(fr.IDBase):
            name: Mapped[str]

        class Widget(fr.IDBase):
            name: Mapped[str]

        fr.configure(database_url=f"sqlite:///{database}")
        configure_tests(
            base=fr.DataclassBase,
            create_all=True,
            db_cleanup=DELETE,
            db_cleanup_exclude=["region"],
        )
        setup = _current_setup()
        _create_schema(setup)  # type: ignore[arg-type]

        engine = fr.db.get_engine()
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO region (name) VALUES ('seeded')"))
            connection.execute(text("INSERT INTO widget (name) VALUES ('test data')"))

        _clean_database_sync(setup)  # type: ignore[arg-type]

        with engine.connect() as connection:
            regions = connection.execute(text("SELECT count(*) FROM region")).scalar()
            widgets = connection.execute(text("SELECT count(*) FROM widget")).scalar()
    assert regions == 1  # spared
    assert widgets == 0  # emptied


def test_excluding_a_table_that_does_not_exist_raises(tmp_path: Path):
    """A typo would silently drop the protection and empty the table it names."""
    database = tmp_path / "typo.db"
    with _isolated_config():

        class Gizmo(fr.IDBase):
            name: Mapped[str]

        fr.configure(database_url=f"sqlite:///{database}")
        configure_tests(
            base=fr.DataclassBase,
            create_all=True,
            db_cleanup=DELETE,
            db_cleanup_exclude=["gizmoo"],
        )
        setup = _current_setup()
        _create_schema(setup)  # type: ignore[arg-type]

        with pytest.raises(RestlyConfigurationError) as excinfo:
            _clean_database_sync(setup)  # type: ignore[arg-type]

    message = str(excinfo.value)
    assert "'gizmoo'" in message
    assert "gizmo" in message  # the known tables, so the fix is obvious


def test_cleaning_leaves_tables_the_base_does_not_declare(tmp_path: Path):
    """Alembic's bookkeeping used to need an explicit exception, because the table
    list was reflected from the database. Taken from the models instead, a table
    nobody declared is not in the list at all and cannot be emptied."""
    database = tmp_path / "migrated.db"
    engine = create_engine(f"sqlite:///{database}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
            connection.execute(text("INSERT INTO alembic_version VALUES ('abc123')"))
    finally:
        engine.dispose()

    with _isolated_config():

        class Cog(fr.IDBase):
            name: Mapped[str]

        fr.configure(database_url=f"sqlite:///{database}")
        configure_tests(base=fr.DataclassBase, create_all=True, db_cleanup=DELETE)
        setup = _current_setup()
        _create_schema(setup)  # type: ignore[arg-type]

        engine = fr.db.get_engine()
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO cog (name) VALUES ('gone')"))

        _clean_database_sync(setup)  # type: ignore[arg-type]

        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM cog")).scalar() == 0
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
    assert revision == "abc123"


def test_exclusion_names_schema_qualified_tables_by_their_key():
    """Two same-named tables in different schemas must be excludable separately;
    matching on the bare name would spare both or neither."""
    from sqlalchemy import Column, Integer, MetaData, Table

    metadata = MetaData()
    Table("item", metadata, Column("id", Integer, primary_key=True))
    Table("item", metadata, Column("id", Integer, primary_key=True), schema="tenant")
    setup = _TestSetup(
        base=metadata, db_cleanup=DELETE, db_cleanup_exclude=("tenant.item",)
    )
    assert [table.key for table in _tables_to_clean(setup)] == ["item"]


def test_split_sync_and_async_databases_are_rejected(tmp_path: Path):
    """Cleaning goes through one leg assuming the other sees it, and rollback
    mode serves async requests over the sync connection: two databases would
    break both, silently."""
    with _isolated_config():
        fr.configure(
            database_url=f"sqlite:///{tmp_path / 'sync.db'}",
            async_database_url=f"sqlite+aiosqlite:///{tmp_path / 'async.db'}",
        )
        with pytest.raises(RestlyConfigurationError, match="not the same database"):
            configure_tests()


def test_a_memory_leg_paired_with_a_located_leg_is_rejected(tmp_path: Path):
    """A private in-memory database is provably not the other leg's file or
    server database; only a pair of in-memory legs is accepted, because
    rollback mode makes those one database over the pinned connection."""
    with _isolated_config():
        fr.configure(
            database_url="sqlite:///:memory:",
            async_database_url=f"sqlite+aiosqlite:///{tmp_path / 'real.db'}",
        )
        with pytest.raises(RestlyConfigurationError, match="not the same database"):
            configure_tests()


def test_equivalent_sqlite_spellings_are_one_database(tmp_path: Path):
    """The same file spelled two ways is one database, not a split."""
    with _isolated_config():
        fr.configure(
            database_url=f"sqlite:///{tmp_path}/test.db",
            async_database_url=f"sqlite+aiosqlite:///{tmp_path}/./test.db",
        )
        configure_tests()
        assert _current_setup() is not None


def test_cleaning_needs_to_be_told_which_tables():
    """Without a base there is nothing to empty, and guessing is what reflection
    used to do."""
    with _isolated_config():
        fr.configure(database_url="sqlite://")
        configure_tests(db_cleanup=DELETE)
        setup = _current_setup()
        with pytest.raises(RestlyConfigurationError, match="which tables to empty"):
            _clean_database_sync(setup)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# End to end, through a real pytest session
# ---------------------------------------------------------------------------

_APP_MODULE = """
import os

from sqlalchemy.orm import Mapped, mapped_column
from fastapi import FastAPI
import fastapi_restly as fr

# The process environment selects the database before the application is imported.
fr.configure(
    async_database_url=os.environ.get(
        "DATABASE_URL", "sqlite+aiosqlite:///./dev.db"
    )
)

app = FastAPI()


class Note(fr.IDBase):
    text: Mapped[str] = mapped_column(unique=True)


class NoteSchema(fr.IDSchema):
    text: str


@fr.include_view(app)
class NoteView(fr.AsyncRestView):
    prefix = "/notes"
    model = Note
    schema = NoteSchema
"""

_CONFTEST = """
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"

import fastapi_restly as fr
from myapp import app

fr.testing.configure_tests(
    app=app,
    base=fr.DataclassBase,
    create_all=True,
)
"""

_TESTS = """
def test_write_a_note(restly_client):
    assert restly_client.post("/notes/", json={"text": "shared"}).json()["text"] == "shared"


def test_the_unique_text_is_free_again(restly_client):
    # Only passes if the previous test's write rolled back.
    restly_client.post("/notes/", json={"text": "shared"})


def test_nothing_is_left_behind(restly_client):
    assert restly_client.get("/notes/").json()["data"] == []


def test_requests_within_one_test_share_a_connection(restly_client):
    created = restly_client.post("/notes/", json={"text": "chained"}).json()
    listed = restly_client.get("/notes/").json()["data"]
    assert [row["id"] for row in listed] == [created["id"]]
"""

_PYPROJECT = """
[tool.pytest.ini_options]
pythonpath = ["."]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
"""


def _clean_env(**overrides: str) -> dict[str, str]:
    """The ambient environment without the variable this feature reads.

    The docs tell developers to export it, so a suite testing the default mode
    has to say so rather than inherit whatever the shell happens to hold.
    """
    environment = {**os.environ}
    environment.pop(DB_CLEANUP_ENV_VAR, None)
    environment.update(overrides)
    return environment


def _run_pytest(project: Path, quiet: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(project),
            *(["-q"] if quiet else []),
            "-p",
            "no:cacheprovider",
            "-c",
            str(project / "pyproject.toml"),
            "--rootdir",
            str(project),
        ],
        capture_output=True,
        text=True,
        cwd=project,
        env=_clean_env(),
    )


_DELETE_CONFTEST = """
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"

import fastapi_restly as fr
from myapp import app

fr.testing.configure_tests(
    app=app,
    base=fr.DataclassBase,
    create_all=True,
    db_cleanup="delete",
)
"""

_DELETE_TESTS = """
SHARED = "shared@example.com"


def test_a_writes(restly_client):
    restly_client.post("/notes/", json={"text": SHARED})
    assert restly_client.get("/notes/").json()["total_count"] == 1


def test_b_starts_clean(restly_client):
    # Only passes if the tables were emptied before this test ran.
    assert restly_client.get("/notes/").json()["total_count"] == 0
    # The unique text is free again, which a leftover row would prevent.
    restly_client.post("/notes/", json={"text": SHARED})
"""


def test_delete_mode_isolates_tests_and_leaves_the_last_one_behind(tmp_path: Path):
    """The trade delete mode exists for: slower and committed, but inspectable."""
    _write_project(tmp_path, _DELETE_CONFTEST, _DELETE_TESTS)
    result = _run_pytest(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("select text from note")).scalars().all()
    finally:
        engine.dispose()
    # Committed for real, and cleanup happens before a test rather than after, so
    # the final test's row is still here to inspect.
    assert rows == ["shared@example.com"]


def test_the_flag_switches_a_rollback_suite_to_delete(tmp_path: Path):
    """A debugging run changes mode without the suite being edited."""
    _write_project(tmp_path, _CONFTEST, _DELETE_TESTS)  # conftest says rollback
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(tmp_path),
            # Not -q: pytest only prints the report header at normal verbosity.
            "-p",
            "no:cacheprovider",
            "--restly-db-cleanup=delete",
            "-c",
            str(tmp_path / "pyproject.toml"),
            "--rootdir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_clean_env(),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    # The header announces the mode, so a stale flag cannot go unnoticed.
    assert "db cleanup mode 'delete'" in result.stdout

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    try:
        with engine.connect() as connection:
            assert connection.execute(text("select count(*) from note")).scalar() == 1
    finally:
        engine.dispose()


_NONE_CONFTEST = """
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"

import fastapi_restly as fr
from myapp import app

fr.testing.configure_tests(
    app=app,
    base=fr.DataclassBase,
    create_all=True,
    db_cleanup="none",
)
"""

_ACCUMULATING_TESTS = """
def test_a_writes(restly_client):
    restly_client.post("/notes/", json={"text": "first"})
    assert restly_client.get("/notes/").json()["total_count"] == 1


def test_b_still_sees_it(restly_client):
    # 'none' cleans nothing, so the previous test's row is still here.
    assert restly_client.get("/notes/").json()["total_count"] == 1
"""


def test_none_mode_cleans_nothing_and_says_so(tmp_path: Path):
    """The fallback for suites that can use neither of the other modes."""
    _write_project(tmp_path, _NONE_CONFTEST, _ACCUMULATING_TESTS)
    result = _run_pytest(tmp_path, quiet=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "nothing is cleaned between tests" in result.stdout

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    try:
        with engine.connect() as connection:
            assert connection.execute(text("select count(*) from note")).scalar() == 1
    finally:
        engine.dispose()


def test_rollback_mode_says_nothing_in_the_header(tmp_path: Path):
    _write_project(tmp_path, _CONFTEST, _TESTS)
    result = _run_pytest(tmp_path, quiet=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "db cleanup mode" not in result.stdout


def _write_project(
    project: Path, conftest: str, tests: str, app_module: str = ""
) -> None:
    (project / "pyproject.toml").write_text(textwrap.dedent(_PYPROJECT))
    (project / "myapp.py").write_text(textwrap.dedent(app_module or _APP_MODULE))
    (project / "conftest.py").write_text(textwrap.dedent(conftest))
    (project / "test_generated.py").write_text(textwrap.dedent(tests))


def test_a_configured_suite_is_isolated_end_to_end(tmp_path: Path):
    _write_project(tmp_path, _CONFTEST, _TESTS)
    result = _run_pytest(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 passed" in result.stdout


def test_the_configured_suite_leaves_the_application_database_alone(tmp_path: Path):
    """The point of the whole entry point: the app's own database is never touched."""
    _write_project(tmp_path, _CONFTEST, _TESTS)
    result = _run_pytest(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "test.db").exists()
    assert not (tmp_path / "dev.db").exists()


def test_the_test_database_keeps_no_rows(tmp_path: Path):
    _write_project(tmp_path, _CONFTEST, _TESTS)
    assert _run_pytest(tmp_path).returncode == 0

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    try:
        with engine.connect() as connection:
            assert connection.execute(text("select count(*) from note")).scalar() == 0
    finally:
        engine.dispose()


def test_a_suite_that_never_opts_in_is_untouched(tmp_path: Path):
    """Without ``configure_tests()`` the autouse fixtures stay inert: no schema is
    built, so the app's own database is still what a request would reach."""
    _write_project(
        tmp_path,
        "import fastapi_restly as fr\nfrom myapp import app\n\n"
        "@__import__('pytest').fixture\ndef restly_app():\n    return app\n",
        "def test_runs_without_a_database():\n    assert True\n",
    )
    result = _run_pytest(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    # dev.db is the app's own, and the only database this project names: the
    # schema step would have created it had the fixtures acted.
    assert not (tmp_path / "dev.db").exists()


def test_tests_still_run_when_no_database_is_configured_anywhere(tmp_path: Path):
    """A DB-less project must not have every test skipped by the isolation fixture."""
    (tmp_path / "pyproject.toml").write_text(textwrap.dedent(_PYPROJECT))
    (tmp_path / "conftest.py").write_text(
        textwrap.dedent("""
        from fastapi import FastAPI
        import fastapi_restly as fr

        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {"ok": True}

        fr.testing.configure_tests(app=app)
        """)
    )
    (tmp_path / "test_generated.py").write_text(
        textwrap.dedent("""
        def test_client_works(restly_client):
            assert restly_client.get("/ping").json() == {"ok": True}

        def test_plain_assertion():
            assert True
        """)
    )
    result = _run_pytest(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
    assert "skipped" not in result.stdout


def test_the_application_database_is_the_test_database(tmp_path: Path):
    """The suite uses the database its application configured."""
    _write_project(
        tmp_path,
        "import fastapi_restly as fr\nfrom myapp import app\n\n"
        "fr.testing.configure_tests(app=app, base=fr.DataclassBase)\n",
        "def test_never_runs():\n    assert True\n",
    )
    result = _run_pytest(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


# ---------------------------------------------------------------------------
# Which database the requests actually reach
# ---------------------------------------------------------------------------

_SYNC_APP_MODULE = """
import os

from collections.abc import Iterator

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Mapped, Session, sessionmaker

import fastapi_restly as fr

_database_url = os.environ.get("DATABASE_URL", "sqlite:///./dev.db")
_dev_engine = create_engine(_database_url)
_dev_sessions = sessionmaker(bind=_dev_engine, expire_on_commit=False)


def dev_session_generator() -> Iterator[Session]:
    with _dev_sessions() as session:
        yield session


CONFIGURE_KWARGS = {"make_session": _dev_sessions}
if __import__("os").environ.get("APP_USES_GENERATOR"):
    CONFIGURE_KWARGS["sync_session_generator"] = dev_session_generator

fr.configure(**CONFIGURE_KWARGS)

app = FastAPI()


class Note(fr.IDBase):
    text: Mapped[str]


class NoteSchema(fr.IDSchema):
    text: str


@fr.include_view(app)
class NoteView(fr.RestView):
    prefix = "/notes"
    model = Note
    schema = NoteSchema


if __import__("os").environ.get("APP_USES_GENERATOR"):
    # Only for the generator case: give that database the schema, so a row which
    # leaks there is counted rather than swallowed as a missing table.
    fr.DataclassBase.metadata.create_all(_dev_engine)
"""

_LEAK_TESTS = """
def test_a_writes(restly_client):
    restly_client.post("/notes/", json={"text": "one"})


def test_b_starts_clean(restly_client):
    assert restly_client.get("/notes/").json()["total_count"] == 0
"""


def _rows(database: Path) -> int | None:
    if not database.exists():
        return None
    engine = create_engine(f"sqlite:///{database}")
    try:
        with engine.connect() as connection:
            return connection.execute(text("select count(*) from note")).scalar()
    except Exception:
        return None
    finally:
        engine.dispose()


def test_a_hybrid_suite_isolates_its_sync_routes_too(tmp_path: Path):
    """Each session fixture swaps only its own factory, so a suite that configures
    both legs must activate both or its sync routes commit for real."""
    _write_project(
        tmp_path,
        "import os\n\n"
        'os.environ["DATABASE_URL"] = "sqlite:///./test.db"\n\n'
        "import fastapi_restly as fr\nfrom myapp import app\n\n"
        'fr.configure(async_database_url="sqlite+aiosqlite:///./test.db")\n'
        "fr.testing.configure_tests(\n"
        "    app=app,\n"
        "    base=fr.DataclassBase,\n"
        "    create_all=True,\n"
        ")\n",
        _LEAK_TESTS,
        app_module=_SYNC_APP_MODULE,
    )
    result = _run_pytest(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _rows(tmp_path / "test.db") == 0
    assert not (tmp_path / "dev.db").exists()


def test_delete_mode_rejects_a_custom_session_generator(tmp_path: Path):
    """The helper cannot prove which database an opaque generator will use."""
    _write_project(
        tmp_path,
        "import os\n\n"
        'os.environ["DATABASE_URL"] = "sqlite:///./test.db"\n\n'
        "import fastapi_restly as fr\nfrom myapp import app\n\n"
        "fr.testing.configure_tests(\n"
        "    app=app,\n"
        "    base=fr.DataclassBase,\n"
        "    create_all=True,\n"
        '    db_cleanup="delete",\n'
        ")\n",
        _LEAK_TESTS,
        app_module=_SYNC_APP_MODULE,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(tmp_path),
            "-q",
            "-p",
            "no:cacheprovider",
            "-c",
            str(tmp_path / "pyproject.toml"),
            "--rootdir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_clean_env(APP_USES_GENERATOR="1"),
    )

    assert result.returncode != 0
    assert "custom session generator" in result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Dialect and driver details
# ---------------------------------------------------------------------------


def test_alembic_accepts_a_percent_encoded_password(tmp_path: Path):
    """Alembic stores the URL in a ConfigParser with interpolation on, where a
    bare % is a syntax error, and the ValueError would carry the whole URL."""
    captured = {}

    def fake_upgrade(config, revision):
        captured["url"] = config.get_main_option("sqlalchemy.url")

    ini = tmp_path / "alembic.ini"
    ini.write_text("[alembic]\nscript_location = alembic\n")
    (tmp_path / "alembic").mkdir()
    url = "postgresql+psycopg://app:p%40ssw0rd%23x@db.internal:5432/prod"

    import alembic.command

    with _isolated_config():
        original = alembic.command.upgrade
        alembic.command.upgrade = fake_upgrade
        try:
            _run_alembic_upgrade(True, root=tmp_path, url=url)
        finally:
            alembic.command.upgrade = original

    assert captured["url"] == url


def test_error_messages_name_a_function_that_exists():
    """The rename left messages pointing at fr.testing.configure()."""
    import fastapi_restly.testing as testing
    from fastapi_restly import _pytest_fixtures, _test_setup

    assert not hasattr(testing, "configure")
    for module in (_test_setup, _pytest_fixtures):
        source = Path(module.__file__).read_text()  # type: ignore[arg-type]
        assert "fr.testing.configure(" not in source


def test_a_sessionmaker_bound_to_a_connection_is_unwrapped(tmp_path: Path):
    """fr.configure(make_session=...) allows a Connection as the bind; cleaning
    needs the engine behind it, not the connection's transaction."""
    database = tmp_path / "bound.db"
    engine = create_engine(f"sqlite:///{database}")
    try:
        with engine.connect() as connection:
            with _isolated_config():

                class Bolt(fr.IDBase):
                    name: Mapped[str]

                fr.configure(make_session=sessionmaker(bind=connection))
                configure_tests(
                    base=fr.DataclassBase, create_all=True, db_cleanup=DELETE
                )
                setup = _current_setup()
                _create_schema(setup)  # type: ignore[arg-type]
                # Would raise AttributeError on RootTransaction without the unwrap.
                _clean_database_sync(setup)  # type: ignore[arg-type]
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# The mode surface
# ---------------------------------------------------------------------------


def test_a_misspelled_environment_mode_is_a_usage_error(tmp_path: Path):
    """Raised from a later hook it would surface as a pytest INTERNALERROR."""
    _write_project(tmp_path, _CONFTEST, _TESTS)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(tmp_path),
            "-p",
            "no:cacheprovider",
            "-c",
            str(tmp_path / "pyproject.toml"),
            "--rootdir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_clean_env(**{DB_CLEANUP_ENV_VAR: "trunkate"}),
    )

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "INTERNALERROR" not in combined
    assert "trunkate" in combined
    assert "expected one of" in combined


def test_delete_mode_cleans_nothing_when_no_database_is_configured(tmp_path: Path):
    """configure_tests(app=...) with no database is supported, so switching mode
    for one run must not turn every test into an error."""
    (tmp_path / "pyproject.toml").write_text(textwrap.dedent(_PYPROJECT))
    (tmp_path / "conftest.py").write_text(
        textwrap.dedent("""
        from fastapi import FastAPI
        import fastapi_restly as fr

        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {"ok": True}

        fr.testing.configure_tests(app=app)
        """)
    )
    (tmp_path / "test_generated.py").write_text(
        "def test_client_works(restly_client):\n"
        '    assert restly_client.get("/ping").json() == {"ok": True}\n'
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(tmp_path),
            "-q",
            "-p",
            "no:cacheprovider",
            "-c",
            str(tmp_path / "pyproject.toml"),
            "--rootdir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_clean_env(**{DB_CLEANUP_ENV_VAR: "delete"}),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_delete_mode_rejects_async_per_mapper_binds_too():
    """Sync/async parity for the cleaning guard: an async-only suite's binds=
    factory would half-clean just the same."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    other = create_async_engine("sqlite+aiosqlite://")
    try:
        with _isolated_config():

            class RoutedAsync(fr.IDBase):
                name: Mapped[str]

            engine = create_async_engine("sqlite+aiosqlite://")
            factory = async_sessionmaker(
                bind=engine, binds={RoutedAsync: other}, expire_on_commit=False
            )
            fr.configure(async_make_session=factory)
            configure_tests(base=fr.DataclassBase, db_cleanup=DELETE)
            with pytest.raises(RestlyConfigurationError, match="binds="):
                _clean_database_sync(_current_setup())  # type: ignore[arg-type]
    finally:
        other.sync_engine.dispose()


def test_delete_mode_rejects_binds_on_the_leg_that_does_not_clean(tmp_path: Path):
    """Cleaning runs through the sync leg, but a binds= factory on the async leg
    would keep its routed models' rows just as silently; both recorded legs are
    checked, not just the one that happens to clean."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    database = tmp_path / "main.db"
    other = create_async_engine("sqlite+aiosqlite://")
    try:
        with _isolated_config():

            class RoutedHybrid(fr.IDBase):
                name: Mapped[str]

            engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
            fr.configure(
                database_url=f"sqlite:///{database}",
                async_make_session=async_sessionmaker(
                    bind=engine, binds={RoutedHybrid: other}, expire_on_commit=False
                ),
            )
            configure_tests(base=fr.DataclassBase, db_cleanup=DELETE)
            with pytest.raises(RestlyConfigurationError, match="binds="):
                _clean_database_sync(_current_setup())  # type: ignore[arg-type]
    finally:
        other.sync_engine.dispose()


def test_the_exclusion_error_does_not_claim_to_have_checked_the_database(tmp_path):
    """The names come from the models base= declares, not from the database."""
    database = tmp_path / "wording.db"
    with _isolated_config():

        class Cam(fr.IDBase):
            name: Mapped[str]

        fr.configure(database_url=f"sqlite:///{database}")
        configure_tests(
            base=fr.DataclassBase,
            create_all=True,
            db_cleanup=DELETE,
            db_cleanup_exclude=["nope"],
        )
        setup = _current_setup()
        _create_schema(setup)  # type: ignore[arg-type]
        with pytest.raises(RestlyConfigurationError) as excinfo:
            _clean_database_sync(setup)  # type: ignore[arg-type]

    assert "not in the database" not in str(excinfo.value)
    assert "would" in str(excinfo.value)


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------

_LIFESPAN_APP = """
from contextlib import asynccontextmanager

from fastapi import FastAPI

import fastapi_restly as fr

EVENTS = []


@asynccontextmanager
async def lifespan(app):
    EVENTS.append("startup")
    yield
    EVENTS.append("shutdown")


app = FastAPI(lifespan=lifespan)


@app.get("/ping")
def ping():
    return {"ok": True}
"""


def test_the_client_runs_the_application_lifespan(tmp_path: Path):
    """Starlette only runs startup and shutdown inside the client's context, so an
    unentered one silently skips whatever lifespan= sets up."""
    _write_project(
        tmp_path,
        "import fastapi_restly as fr\nfrom myapp import app\n\n"
        "fr.testing.configure_tests(app=app)\n",
        "from myapp import EVENTS\n\n\n"
        "def test_startup_ran(restly_client):\n"
        '    restly_client.get("/ping")\n'
        '    assert EVENTS == ["startup"]\n',
        app_module=_LIFESPAN_APP,
    )
    result = _run_pytest(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_view_registered_after_the_client_still_gets_the_409_handler(tmp_path: Path):
    """Entering the client builds Starlette's middleware stack, which caches the
    exception handlers. A suite that registers views inside its tests installs the
    409 handler after that, and it must not be left out of the stack.

    The app is deliberately not passed to configure_tests(): doing so installs the
    handler up front, which is the case that never had a problem.
    """
    _write_project(
        tmp_path,
        "import os\n"
        "import pytest\n"
        "from fastapi import FastAPI\n"
        "import fastapi_restly as fr\n\n"
        'os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"\n'
        "from myapp import Note, NoteSchema  # noqa: F401, E402\n\n"
        "fr.testing.configure_tests(\n"
        "    base=fr.DataclassBase,\n"
        "    create_all=True,\n"
        ")\n\n\n"
        "@pytest.fixture\n"
        "def restly_app():\n"
        "    return FastAPI()\n",
        "import fastapi_restly as fr\n"
        "from myapp import Note, NoteSchema\n\n\n"
        "def test_duplicate_is_a_conflict(restly_client):\n"
        "    @fr.include_view(restly_client.app)\n"
        "    class LateView(fr.AsyncRestView):\n"
        '        prefix = "/late"\n'
        "        model = Note\n"
        "        schema = NoteSchema\n\n"
        '    restly_client.post("/late/", json={"text": "dup"})\n'
        "    response = restly_client.post(\n"
        '        "/late/", json={"text": "dup"}, assert_status_code=409\n'
        "    )\n"
        "    assert response.status_code == 409\n",
    )
    result = _run_pytest(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Ways a test can still reach the wrong database
# ---------------------------------------------------------------------------


def test_alembic_derives_the_url_from_an_engine(tmp_path: Path):
    """An application may configure an engine instead of retaining its URL."""
    captured = {}

    def fake_upgrade(config, revision):
        captured["url"] = config.get_main_option("sqlalchemy.url")

    ini = tmp_path / "alembic.ini"
    ini.write_text(
        "[alembic]\nscript_location = alembic\nsqlalchemy.url = sqlite:///./DEV.db\n"
    )
    (tmp_path / "alembic").mkdir()

    import alembic.command

    with _isolated_config():
        fr.configure(engine=create_engine(f"sqlite:///{tmp_path / 'test.db'}"))
        configure_tests(alembic_upgrade=True)
        original = alembic.command.upgrade
        alembic.command.upgrade = fake_upgrade
        try:
            _create_schema(_current_setup(), root=tmp_path)  # type: ignore[arg-type]
        finally:
            alembic.command.upgrade = original

    assert captured["url"] == f"sqlite:///{tmp_path / 'test.db'}"
    assert "DEV.db" not in captured["url"]


def test_database_reconfiguration_after_setup_is_rejected(tmp_path: Path):
    """Schema setup, cleanup and requests must never observe different sources."""
    with _isolated_config():
        fr.configure(database_url=f"sqlite:///{tmp_path / 'test.db'}")
        configure_tests()
        with pytest.raises(RestlyConfigurationError, match="cannot be changed"):
            fr.configure(database_url=f"sqlite:///{tmp_path / 'dev.db'}")


def test_alembic_refuses_when_no_url_can_be_derived(tmp_path: Path):
    ini = tmp_path / "alembic.ini"
    ini.write_text(
        "[alembic]\nscript_location = alembic\nsqlalchemy.url = sqlite:///./DEV.db\n"
    )
    (tmp_path / "alembic").mkdir()
    with _isolated_config():
        with pytest.raises(RestlyConfigurationError, match="could not work out"):
            _run_alembic_upgrade(True, root=tmp_path, url=None)


def test_a_per_mapper_binds_factory_is_rejected():
    """binds= rides along into the isolated factory and routes those models to
    their own engine, outside the pinned connection, where writes really commit."""
    engine = create_engine("sqlite://")
    other = create_engine("sqlite://")
    try:
        with _isolated_config():

            class Routed(fr.IDBase):
                name: Mapped[str]

            factory = sessionmaker(bind=engine, binds={Routed: other})
            fr.configure(make_session=factory)
            with pytest.raises(RestlyConfigurationError) as excinfo:
                _fixtures._reject_per_mapper_binds(factory)
        message = str(excinfo.value)
        assert "Routed" in message
        assert "single-bind" in message
        # Delete mode cleans one bind, so it is no escape hatch for binds= and
        # must not be recommended as one.
        assert "db_cleanup" not in message
    finally:
        engine.dispose()
        other.dispose()


def test_delete_mode_rejects_a_per_mapper_binds_factory():
    """The escape hatch the rollback rejection used to recommend: cleaning opens
    one connection on the single bind, so routed models' tables would silently
    keep their rows between tests."""
    engine = create_engine("sqlite://")
    other = create_engine("sqlite://")
    try:
        with _isolated_config():

            class RoutedElsewhere(fr.IDBase):
                name: Mapped[str]

            factory = sessionmaker(bind=engine, binds={RoutedElsewhere: other})
            fr.configure(make_session=factory)
            configure_tests(base=fr.DataclassBase, db_cleanup=DELETE)
            with pytest.raises(RestlyConfigurationError, match="binds="):
                _clean_database_sync(_current_setup())  # type: ignore[arg-type]
    finally:
        engine.dispose()
        other.dispose()


def test_a_connection_bound_factory_works_in_rollback_mode(tmp_path: Path):
    """_shared_connection treated the bind as an Engine; a Connection raised
    "No such event 'connect' for target Connection"."""
    engine = create_engine(f"sqlite:///{tmp_path / 'bound.db'}")
    try:
        with engine.connect() as connection:
            with _isolated_config():
                fr.configure(make_session=sessionmaker(bind=connection))
                pinned = next(_fixtures._shared_connection.__wrapped__())
                assert pinned is not None
                assert pinned.dialect.name == "sqlite"
    finally:
        engine.dispose()


_RECONFIGURING_APP = """
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import Mapped

import fastapi_restly as fr


class Note(fr.IDBase):
    text: Mapped[str]


class NoteSchema(fr.IDSchema):
    text: str


@asynccontextmanager
async def lifespan(app):
    # The hazard: startup re-points Restly at the development database, which the
    # test client now runs because it enters the app's lifespan.
    fr.configure(async_database_url="sqlite+aiosqlite:///./dev.db")
    yield


app = FastAPI(lifespan=lifespan)


@fr.include_view(app)
class NoteView(fr.AsyncRestView):
    prefix = "/notes"
    model = Note
    schema = NoteSchema
"""


@pytest.mark.parametrize("db_cleanup", [None, "delete"])
def test_a_lifespan_cannot_reconfigure_restlys_database(
    tmp_path: Path, db_cleanup: str | None
):
    """The app must select its test database before configure_tests(), not during
    lifespan startup after schema setup and isolation have already begun."""
    mode_line = f'    db_cleanup="{db_cleanup}",\n' if db_cleanup else ""
    _write_project(
        tmp_path,
        "import fastapi_restly as fr\nfrom myapp import app\n\n"
        'fr.configure(async_database_url="sqlite+aiosqlite:///./test.db")\n'
        "fr.testing.configure_tests(\n"
        "    app=app,\n"
        "    base=fr.DataclassBase,\n"
        "    create_all=True,\n"
        f"{mode_line}"
        ")\n",
        "def test_a(restly_client):\n"
        '    restly_client.post("/notes/", json={"text": "one"})\n'
        '    assert restly_client.get("/notes/").json()["total_count"] == 1\n\n\n'
        "def test_b_starts_clean(restly_client):\n"
        '    assert restly_client.get("/notes/").json()["total_count"] == 0\n',
        app_module=_RECONFIGURING_APP,
    )
    result = _run_pytest(tmp_path)

    assert result.returncode != 0
    assert "cannot be changed" in result.stdout + result.stderr
    assert not (tmp_path / "dev.db").exists()


_LIFESPAN_ONLY_APP = """
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Mapped

import fastapi_restly as fr


class Note(fr.IDBase):
    text: Mapped[str]


# The development database already exists, with data worth keeping.
_dev = create_engine("sqlite:///./dev.db")
fr.DataclassBase.metadata.create_all(_dev)
with _dev.begin() as connection:
    connection.execute(text("INSERT INTO note (text) VALUES ('precious')"))
_dev.dispose()


@asynccontextmanager
async def lifespan(app):
    # Too late for managed test setup: configure_tests() has already recorded
    # that this application has no database.
    fr.configure(database_url="sqlite:///./dev.db")
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/ping")
def ping():
    return {"ok": True}
"""


def test_delete_mode_rejects_a_database_configured_after_setup(tmp_path: Path):
    """A late source is rejected before delete mode can clean the wrong database."""
    _write_project(
        tmp_path,
        "import fastapi_restly as fr\nfrom myapp import app\n\n"
        "fr.testing.configure_tests(\n"
        "    app=app,\n"
        "    base=fr.DataclassBase,\n"
        '    db_cleanup="delete",\n'
        ")\n",
        "def test_a(restly_client):\n"
        '    assert restly_client.get("/ping").json() == {"ok": True}\n\n\n'
        "def test_b(restly_client):\n"
        '    assert restly_client.get("/ping").json() == {"ok": True}\n',
        app_module=_LIFESPAN_ONLY_APP,
    )
    result = _run_pytest(tmp_path)

    assert result.returncode != 0
    assert "cannot be changed" in result.stdout + result.stderr

    engine = create_engine(f"sqlite:///{tmp_path / 'dev.db'}")
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("select text from note")).scalars().all()
    finally:
        engine.dispose()
    assert rows == ["precious"]


_UNCONFIGURED_ASYNC_APP = """
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

import fastapi_restly as fr


class Note(fr.IDBase):
    text: Mapped[str]


class NoteSchema(fr.IDSchema):
    text: str


app = FastAPI()


@fr.include_view(app)
class NoteView(fr.AsyncRestView):
    prefix = "/notes"
    model = Note
    schema = NoteSchema
"""


def test_a_source_arriving_during_collection_is_rejected(tmp_path: Path):
    """Every database source must exist before configure_tests() records it."""
    _write_project(
        tmp_path,
        "import fastapi_restly as fr\nfrom myapp import app\n\n"
        'fr.configure(database_url="sqlite:///./test.db")\n'
        "fr.testing.configure_tests(\n"
        "    app=app,\n"
        "    base=fr.DataclassBase,\n"
        "    create_all=True,\n"
        ")\n",
        "import fastapi_restly as fr\n\n"
        'fr.configure(async_database_url="sqlite+aiosqlite:///./dev.db")\n\n\n'
        "def test_an_async_route(restly_client):\n"
        '    restly_client.get("/notes/")\n',
        app_module=_UNCONFIGURED_ASYNC_APP,
    )
    result = _run_pytest(tmp_path)

    assert result.returncode != 0
    assert "cannot be changed" in result.stdout + result.stderr
    assert not (tmp_path / "dev.db").exists()


_UNCONFIGURED_SYNC_APP = """
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

import fastapi_restly as fr


class Note(fr.IDBase):
    text: Mapped[str]


class NoteSchema(fr.IDSchema):
    text: str


app = FastAPI()


@fr.include_view(app)
class NoteView(fr.RestView):
    prefix = "/notes"
    model = Note
    schema = NoteSchema
"""


def test_a_sync_source_arriving_during_collection_is_rejected(tmp_path: Path):
    """The consistency check applies equally to the sync configuration."""
    _write_project(
        tmp_path,
        "import fastapi_restly as fr\nfrom myapp import app\n\n"
        'fr.configure(async_database_url="sqlite+aiosqlite:///./test.db")\n'
        "fr.testing.configure_tests(\n"
        "    app=app,\n"
        "    base=fr.DataclassBase,\n"
        "    create_all=True,\n"
        ")\n",
        "import fastapi_restly as fr\n\n"
        'fr.configure(database_url="sqlite:///./dev.db")\n\n\n'
        "def test_a_sync_route(restly_client):\n"
        '    restly_client.get("/notes/")\n\n\n'
        "def test_get_engine():\n"
        "    fr.db.get_engine()\n",
        app_module=_UNCONFIGURED_SYNC_APP,
    )
    result = _run_pytest(tmp_path)

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "cannot be changed" in combined
    assert not (tmp_path / "dev.db").exists()


def test_rollback_refuses_client_requests_over_a_loop_bound_driver(tmp_path: Path):
    """asyncpg binds connections to their creating loop; rollback's pinned
    connection would cross into the client's portal loop and fail mid-test with
    "attached to a different loop". Refusing at the client names the two ways
    out, while a suite that only uses the session fixture stays on one loop and
    keeps working."""
    _write_project(
        tmp_path,
        "import fastapi_restly as fr\n"
        "from sqlalchemy.ext.asyncio import create_async_engine\n"
        "from sqlalchemy.pool import NullPool\n"
        "from myapp import app\n\n"
        "# NullPool as the docs advise for real async servers; the pinning\n"
        "# refusal is about the held per-test connection, which no pool policy\n"
        "# can save.\n"
        "fr.configure(\n"
        "    async_engine=create_async_engine(\n"
        '        "sqlite+aiosqlite:///./test.db", poolclass=NullPool\n'
        "    )\n"
        ")\n"
        "fr.testing.configure_tests(\n"
        "    app=app,\n"
        "    base=fr.DataclassBase,\n"
        "    create_all=True,\n"
        ")\n\n"
        "# Impersonate asyncpg's loop-bound behavior on the recorded engine.\n"
        "from fastapi_restly._test_setup import _current_setup\n"
        '_engine = _current_setup().async_make_session.kw["bind"]\n'
        '_engine.sync_engine.dialect.driver = "asyncpg"\n',
        "from myapp import Note\n\n\n"
        "def test_client_is_refused(restly_client):\n"
        '    restly_client.get("/notes/")\n\n\n'
        "def test_session_fixture_alone_still_works(restly_async_session):\n"
        '    restly_async_session.add(Note(text="one loop only"))\n'
        "    restly_async_session.flush()\n",
    )
    result = _run_pytest(tmp_path)

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "attached to a different loop" in combined
    assert "database_url=" in combined
    assert "1 passed" in result.stdout
    assert "1 error" in result.stdout


def test_collection_time_reconfiguration_fails_before_session_fixtures(tmp_path: Path):
    """The framework no longer installs a hidden run-wide routing layer."""
    _write_project(
        tmp_path,
        "import os\n"
        "import fastapi_restly as fr\n"
        "import pytest\n\n"
        'os.environ["DATABASE_URL"] = "sqlite:///./test.db"\n\n'
        "from myapp import app, Note\n\n"
        "fr.testing.configure_tests(\n"
        "    app=app,\n"
        "    base=fr.DataclassBase,\n"
        "    create_all=True,\n"
        '    db_cleanup="none",\n'
        ")\n\n\n"
        '@pytest.fixture(scope="session", autouse=True)\n'
        "def seed():\n"
        "    with fr.open_session() as session:\n"
        '        session.add(Note(text="seeded"))\n'
        "        session.commit()\n",
        "import myapp_tasks  # noqa: F401 -- reconfigures Restly at collection time\n\n\n"
        "def test_never_starts(restly_client):\n"
        '    assert restly_client.get("/notes/").status_code == 200\n',
        app_module=_SYNC_APP_MODULE,
    )
    (tmp_path / "myapp_tasks.py").write_text(
        "import fastapi_restly as fr\n\n"
        "# A background-tasks module wiring its own database access on import.\n"
        'fr.configure(database_url="sqlite:///./dev.db")\n'
    )
    result = _run_pytest(tmp_path)

    assert result.returncode != 0
    assert "cannot be changed" in result.stdout + result.stderr
    assert not (tmp_path / "dev.db").exists()


def test_a_nested_run_gives_the_outer_run_its_mode_back(monkeypatch):
    """Clearing the override would take the outer run's mode away, which is the
    inverse of the leak the hook exists to prevent."""
    monkeypatch.setattr(_fixtures, "_db_cleanup_override", None)
    monkeypatch.setattr(_fixtures, "_override_stack", [])
    monkeypatch.delenv(DB_CLEANUP_ENV_VAR, raising=False)

    _fixtures.pytest_configure(_FakeConfig(DELETE))  # type: ignore[arg-type]
    assert _fixtures._db_cleanup_override == DELETE

    # A nested in-process run, with its own mode.
    _fixtures.pytest_configure(_FakeConfig(NONE))  # type: ignore[arg-type]
    assert _fixtures._db_cleanup_override == NONE
    _fixtures.pytest_unconfigure(_FakeConfig(NONE))  # type: ignore[arg-type]

    assert _fixtures._db_cleanup_override == DELETE

    _fixtures.pytest_unconfigure(_FakeConfig(DELETE))  # type: ignore[arg-type]
    assert _fixtures._db_cleanup_override is None


def test_a_generator_only_application_is_rejected():
    """A session_generator is where an application's requests get their database.
    The fixtures cannot isolate one they know nothing about, so a suite that names
    no matching factory cannot use rollback isolation."""
    with _isolated_config():

        def dev_sessions():  # pragma: no cover - never called
            yield None

        fr.configure(sync_session_generator=dev_sessions)
        configure_tests(app=FastAPI())
        with pytest.raises(RestlyConfigurationError) as excinfo:
            _validate_database_sources(_current_setup(), ROLLBACK)  # type: ignore[arg-type]

    message = str(excinfo.value)
    assert "session_generator" in message
    assert "sessionmaker" in message


def test_rollback_accepts_a_generator_alongside_a_configured_factory():
    """Rollback's per-test factory override takes precedence over the generator."""
    with _isolated_config():

        def dev_sessions():  # pragma: no cover - never called
            yield None

        fr.configure(database_url="sqlite://", sync_session_generator=dev_sessions)
        configure_tests()
        assert _current_setup() is not None


def test_delete_rejects_a_generator_even_with_a_factory():
    with _isolated_config():

        def dev_sessions():  # pragma: no cover - never called
            yield None

        fr.configure(database_url="sqlite://", sync_session_generator=dev_sessions)
        configure_tests(db_cleanup=DELETE)
        with pytest.raises(RestlyConfigurationError) as excinfo:
            _validate_database_sources(_current_setup(), DELETE)  # type: ignore[arg-type]

    message = str(excinfo.value)
    assert "custom session generator" in message
    assert "none" in message


def test_none_accepts_a_generator_without_a_factory():
    with _isolated_config():

        async def dev_sessions():  # pragma: no cover - never called
            yield None

        fr.configure(session_generator=dev_sessions)
        configure_tests(db_cleanup=NONE)
        assert _current_setup() is not None


def test_a_pooled_asyncpg_engine_is_rejected_outside_both_leg_rollback():
    """asyncpg connections are bound to their creating loop, and a holding pool
    hands them across loops. Delete and none modes, and async-only rollback,
    all reuse pooled connections, so the refusal names the NullPool fix."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    with _isolated_config():
        engine = create_async_engine("sqlite+aiosqlite:///./test.db")
        engine.sync_engine.dialect.driver = "asyncpg"
        fr.configure(
            async_make_session=async_sessionmaker(bind=engine, expire_on_commit=False)
        )
        configure_tests(db_cleanup=DELETE)
        setup = _current_setup()
        for mode in (DELETE, NONE, ROLLBACK):
            with pytest.raises(RestlyConfigurationError, match="poolclass=NullPool"):
                _validate_database_sources(setup, mode)  # type: ignore[arg-type]


def test_an_unpooled_asyncpg_engine_is_accepted():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    with _isolated_config():
        engine = create_async_engine(
            "sqlite+aiosqlite:///./test.db", poolclass=NullPool
        )
        engine.sync_engine.dialect.driver = "asyncpg"
        fr.configure(
            async_make_session=async_sessionmaker(bind=engine, expire_on_commit=False)
        )
        configure_tests(db_cleanup=DELETE)
        _validate_database_sources(_current_setup(), DELETE)  # type: ignore[arg-type]


def test_a_pooled_asyncpg_engine_is_accepted_under_both_leg_rollback():
    """With a sync leg, rollback never draws from the async pool: the async
    engine only fronts the pinned sync connection."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    with _isolated_config():
        engine = create_async_engine("sqlite+aiosqlite:///./test.db")
        engine.sync_engine.dialect.driver = "asyncpg"
        fr.configure(
            database_url="sqlite:///./test.db",
            async_make_session=async_sessionmaker(bind=engine, expire_on_commit=False),
        )
        configure_tests()
        _validate_database_sources(_current_setup(), ROLLBACK)  # type: ignore[arg-type]


def test_a_second_call_in_one_process_is_rejected():
    """The setup is a process-wide singleton: a silent second call would move
    every already-collected test onto its app, database and cleanup mode."""
    with _isolated_config():
        fr.configure(database_url="sqlite://")
        configure_tests()
        with pytest.raises(RestlyConfigurationError, match="already called"):
            configure_tests()
