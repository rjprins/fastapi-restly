"""``fr.testing.configure_tests()``: the one-call setup for a Restly test suite.

The unit tests below pin the validation and the schema step. The subprocess tests
at the bottom run a generated project through a real pytest session, which is the
only way to exercise the autouse fixtures: they are already resolved for the test
that would assert on them.
"""

from __future__ import annotations

import contextlib
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
    _create_schema,
    _current_setup,
    _reset_setup,
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
            app=None, create_all_from=fr.DataclassBase, alembic_upgrade=False
        )
        with pytest.raises(RestlyConfigurationError, match="needs a configured"):
            _create_schema(setup)


def test_neither_schema_option_does_nothing():
    with _isolated_config():
        # No database configured either: the no-op must not reach for one.
        _create_schema(
            _TestSetup(app=None, create_all_from=None, alembic_upgrade=False)
        )


def test_missing_alembic_config_names_the_path_it_looked_for(tmp_path: Path):
    setup = _TestSetup(app=None, create_all_from=None, alembic_upgrade=True)
    with pytest.raises(RestlyConfigurationError) as excinfo:
        _create_schema(setup, root=tmp_path)
    assert str(tmp_path / "alembic.ini") in str(excinfo.value)


def test_alembic_config_is_resolved_against_the_root_not_the_cwd(tmp_path: Path):
    """The path is anchored to the project, so the invocation directory cannot
    decide which config is found (the bug ``restly_project_root`` also had)."""
    (tmp_path / "alembic.ini").write_text("[alembic]\nscript_location = migrations\n")
    setup = _TestSetup(app=None, create_all_from=None, alembic_upgrade=True)

    # Resolution gets far enough to find the config and fail inside Alembic on the
    # missing script directory, rather than failing to locate alembic.ini at all.
    with pytest.raises(Exception) as excinfo:
        _create_schema(setup, root=tmp_path)
    assert not isinstance(excinfo.value, RestlyConfigurationError)


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


def _run_pytest(project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(project),
            "-q",
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
    )


def _write_project(project: Path, conftest: str, tests: str) -> None:
    (project / "pyproject.toml").write_text(textwrap.dedent(_PYPROJECT))
    (project / "myapp.py").write_text(textwrap.dedent(_APP_MODULE))
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
