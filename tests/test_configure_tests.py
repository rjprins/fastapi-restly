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
    ROLLBACK,
    TRUNCATE,
    _clean_database,
    _create_schema,
    _current_setup,
    _reset_setup,
    _resolve_db_cleanup,
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
            create_all_from=fr.DataclassBase,
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


def test_the_rejection_messages_run_urls_through_the_redaction():
    with _isolated_config():
        fr.configure(database_url="sqlite:///./dev.db")
        with pytest.raises(RestlyConfigurationError) as inherited:
            configure_tests(app=FastAPI())
        with pytest.raises(RestlyConfigurationError) as unnamed:
            configure_tests(async_database_url="sqlite+aiosqlite:///./test.db")

    # sqlite URLs carry no password, so the check is that they are rendered
    # through the same helper rather than interpolated raw.
    for excinfo in (inherited, unnamed):
        assert _safe_url("sqlite:///./dev.db") in str(excinfo.value)


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


def test_create_all_from_builds_the_schema(tmp_path: Path):
    database = tmp_path / "schema.db"
    with _isolated_config():

        class Sprocket(fr.IDBase):
            name: Mapped[str]

        configure_tests(
            database_url=f"sqlite:///{database}", create_all_from=fr.DataclassBase
        )
        _create_schema(_current_setup())  # type: ignore[arg-type]

    engine = create_engine(f"sqlite:///{database}")
    try:
        assert "sprocket" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_create_all_from_without_a_database_raises():
    with _isolated_config():
        setup = _TestSetup(
            app=None,
            create_all_from=fr.DataclassBase,
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
                create_all_from=None,
                alembic_upgrade=False,
                db_cleanup=ROLLBACK,
                db_cleanup_exclude=(),
            )
        )


def test_missing_alembic_config_names_the_path_it_looked_for(tmp_path: Path):
    setup = _TestSetup(
        app=None,
        create_all_from=None,
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
        create_all_from=None,
        alembic_upgrade=True,
        db_cleanup=ROLLBACK,
        db_cleanup_exclude=(),
    )

    # Resolution gets far enough to find the config and fail inside Alembic on the
    # missing script directory, rather than failing to locate alembic.ini at all.
    with pytest.raises(Exception) as excinfo:
        _create_schema(setup, root=tmp_path)
    assert not isinstance(excinfo.value, RestlyConfigurationError)


# ---------------------------------------------------------------------------
# Cleanup mode
# ---------------------------------------------------------------------------


def test_an_unknown_cleanup_mode_is_rejected():
    with pytest.raises(RestlyConfigurationError, match="expected one of"):
        configure_tests(database_url="sqlite://", db_cleanup="vacuum")


def _setup_with(mode: str) -> _TestSetup:
    return _TestSetup(
        app=None,
        create_all_from=None,
        alembic_upgrade=False,
        db_cleanup=mode,
        db_cleanup_exclude=(),
    )


def test_the_argument_decides_when_nothing_overrides_it(monkeypatch):
    monkeypatch.delenv(DB_CLEANUP_ENV_VAR, raising=False)
    assert _resolve_db_cleanup(_setup_with(TRUNCATE), None) == TRUNCATE


def test_the_environment_overrides_the_argument(monkeypatch):
    monkeypatch.setenv(DB_CLEANUP_ENV_VAR, TRUNCATE)
    assert _resolve_db_cleanup(_setup_with(ROLLBACK), None) == TRUNCATE


def test_the_flag_overrides_the_environment(monkeypatch):
    monkeypatch.setenv(DB_CLEANUP_ENV_VAR, TRUNCATE)
    assert _resolve_db_cleanup(_setup_with(TRUNCATE), ROLLBACK) == ROLLBACK


def test_an_unknown_mode_from_the_environment_is_rejected(monkeypatch):
    monkeypatch.setenv(DB_CLEANUP_ENV_VAR, "vacuum")
    with pytest.raises(RestlyConfigurationError, match=DB_CLEANUP_ENV_VAR):
        _resolve_db_cleanup(_setup_with(ROLLBACK), None)


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
            create_all_from=fr.DataclassBase,
            db_cleanup=TRUNCATE,
            db_cleanup_exclude=["region"],
        )
        setup = _current_setup()
        _create_schema(setup)  # type: ignore[arg-type]

        engine = fr.db.get_engine()
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO region (name) VALUES ('seeded')"))
            connection.execute(text("INSERT INTO widget (name) VALUES ('test data')"))

        _clean_database(setup)  # type: ignore[arg-type]

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
            create_all_from=fr.DataclassBase,
            db_cleanup=TRUNCATE,
            db_cleanup_exclude=["gizmoo"],
        )
        setup = _current_setup()
        _create_schema(setup)  # type: ignore[arg-type]

        with pytest.raises(RestlyConfigurationError) as excinfo:
            _clean_database(setup)  # type: ignore[arg-type]

    message = str(excinfo.value)
    assert "'gizmoo'" in message
    assert "gizmo" in message  # the known tables, so the fix is obvious


