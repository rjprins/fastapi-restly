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
    NONE,
    ROLLBACK,
    TRUNCATE,
    _clean_database_sync,
    _create_schema,
    _current_setup,
    _reset_setup,
    _resolve_db_cleanup,
    _run_alembic_upgrade,
    _safe_url,
    _TestSetup,
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
        configure_tests(
            database_url="sqlite://",
            base=fr.DataclassBase,
            create_all=True,
            alembic_upgrade=True,
        )


def test_inheriting_a_configured_database_is_rejected():
    # The shape that bites: the application module configured a database on
    # import and the suite named none of its own.
    with _isolated_config():
        fr.configure(async_database_url="sqlite+aiosqlite:///./dev.db")
        with pytest.raises(RestlyConfigurationError) as excinfo:
            configure_tests(app=FastAPI())

    message = str(excinfo.value)
    # The message must name the database it refused, so the reader can tell at a
    # glance whether it was the development one.
    assert "sqlite+aiosqlite:///./dev.db" in message
    assert "no database argument" in message


def test_passing_the_configured_url_explicitly_is_accepted():
    """Opting in is spelled out by repeating the URL, not by a flag."""
    with _isolated_config():
        fr.configure(database_url="sqlite://")
        configure_tests(database_url="sqlite://")
        assert _current_setup() is not None


def test_a_suite_with_no_database_at_all_is_allowed():
    with _isolated_config():
        configure_tests(app=FastAPI())
        assert _current_setup() is not None


def test_naming_only_the_async_leg_rejects_an_inherited_sync_leg():
    """fr.configure() replaces only the leg it is given, so the unnamed one would
    survive into the tests and serve every sync route from the app's database."""
    with _isolated_config():
        fr.configure(database_url="sqlite:///./dev.db")
        with pytest.raises(RestlyConfigurationError) as excinfo:
            configure_tests(async_database_url="sqlite+aiosqlite:///./test.db")

    message = str(excinfo.value)
    assert "sync" in message
    assert "database_url=" in message


def test_naming_only_the_sync_leg_rejects_an_inherited_async_leg():
    with _isolated_config():
        fr.configure(async_database_url="sqlite+aiosqlite:///./dev.db")
        with pytest.raises(RestlyConfigurationError) as excinfo:
            configure_tests(database_url="sqlite:///./test.db")

    assert "async_database_url=" in str(excinfo.value)


def test_naming_both_legs_is_accepted():
    with _isolated_config():
        fr.configure(database_url="sqlite:///./dev.db")
        configure_tests(
            database_url="sqlite:///./test.db",
            async_database_url="sqlite+aiosqlite:///./test.db",
        )
        assert _current_setup() is not None


def test_a_leg_configured_only_here_is_not_treated_as_inherited():
    """Nothing was configured before, so naming one leg is complete on its own."""
    with _isolated_config():
        configure_tests(async_database_url="sqlite+aiosqlite:///./test.db")
        assert _current_setup() is not None


def test_urls_in_messages_hide_the_password():
    """These messages land in pytest output and CI logs."""
    rendered = _safe_url("postgresql://app:hunter2@db.internal:5432/dev")
    assert "hunter2" not in rendered
    assert "db.internal:5432/dev" in rendered


def test_safe_url_survives_something_that_is_not_a_url():
    assert _safe_url("not a url at all") == "the configured database"
    assert _safe_url(None) == "the configured database"


def test_the_rejection_messages_hide_the_password():
    """These land in pytest output and CI logs. A sqlite URL carries no password,
    so the recorded URL is set to one that does."""
    with _isolated_config() as context:
        fr.configure(engine=create_engine("sqlite://"))
        context.database_url = "postgresql://app:hunter2@db.internal:5432/dev"

        with pytest.raises(RestlyConfigurationError) as inherited:
            configure_tests(app=FastAPI())
        with pytest.raises(RestlyConfigurationError) as unnamed:
            configure_tests(async_database_url="sqlite+aiosqlite:///./test.db")

    for excinfo in (inherited, unnamed):
        message = str(excinfo.value)
        assert "hunter2" not in message
        assert "db.internal:5432/dev" in message


def test_any_single_database_argument_satisfies_the_guard():
    with _isolated_config():
        fr.configure(database_url="sqlite:///./dev.db")
        engine = create_engine("sqlite://")
        # make_session= is a database argument too, not just the URL forms.
        configure_tests(make_session=sessionmaker(bind=engine))
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

        configure_tests(
            database_url=f"sqlite:///{database}", base=fr.DataclassBase, create_all=True
        )
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
# Async engines and event loops
# ---------------------------------------------------------------------------


def test_an_async_url_gets_a_nullpool_engine():
    """Async test code hops event loops: the schema step's asyncio.run, pytest-
    asyncio's per-function loop, the test client's portal thread. A pooled
    connection created on one loop fails on the next for drivers like asyncpg,
    so the engine built from a test URL must hold nothing between checkouts."""
    from sqlalchemy.pool import NullPool

    with _isolated_config():
        configure_tests(async_database_url="sqlite+aiosqlite:///./test.db")
        setup = _current_setup()
        assert setup is not None
        assert isinstance(setup.async_make_session.kw["bind"].pool, NullPool)


def test_an_in_memory_async_url_keeps_its_default_pool():
    """NullPool would close in-memory SQLite's only connection and discard the
    database with it, and aiosqlite has no loop affinity to guard against."""
    from sqlalchemy.pool import NullPool

    with _isolated_config():
        configure_tests(async_database_url="sqlite+aiosqlite://")
        setup = _current_setup()
        assert setup is not None
        assert not isinstance(setup.async_make_session.kw["bind"].pool, NullPool)


def test_a_supplied_async_engine_keeps_its_pool():
    """configure_tests() imposes NullPool only on the engines it builds itself;
    a supplied engine's pooling is the caller's decision."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine("sqlite+aiosqlite:///./supplied.db")
    with _isolated_config():
        configure_tests(async_engine=engine)
        setup = _current_setup()
        assert setup is not None
        assert setup.async_make_session.kw["bind"] is engine
        assert not isinstance(engine.pool, NullPool)


def test_create_all_disposes_the_connection_it_pooled(tmp_path: Path):
    """asyncio.run()'s loop dies with the schema step; a connection left in a
    user-supplied engine's pool would resurface on a test's own loop, which is
    how asyncpg fails."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'made.db'}")
    with _isolated_config():

        class Doohickey(fr.IDBase):
            name: Mapped[str]

        configure_tests(async_engine=engine, base=fr.DataclassBase, create_all=True)
        _create_schema(_current_setup())  # type: ignore[arg-type]
        assert engine.pool.checkedin() == 0


# ---------------------------------------------------------------------------
# Cleanup mode
# ---------------------------------------------------------------------------


def test_an_unknown_cleanup_mode_is_rejected():
    with pytest.raises(RestlyConfigurationError, match="expected one of"):
        configure_tests(database_url="sqlite://", db_cleanup="vacuum")


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
    assert _resolve_db_cleanup(_setup_with(TRUNCATE), None) == TRUNCATE


def test_the_environment_overrides_the_argument(monkeypatch):
    """Read once, in pytest_configure: a per-call read would let the mode the
    header announced and the mode enforced during the run disagree."""
    monkeypatch.setenv(DB_CLEANUP_ENV_VAR, TRUNCATE)
    monkeypatch.setattr(_fixtures, "_db_cleanup_override", None)
    _fixtures.pytest_configure(_FakeConfig(None))  # type: ignore[arg-type]
    assert _fixtures._db_cleanup_override == TRUNCATE
    assert _resolve_db_cleanup(_setup_with(ROLLBACK), TRUNCATE) == TRUNCATE


def test_the_flag_overrides_the_environment(monkeypatch):
    monkeypatch.setenv(DB_CLEANUP_ENV_VAR, TRUNCATE)
    assert _resolve_db_cleanup(_setup_with(TRUNCATE), ROLLBACK) == ROLLBACK