def test_alembic_bookkeeping_is_never_emptied(tmp_path: Path):
    """Emptying alembic_version would leave the database at no revision."""
    database = tmp_path / "cleanup.db"
    with _isolated_config():

        class Cog(fr.IDBase):
            name: Mapped[str]

        configure_tests(
            database_url=f"sqlite:///{database}",
            create_all_from=fr.DataclassBase,
            db_cleanup=TRUNCATE,
        )
        setup = _current_setup()
        _create_schema(setup)  # type: ignore[arg-type]

        engine = fr.db.get_engine()
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
            connection.execute(text("INSERT INTO alembic_version VALUES ('abc123')"))
            connection.execute(text("INSERT INTO cog (name) VALUES ('gone')"))

        _clean_database(setup)  # type: ignore[arg-type]

        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM cog")).scalar() == 0
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
    assert revision == "abc123"


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
    create_all_from=fr.DataclassBase,
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
    create_all_from=fr.DataclassBase,
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
    create_all_from=fr.DataclassBase,
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
    assert not (tmp_path / "test.db").exists()


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
        "fr.testing.configure_tests(app=app, create_all_from=fr.DataclassBase)\n",
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
        "    create_all_from=fr.DataclassBase,\n"
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
        "    create_all_from=fr.DataclassBase,\n"
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
    # Requests went to the configured test database, not the generator's.
    assert _rows(tmp_path / "dev.db") in (None, 0)


# ---------------------------------------------------------------------------
# Dialect and driver details
# ---------------------------------------------------------------------------


def test_postgres_truncate_keeps_the_schema_qualifier():
    """Hand-quoting drops table.schema, so a qualified table resolves through
    search_path to a different table, or errors."""
    from sqlalchemy import Column, Integer, MetaData, Table
    from sqlalchemy.dialects import postgresql

    metadata = MetaData()
    qualified = Table("audit", metadata, Column("id", Integer), schema="reporting")
    awkward = Table('we"ird', metadata, Column("id", Integer))

    preparer = postgresql.dialect().identifier_preparer
    assert preparer.format_table(qualified) == "reporting.audit"
    assert preparer.format_table(awkward) == '"we""ird"'


def test_alembic_accepts_a_percent_encoded_password(tmp_path: Path):
    """A percent-encoded password is ordinary; ConfigParser interpolation would
    reject it, and the ValueError would carry the whole URL into the log."""
    from alembic.config import Config

    ini = tmp_path / "alembic.ini"
    ini.write_text("[alembic]\nscript_location = alembic\n")
    url = "postgresql://app:p%40ssw0rd%23x@db.internal:5432/prod"

    config = Config(str(ini))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    assert config.get_main_option("sqlalchemy.url") == url


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
                    create_all_from=fr.DataclassBase,
                    db_cleanup=TRUNCATE,
                )
                setup = _current_setup()
                _create_schema(setup)  # type: ignore[arg-type]
                # Would raise AttributeError on RootTransaction without the unwrap.
                _clean_database(setup)  # type: ignore[arg-type]
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
            create_all_from=None,
            alembic_upgrade=False,
            db_cleanup=TRUNCATE,
            db_cleanup_exclude=(),
        )
        with pytest.raises(RestlyConfigurationError, match="session_generator"):
            _clean_database(setup)


def test_the_exclusion_error_does_not_claim_to_have_checked_the_database(tmp_path):
    """On the create_all_from path the names come from model metadata."""
    database = tmp_path / "wording.db"
    with _isolated_config():

        class Cam(fr.IDBase):
            name: Mapped[str]

        configure_tests(
            database_url=f"sqlite:///{database}",
            create_all_from=fr.DataclassBase,
            db_cleanup=TRUNCATE,
            db_cleanup_exclude=["nope"],
        )
        setup = _current_setup()
        _create_schema(setup)  # type: ignore[arg-type]
        with pytest.raises(RestlyConfigurationError) as excinfo:
            _clean_database(setup)  # type: ignore[arg-type]

    assert "not in the database" not in str(excinfo.value)
    assert "would" in str(excinfo.value)