def test_an_unknown_mode_from_the_environment_is_rejected(monkeypatch):
    monkeypatch.setenv(DB_CLEANUP_ENV_VAR, "vacuum")
    monkeypatch.setattr(_fixtures, "_db_cleanup_override", None)
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

        configure_tests(
            database_url=f"sqlite:///{database}",
            base=fr.DataclassBase,
            create_all=True,
            db_cleanup=TRUNCATE,
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

        configure_tests(
            database_url=f"sqlite:///{database}",
            base=fr.DataclassBase,
            create_all=True,
            db_cleanup=TRUNCATE,
            db_cleanup_exclude=["gizmoo"],
        )
        setup = _current_setup()
        _create_schema(setup)  # type: ignore[arg-type]

        with pytest.raises(RestlyConfigurationError) as excinfo:
            _clean_database_sync(setup)  # type: ignore[arg-type]

    message = str(excinfo.value)
    assert "'gizmoo'" in message
    assert "gizmo" in message  # the known tables, so the fix is obvious


def test_truncation_leaves_tables_the_base_does_not_declare(tmp_path: Path):
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

        configure_tests(
            database_url=f"sqlite:///{database}",
            base=fr.DataclassBase,
            create_all=True,
            db_cleanup=TRUNCATE,
        )
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


def test_truncation_needs_to_be_told_which_tables():
    """Without a base there is nothing to empty, and guessing is what reflection
    used to do."""
    with _isolated_config():
        fr.configure(database_url="sqlite://")
        configure_tests(database_url="sqlite://", db_cleanup=TRUNCATE)
        setup = _current_setup()
        with pytest.raises(RestlyConfigurationError, match="which tables to empty"):
            _clean_database_sync(setup)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# End to end, through a real pytest session
# ---------------------------------------------------------------------------

_APP_MODULE = """
from sqlalchemy.orm import Mapped, mapped_column
from fastapi import FastAPI
import fastapi_restly as fr

# A real app module configures its own (development) database on import.
fr.configure(async_database_url="sqlite+aiosqlite:///./dev.db")

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
import fastapi_restly as fr
from myapp import app

fr.testing.configure_tests(
    app=app,
    async_database_url="sqlite+aiosqlite:///./test.db",
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


_TRUNCATE_CONFTEST = """
import fastapi_restly as fr
from myapp import app

fr.testing.configure_tests(
    app=app,
    async_database_url="sqlite+aiosqlite:///./test.db",
    base=fr.DataclassBase,
            create_all=True,
    db_cleanup="truncate",
)
"""

_TRUNCATE_TESTS = """
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


def test_truncate_mode_isolates_tests_and_leaves_the_last_one_behind(tmp_path: Path):
    """The trade truncate exists for: slower and committed, but inspectable."""
    _write_project(tmp_path, _TRUNCATE_CONFTEST, _TRUNCATE_TESTS)
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


def test_the_flag_switches_a_rollback_suite_to_truncate(tmp_path: Path):
    """A debugging run changes mode without the suite being edited."""
    _write_project(tmp_path, _CONFTEST, _TRUNCATE_TESTS)  # conftest says rollback
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(tmp_path),
            # Not -q: pytest only prints the report header at normal verbosity.
            "-p",
            "no:cacheprovider",
            "--restly-db-cleanup=truncate",
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
    assert "db cleanup mode 'truncate'" in result.stdout

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    try:
        with engine.connect() as connection:
            assert connection.execute(text("select count(*) from note")).scalar() == 1
    finally:
        engine.dispose()


_NONE_CONFTEST = """
import fastapi_restly as fr
from myapp import app

fr.testing.configure_tests(
    app=app,
    async_database_url="sqlite+aiosqlite:///./test.db",
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


def test_inheriting_the_application_database_fails_the_run(tmp_path: Path):
    """The guard must fire in a real session, not just in a unit test."""
    _write_project(
        tmp_path,
        "import fastapi_restly as fr\nfrom myapp import app\n\n"
        "fr.testing.configure_tests(app=app, base=fr.DataclassBase)\n",
        "def test_never_runs():\n    assert True\n",
    )
    result = _run_pytest(tmp_path)

    assert result.returncode != 0
    assert "no database argument" in result.stdout + result.stderr
    # It refused before creating anything in the application's database.
    assert not (tmp_path / "dev.db").exists()


# ---------------------------------------------------------------------------
# Which database the requests actually reach
# ---------------------------------------------------------------------------

_SYNC_APP_MODULE = """
from collections.abc import Iterator

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Mapped, Session, sessionmaker

import fastapi_restly as fr

_dev_engine = create_engine("sqlite:///./dev.db")
_dev_sessions = sessionmaker(bind=_dev_engine, expire_on_commit=False)


def dev_session_generator() -> Iterator[Session]:
    with _dev_sessions() as session:
        yield session


CONFIGURE_KWARGS = {"database_url": "sqlite:///./dev.db"}
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
        "import fastapi_restly as fr\nfrom myapp import app\n\n"
        "fr.testing.configure_tests(\n"
        "    app=app,\n"
        '    database_url="sqlite:///./test.db",\n'
        '    async_database_url="sqlite+aiosqlite:///./test.db",\n'
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


def test_an_inherited_generator_does_not_route_requests_away(tmp_path: Path):
    """The session dependency reads a generator before the factory, so an
    application's generator would serve requests from its own database."""
    _write_project(
        tmp_path,
        "import fastapi_restly as fr\nfrom myapp import app\n\n"
        "fr.testing.configure_tests(\n"
        "    app=app,\n"
        '    database_url="sqlite:///./test.db",\n'
        "    base=fr.DataclassBase,\n"
        "    create_all=True,\n"
        '    db_cleanup="truncate",\n'
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

    assert result.returncode == 0, result.stdout + result.stderr
    # The generator's database has the schema, so a row landing there would be
    # counted rather than swallowed as a missing table.
    assert _rows(tmp_path / "dev.db") == 0
    assert _rows(tmp_path / "test.db") is not None


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

                configure_tests(
                    make_session=sessionmaker(bind=connection),
                    base=fr.DataclassBase,
                    create_all=True,
                    db_cleanup=TRUNCATE,
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


def test_truncate_cleans_nothing_when_no_database_is_configured(tmp_path: Path):
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
        env=_clean_env(**{DB_CLEANUP_ENV_VAR: "truncate"}),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_truncate_says_what_a_generator_only_suite_is_missing():
    """It needs a connection of its own; a generator does not provide one."""
    with _isolated_config():

        def generator():  # pragma: no cover - never called
            yield None

        fr.configure(sync_session_generator=generator)
        setup = _TestSetup(
            app=None,
            base=None,
            alembic_upgrade=False,
            db_cleanup=TRUNCATE,
            db_cleanup_exclude=(),
        )
        with pytest.raises(RestlyConfigurationError, match="session_generator"):
            _clean_database_sync(setup)


def test_the_exclusion_error_does_not_claim_to_have_checked_the_database(tmp_path):
    """The names come from the models base= declares, not from the database."""
    database = tmp_path / "wording.db"
    with _isolated_config():

        class Cam(fr.IDBase):
            name: Mapped[str]

        configure_tests(
            database_url=f"sqlite:///{database}",
            base=fr.DataclassBase,
            create_all=True,
            db_cleanup=TRUNCATE,
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
        "import pytest\n"
        "from fastapi import FastAPI\n"
        "import fastapi_restly as fr\n\n"
        "fr.testing.configure_tests(\n"
        '    async_database_url="sqlite+aiosqlite:///./test.db",\n'
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
    """configure_tests(engine=...) records no URL, and without one Alembic falls
    through to alembic.ini, which points at the development database."""
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
        configure_tests(
            engine=create_engine(f"sqlite:///{tmp_path / 'test.db'}"),
            alembic_upgrade=True,
        )
        original = alembic.command.upgrade
        alembic.command.upgrade = fake_upgrade
        try:
            _create_schema(_current_setup(), root=tmp_path)  # type: ignore[arg-type]
        finally:
            alembic.command.upgrade = original

    assert captured["url"] == f"sqlite:///{tmp_path / 'test.db'}"
    assert "DEV.db" not in captured["url"]


def test_alembic_migrates_the_recorded_database_not_the_live_one(tmp_path: Path):
    """Alembic runs DDL, which survives everything, so a reconfiguration after
    configure_tests() -- a lifespan, a module imported during collection -- must
    not be able to point the upgrade at its own database."""
    captured = {}

    def fake_upgrade(config, revision):
        captured["url"] = config.get_main_option("sqlalchemy.url")

    ini = tmp_path / "alembic.ini"
    ini.write_text("[alembic]\nscript_location = alembic\n")
    (tmp_path / "alembic").mkdir()

    import alembic.command

    with _isolated_config():
        configure_tests(
            database_url=f"sqlite:///{tmp_path / 'test.db'}", alembic_upgrade=True
        )
        fr.configure(database_url=f"sqlite:///{tmp_path / 'dev.db'}")
        original = alembic.command.upgrade
        alembic.command.upgrade = fake_upgrade
        try:
            _create_schema(_current_setup(), root=tmp_path)  # type: ignore[arg-type]
        finally:
            alembic.command.upgrade = original

    assert captured["url"] == f"sqlite:///{tmp_path / 'test.db'}"


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
        assert "truncate" in message
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


def test_a_lifespan_reconfiguring_restly_cannot_reach_the_tests(tmp_path: Path):
    """An application lifespan that configures Restly at startup now runs, because
    the client is entered. It used to replace the session factory the fixtures
    installed. The test's session source is consulted first and is not something
    fr.configure() writes, so the reconfiguration simply cannot reach the test.
    """
    _write_project(
        tmp_path,
        "import fastapi_restly as fr\nfrom myapp import app\n\n"
        "fr.testing.configure_tests(\n"
        "    app=app,\n"
        '    async_database_url="sqlite+aiosqlite:///./test.db",\n'
        "    base=fr.DataclassBase,\n"
        "    create_all=True,\n"
        ")\n",
        "def test_a(restly_client):\n"
        '    restly_client.post("/notes/", json={"text": "one"})\n'
        '    assert restly_client.get("/notes/").json()["total_count"] == 1\n\n\n'
        "def test_b_is_still_isolated(restly_client):\n"
        '    assert restly_client.get("/notes/").json()["total_count"] == 0\n',
        app_module=_RECONFIGURING_APP,
    )
    result = _run_pytest(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    # The application's database was never opened, and isolation held.
    assert not (tmp_path / "dev.db").exists()


def test_a_lifespan_reconfiguring_restly_cannot_move_truncate_mode(tmp_path: Path):
    """Truncate mode routes and cleans from the recorded factories too: requests
    keep committing to the test database and cleaning keeps emptying that same
    database, no matter what startup configures. Rollback mode was hardened
    against this first; the committing modes have to hold the same line."""
    _write_project(
        tmp_path,
        "import fastapi_restly as fr\nfrom myapp import app\n\n"
        "fr.testing.configure_tests(\n"
        "    app=app,\n"
        '    async_database_url="sqlite+aiosqlite:///./test.db",\n'
        "    base=fr.DataclassBase,\n"
        "    create_all=True,\n"
        '    db_cleanup="truncate",\n'
        ")\n",
        "def test_a(restly_client):\n"
        '    restly_client.post("/notes/", json={"text": "one"})\n'
        '    assert restly_client.get("/notes/").json()["total_count"] == 1\n\n\n'
        "def test_b_starts_clean(restly_client):\n"
        '    assert restly_client.get("/notes/").json()["total_count"] == 0\n',
        app_module=_RECONFIGURING_APP,
    )
    result = _run_pytest(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
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
    # FastAPI's recommended init point: nothing is configured at import time,
    # so configure_tests() has nothing to reject.
    fr.configure(database_url="sqlite:///./dev.db")
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/ping")
def ping():
    return {"ok": True}
"""


def test_truncate_never_cleans_a_database_configured_after_setup(tmp_path: Path):
    """The worst shape: the suite named no database, the lifespan configures the
    development one, and cleaning runs with a base to work from. Falling back to
    the live globals here used to DELETE the development database's rows."""
    _write_project(
        tmp_path,
        "import fastapi_restly as fr\nfrom myapp import app\n\n"
        "fr.testing.configure_tests(\n"
        "    app=app,\n"
        "    base=fr.DataclassBase,\n"
        '    db_cleanup="truncate",\n'
        ")\n",
        "def test_a(restly_client):\n"
        '    assert restly_client.get("/ping").json() == {"ok": True}\n\n\n'
        "def test_b(restly_client):\n"
        '    assert restly_client.get("/ping").json() == {"ok": True}\n',
        app_module=_LIFESPAN_ONLY_APP,
    )
    result = _run_pytest(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout

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


def test_a_source_arriving_after_setup_trips_instead_of_serving(tmp_path: Path):
    """The suite named only the sync database; a module imported during
    collection configures an async one. The unnamed leg must refuse loudly, not
    quietly serve requests from the late arrival's database."""
    _write_project(
        tmp_path,
        "import fastapi_restly as fr\nfrom myapp import app\n\n"
        "fr.testing.configure_tests(\n"
        "    app=app,\n"
        '    database_url="sqlite:///./test.db",\n'
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
    assert "named no async database" in result.stdout + result.stderr
    assert not (tmp_path / "dev.db").exists()


def test_a_nested_run_gives_the_outer_run_its_mode_back(monkeypatch):
    """Clearing the override would take the outer run's mode away, which is the
    inverse of the leak the hook exists to prevent."""
    monkeypatch.setattr(_fixtures, "_db_cleanup_override", None)
    monkeypatch.setattr(_fixtures, "_override_stack", [])
    monkeypatch.delenv(DB_CLEANUP_ENV_VAR, raising=False)

    _fixtures.pytest_configure(_FakeConfig(TRUNCATE))  # type: ignore[arg-type]
    assert _fixtures._db_cleanup_override == TRUNCATE

    # A nested in-process run, with its own mode.
    _fixtures.pytest_configure(_FakeConfig(NONE))  # type: ignore[arg-type]
    assert _fixtures._db_cleanup_override == NONE
    _fixtures.pytest_unconfigure(_FakeConfig(NONE))  # type: ignore[arg-type]

    assert _fixtures._db_cleanup_override == TRUNCATE

    _fixtures.pytest_unconfigure(_FakeConfig(TRUNCATE))  # type: ignore[arg-type]
    assert _fixtures._db_cleanup_override is None


def test_a_generator_only_application_is_rejected():
    """A session_generator is where an application's requests get their database.
    The fixtures cannot isolate one they know nothing about, so a suite that names
    no database of its own would read and write the application's."""
    with _isolated_config():

        def dev_sessions():  # pragma: no cover - never called
            yield None

        fr.configure(sync_session_generator=dev_sessions)
        with pytest.raises(RestlyConfigurationError) as excinfo:
            configure_tests(app=FastAPI())

    message = str(excinfo.value)
    assert "session_generator" in message
    assert "make_session=" in message


def test_a_generator_alongside_a_named_database_is_accepted():
    """With a sessionmaker configured for the tests the fixtures install their own
    source, which the session dependencies consult before any generator."""
    with _isolated_config():

        def dev_sessions():  # pragma: no cover - never called
            yield None

        fr.configure(
            database_url="sqlite:///./dev.db", sync_session_generator=dev_sessions
        )
        configure_tests(database_url="sqlite:///./test.db")
        assert _current_setup() is not None


def test_a_generator_on_the_unnamed_leg_is_rejected():
    """A generator is a session source like a sessionmaker: naming only the async
    database would leave every sync route on the generator's database."""
    with _isolated_config():

        def dev_sessions():  # pragma: no cover - never called
            yield None

        fr.configure(sync_session_generator=dev_sessions)
        with pytest.raises(RestlyConfigurationError) as excinfo:
            configure_tests(async_database_url="sqlite+aiosqlite:///./test.db")

    message = str(excinfo.value)
    assert "sync_session_generator" in message
    assert "database_url=" in message


def test_an_async_generator_on_the_unnamed_leg_is_rejected():
    with _isolated_config():

        async def dev_sessions():  # pragma: no cover - never called
            yield None

        fr.configure(session_generator=dev_sessions)
        with pytest.raises(RestlyConfigurationError) as excinfo:
            configure_tests(database_url="sqlite:///./test.db")

    message = str(excinfo.value)
    assert "a session_generator" in message
    assert "async_database_url=" in message


def test_a_second_call_in_one_process_is_rejected():
    """The setup is a process-wide singleton: a silent second call would move
    every already-collected test onto its app, database and cleanup mode."""
    with _isolated_config():
        configure_tests(database_url="sqlite://")
        with pytest.raises(RestlyConfigurationError, match="already called"):
            configure_tests(database_url="sqlite://")
