"""Tests for ``restly new``: the project generator and its command line.

The mechanism tests build synthetic overlay trees under ``tmp_path`` and hand
them to ``_plan`` / ``generate`` as the template root, so compositing, renaming
and dotfile restoration are pinned independently of what the bundled templates
happen to contain. The shipped templates are exercised end to end by
``scripts/scaffold_matrix.sh``; one loose smoke test here checks that a real
generated tree looks like a project.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import make_url

from fastapi_restly._scaffold import _cli
from fastapi_restly._scaffold._generate import (
    DOTFILE_NAMES,
    PLACEHOLDER,
    Options,
    _plan,
    build_conftest,
    build_env_example,
    build_files,
    build_pyproject,
    build_readme,
    generate,
    rename,
)
from fastapi_restly.db._engine_defaults import is_memory_sqlite

ALL_COMBINATIONS = [
    Options(name="shop", is_async=is_async, postgres=postgres, alembic=alembic)
    for is_async in (True, False)
    for postgres in (True, False)
    for alembic in (True, False)
]
SQLITE_COMBINATIONS = [options for options in ALL_COMBINATIONS if not options.postgres]


def _combination_id(options: Options) -> str:
    return "-".join(
        [
            "async" if options.is_async else "sync",
            "postgres" if options.postgres else "sqlite",
            "alembic" if options.alembic else "createall",
        ]
    )


def _tree(root: Path, files: dict[str, str]) -> Path:
    """Write a synthetic template tree and return its root."""
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, "utf-8")
    return root


def _template_root(root: Path, files: dict[str, str], options: Options) -> Path:
    """A synthetic template tree for ``options``.

    Every overlay the options name has to exist, so the ones the test does not
    fill are created empty.
    """
    _tree(root, files)
    for overlay in options.overlays:
        (root / overlay).mkdir(parents=True, exist_ok=True)
    return root


def _content(plan: dict[Path, Any], relative: str) -> str:
    """The content the plan resolved for one destination path."""
    return str(plan[Path(relative)].read_text("utf-8"))


def _written(paths: list[Path], destination: Path) -> set[str]:
    return {path.relative_to(destination).as_posix() for path in paths}


def _configure_tests_call(conftest: str) -> str:
    """The one ``configure_tests`` line, without the comments around it."""
    (call,) = [
        line
        for line in conftest.splitlines()
        if line.startswith("fr.testing.configure_tests")
    ]
    return call


# ---------------------------------------------------------------------------
# Overlay compositing
# ---------------------------------------------------------------------------


def test_later_overlay_wins_for_a_shared_path(tmp_path):
    root = _tree(tmp_path, {"one/shared.txt": "from one", "two/shared.txt": "from two"})
    assert _content(_plan(("one", "two"), root), "shared.txt") == "from two"
    # The rule is positional, not alphabetical.
    assert _content(_plan(("two", "one"), root), "shared.txt") == "from one"


def test_files_unique_to_each_overlay_all_appear(tmp_path):
    root = _tree(
        tmp_path,
        {
            "one/shared.txt": "from one",
            "one/only_one.txt": "one",
            "one/pkg/deep.txt": "one deep",
            "two/shared.txt": "from two",
            "two/only_two.txt": "two",
            "two/pkg/nested/deeper.txt": "two deeper",
        },
    )
    plan = _plan(("one", "two"), root)

    assert set(plan) == {
        Path("shared.txt"),
        Path("only_one.txt"),
        Path("only_two.txt"),
        Path("pkg/deep.txt"),
        Path("pkg/nested/deeper.txt"),
    }
    assert _content(plan, "only_one.txt") == "one"
    assert _content(plan, "only_two.txt") == "two"
    assert _content(plan, "pkg/deep.txt") == "one deep"


def test_a_deep_file_is_overridden_by_path_not_by_name(tmp_path):
    root = _tree(
        tmp_path,
        {
            "one/pkg/main.py": "one",
            "two/pkg/main.py": "two",
            "two/main.py": "top level",
        },
    )
    plan = _plan(("one", "two"), root)

    assert _content(plan, "pkg/main.py") == "two"
    assert _content(plan, "main.py") == "top level"


def test_a_single_overlay_is_composited_on_its_own(tmp_path):
    root = _tree(tmp_path, {"base/a.txt": "a", "base/pkg/b.txt": "b"})

    assert set(_plan(("base",), root)) == {Path("a.txt"), Path("pkg/b.txt")}


# ---------------------------------------------------------------------------
# Options.overlays
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "options, expected",
    [
        (Options(name="shop"), ("base", "app_async", "db_postgres", "alembic_async")),
        (Options(name="shop", alembic=False), ("base", "app_async", "db_postgres")),
        (Options(name="shop", postgres=False), ("base", "app_async", "alembic_async")),
        (Options(name="shop", postgres=False, alembic=False), ("base", "app_async")),
        (
            Options(name="shop", is_async=False),
            ("base", "app_sync", "db_postgres", "alembic_sync"),
        ),
        (
            Options(name="shop", is_async=False, alembic=False),
            ("base", "app_sync", "db_postgres"),
        ),
        (
            Options(name="shop", is_async=False, postgres=False),
            ("base", "app_sync", "alembic_sync"),
        ),
        (
            Options(name="shop", is_async=False, postgres=False, alembic=False),
            ("base", "app_sync"),
        ),
    ],
    ids=[
        "async-postgres-alembic",
        "async-postgres-createall",
        "async-sqlite-alembic",
        "async-sqlite-createall",
        "sync-postgres-alembic",
        "sync-postgres-createall",
        "sync-sqlite-alembic",
        "sync-sqlite-createall",
    ],
)
def test_overlays_for_every_combination(options, expected):
    assert options.overlays == expected


@pytest.mark.parametrize("options", ALL_COMBINATIONS, ids=_combination_id)
def test_base_is_always_first_and_the_app_overlay_second(options):
    """Everything after ``base`` overrides it, so its position is the contract."""
    assert options.overlays[0] == "base"
    assert options.overlays[1].startswith("app_")


# ---------------------------------------------------------------------------
# Renaming the placeholder package
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("import myapp", "import shop"),
        ("from myapp.main import create_app", "from shop.main import create_app"),
        ("myapp", "shop"),
        ("myapp.asgi:app", "shop.asgi:app"),
        ("# myapp, myapp; myapp!", "# shop, shop; shop!"),
        ("path/to/myapp/main.py", "path/to/shop/main.py"),
        ('name = "myapp"', 'name = "shop"'),
    ],
)
def test_rename_replaces_whole_words(text, expected):
    assert rename(text, "shop") == expected


@pytest.mark.parametrize(
    "text",
    [
        "myapplication",
        "my_myapp_thing",
        "myapp_extra",
        "extra_myapp",
        "myappy",
        "notmyapp",
        "MYAPP",
        "Myapp",
    ],
)
def test_rename_leaves_longer_identifiers_alone(text):
    """The word boundary is the whole guarantee: a name that merely contains the
    placeholder keeps its own spelling."""
    assert rename(text, "shop") == text


def test_rename_touches_only_the_whole_word_in_mixed_text():
    text = "myapp and myapplication and my_myapp_thing and myapp"
    assert rename(text, "shop") == "shop and myapplication and my_myapp_thing and shop"


def test_generate_renames_content_and_path_components(tmp_path):
    options = Options(name="shop")
    root = _template_root(
        tmp_path / "templates",
        {
            f"base/{PLACEHOLDER}/main.py": "from myapp.settings import Settings\n",
            f"base/{PLACEHOLDER}/tasks/views.py": "# myapp.tasks\n",
            "base/tests/test_tasks.py": "import myapp\n",
        },
        options,
    )
    destination = tmp_path / "out"
    generate(options, destination, root)

    assert (destination / "shop" / "main.py").read_text() == (
        "from shop.settings import Settings\n"
    )
    assert (destination / "shop" / "tasks" / "views.py").read_text() == "# shop.tasks\n"
    assert (destination / "tests" / "test_tasks.py").read_text() == "import shop\n"
    assert not (destination / PLACEHOLDER).exists()


def test_generate_leaves_path_components_that_merely_contain_the_placeholder(tmp_path):
    """A directory is renamed only when its whole name is the placeholder."""
    options = Options(name="shop")
    root = _template_root(
        tmp_path / "templates",
        {
            "base/myapplication/keep.py": "keep\n",
            "base/my_myapp_thing/keep.py": "keep\n",
            f"base/{PLACEHOLDER}/rename.py": "rename\n",
        },
        options,
    )
    destination = tmp_path / "out"
    generate(options, destination, root)

    assert (destination / "myapplication" / "keep.py").exists()
    assert (destination / "my_myapp_thing" / "keep.py").exists()
    assert (destination / "shop" / "rename.py").exists()


def test_generate_renames_a_placeholder_directory_at_any_depth(tmp_path):
    options = Options(name="shop")
    root = _template_root(
        tmp_path / "templates", {f"base/src/{PLACEHOLDER}/x.py": "x\n"}, options
    )
    destination = tmp_path / "out"
    generate(options, destination, root)

    assert (destination / "src" / "shop" / "x.py").exists()


# ---------------------------------------------------------------------------
# Dot-less template names
# ---------------------------------------------------------------------------


def test_dotless_names_are_restored(tmp_path):
    """Stored without their dot so git and setuptools leave them alone in the
    template tree; the generated project needs the real names."""
    options = Options(name="shop")
    root = _template_root(
        tmp_path / "templates",
        {
            "base/gitignore": ".venv\n",
            "base/alembic/versions/gitkeep": "",
            "base/pyrightconfig.json": "{}\n",
        },
        options,
    )
    destination = tmp_path / "out"
    written = generate(options, destination, root)

    assert (destination / ".gitignore").read_text() == ".venv\n"
    assert (destination / "alembic" / "versions" / ".gitkeep").exists()
    assert not (destination / "gitignore").exists()
    # An ordinary name is left exactly as it is.
    assert (destination / "pyrightconfig.json").exists()
    assert ".gitignore" in _written(written, destination)


def test_dotfile_names_cover_gitignore_and_gitkeep():
    assert DOTFILE_NAMES == {"gitignore": ".gitignore", "gitkeep": ".gitkeep"}


# ---------------------------------------------------------------------------
# Options.test_database_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "options, expected",
    [
        (
            Options(name="shop"),
            "postgresql+asyncpg://postgres:postgres@localhost:5433/shop_test",
        ),
        (
            Options(name="shop", alembic=False),
            "postgresql+asyncpg://postgres:postgres@localhost:5433/shop_test",
        ),
        (
            Options(name="shop", is_async=False),
            "postgresql+psycopg://postgres:postgres@localhost:5433/shop_test",
        ),
        (
            Options(name="shop", is_async=False, alembic=False),
            "postgresql+psycopg://postgres:postgres@localhost:5433/shop_test",
        ),
        (Options(name="shop", postgres=False), "sqlite+aiosqlite:///./test.db"),
        (
            Options(name="shop", postgres=False, alembic=False),
            "sqlite+aiosqlite:///./test.db",
        ),
        (Options(name="shop", is_async=False, postgres=False), "sqlite:///./test.db"),
        (
            Options(name="shop", is_async=False, postgres=False, alembic=False),
            "sqlite:///./test.db",
        ),
    ],
    ids=[
        "async-postgres-alembic",
        "async-postgres-createall",
        "sync-postgres-alembic",
        "sync-postgres-createall",
        "async-sqlite-alembic",
        "async-sqlite-createall",
        "sync-sqlite-alembic",
        "sync-sqlite-createall",
    ],
)
def test_test_database_url(options, expected):
    assert options.test_database_url == expected


@pytest.mark.parametrize("options", SQLITE_COMBINATIONS, ids=_combination_id)
def test_sqlite_test_database_is_a_file_never_in_memory(options):
    """An in-memory SQLite database lives inside its connection, and the suite
    opens a second one: Alembic migrates through its own connection, and the
    async lifespan disposes the engine at the end of every test. Either one
    leaves the tests looking at an empty database."""
    assert not is_memory_sqlite(make_url(options.test_database_url))
    assert options.test_database_url.endswith("test.db")


@pytest.mark.parametrize("alembic", [True, False], ids=["alembic", "createall"])
@pytest.mark.parametrize("is_async", [True, False], ids=["async", "sync"])
def test_postgres_test_database_is_separate_from_development(is_async, alembic):
    """A test run must never reach the development database, so the two URLs
    differ in both port and database name."""
    options = Options(name="shop", is_async=is_async, postgres=True, alembic=alembic)

    assert options.test_database_url != options.database_url
    assert options.test_database_url.endswith(":5433/shop_test")
    assert options.database_url.endswith(":5432/shop")


# ---------------------------------------------------------------------------
# Options.driver and the development URL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "options, expected",
    [
        (Options(name="shop"), "asyncpg"),
        (Options(name="shop", is_async=False), "psycopg[binary]"),
        (Options(name="shop", postgres=False), "aiosqlite"),
        (Options(name="shop", is_async=False, postgres=False), None),
    ],
    ids=["async-postgres", "sync-postgres", "async-sqlite", "sync-sqlite"],
)
def test_driver(options, expected):
    """Sync SQLite gets no driver dependency: sqlite3 is in the standard
    library."""
    driver = options.driver
    if expected is None:
        assert driver is None
    else:
        assert driver is not None
        assert driver.split(">=")[0] == expected
        assert ">=" in driver


@pytest.mark.parametrize(
    "options, expected",
    [
        (
            Options(name="shop"),
            "postgresql+asyncpg://postgres:postgres@localhost:5432/shop",
        ),
        (
            Options(name="shop", is_async=False),
            "postgresql+psycopg://postgres:postgres@localhost:5432/shop",
        ),
        (Options(name="shop", postgres=False), "sqlite+aiosqlite:///./shop.db"),
        (Options(name="shop", is_async=False, postgres=False), "sqlite:///./shop.db"),
    ],
    ids=["async-postgres", "sync-postgres", "async-sqlite", "sync-sqlite"],
)
def test_database_url(options, expected):
    assert options.database_url == expected


# ---------------------------------------------------------------------------
# Built files
# ---------------------------------------------------------------------------


def test_build_files_covers_the_four_mixed_files():
    assert set(build_files(Options(name="shop"))) == {
        "pyproject.toml",
        ".env.example",
        "tests/conftest.py",
        "README.md",
    }


def test_pyproject_uses_the_project_name():
    pyproject = build_pyproject(Options(name="shop"))

    assert 'name = "shop"' in pyproject
    assert 'entrypoint = "shop.asgi:app"' in pyproject
    assert PLACEHOLDER not in pyproject


@pytest.mark.parametrize(
    "options, expected",
    [
        (Options(name="shop"), "asyncpg>="),
        (Options(name="shop", is_async=False), "psycopg[binary]>="),
        (Options(name="shop", postgres=False), "aiosqlite>="),
    ],
    ids=["async-postgres", "sync-postgres", "async-sqlite"],
)
def test_pyproject_depends_on_the_driver(options, expected):
    assert f'"{expected}' in build_pyproject(options)


def test_pyproject_has_no_driver_for_sync_sqlite():
    pyproject = build_pyproject(Options(name="shop", is_async=False, postgres=False))

    for driver in ("asyncpg", "psycopg", "aiosqlite"):
        assert driver not in pyproject
    assert '"fastapi-restly[standard]>=' in pyproject


@pytest.mark.parametrize("options", ALL_COMBINATIONS, ids=_combination_id)
def test_pyproject_floors_restly_at_the_generating_version(options):
    """A generated project uses whatever this scaffold emits, so it needs at
    least the release that emitted it."""
    from fastapi_restly import __version__

    pyproject = build_pyproject(options)

    assert f'"fastapi-restly[standard]>={__version__}"' in pyproject
    assert f'"fastapi-restly[testing]>={__version__}"' in pyproject


@pytest.mark.parametrize("options", ALL_COMBINATIONS, ids=_combination_id)
def test_pyproject_lists_alembic_only_with_migrations(options):
    pyproject = build_pyproject(options)

    assert ('"alembic>=' in pyproject) is options.alembic


@pytest.mark.parametrize("options", ALL_COMBINATIONS, ids=_combination_id)
def test_pyproject_configures_asyncio_only_for_async(options):
    pyproject = build_pyproject(options)

    assert ("asyncio_mode" in pyproject) is options.is_async


def test_conftest_builds_the_schema_from_the_models_without_alembic():
    call = _configure_tests_call(build_conftest(Options(name="shop", alembic=False)))

    assert "create_all=True" in call
    assert "alembic_upgrade" not in call


def test_conftest_builds_the_schema_by_migrating_with_alembic():
    call = _configure_tests_call(build_conftest(Options(name="shop", alembic=True)))

    assert "alembic_upgrade=True" in call
    assert "create_all" not in call


@pytest.mark.parametrize("options", ALL_COMBINATIONS, ids=_combination_id)
def test_conftest_explains_the_file_database_to_sqlite_projects(options):
    """The note answers the question only a SQLite URL raises."""
    assert ("A file rather than :memory:" in build_conftest(options)) is (
        not options.postgres
    )


def test_conftest_imports_the_generated_package():
    conftest = build_conftest(Options(name="shop"))

    assert "from shop.main import create_app" in conftest
    assert "from shop.settings import Settings" in conftest
    assert PLACEHOLDER not in conftest


@pytest.mark.parametrize("options", ALL_COMBINATIONS, ids=_combination_id)
def test_conftest_defaults_to_the_matching_test_database(options):
    assert f'"{options.test_database_url}"' in build_conftest(options)


@pytest.mark.parametrize("options", ALL_COMBINATIONS, ids=_combination_id)
def test_env_example_carries_the_development_url(options):
    comment, setting = build_env_example(options).splitlines()

    assert comment.startswith("# Copy to .env.")
    assert setting == f"DATABASE_URL={options.database_url}"


def test_env_example_points_at_compose_for_postgres():
    assert "compose.yaml" in build_env_example(Options(name="shop"))


@pytest.mark.parametrize("options", ALL_COMBINATIONS, ids=_combination_id)
def test_readme_documents_the_chosen_axes(options):
    readme = build_readme(options)

    assert readme.startswith("# shop\n")
    assert ("docker compose up" in readme) is options.postgres
    assert ("## Services" in readme) is options.postgres
    assert ("alembic upgrade head" in readme) is options.alembic
    assert ("## Migrations" in readme) is options.alembic
    assert ("## Schema" in readme) is not options.alembic
    assert PLACEHOLDER not in readme


# ---------------------------------------------------------------------------
# generate()
# ---------------------------------------------------------------------------


def test_generate_writes_the_built_files_alongside_the_overlays(tmp_path):
    options = Options(name="shop")
    root = _template_root(
        tmp_path / "templates", {f"base/{PLACEHOLDER}/__init__.py": ""}, options
    )
    destination = tmp_path / "out"
    written = generate(options, destination, root)

    assert _written(written, destination) >= {
        "shop/__init__.py",
        "pyproject.toml",
        ".env.example",
        "tests/conftest.py",
        "README.md",
    }
    assert written == sorted(written)


def test_built_files_win_over_a_template_of_the_same_name(tmp_path):
    """The built files are assembled last, so an overlay cannot shadow one."""
    options = Options(name="shop")
    root = _template_root(
        tmp_path / "templates", {"base/pyproject.toml": "from the overlay\n"}, options
    )
    destination = tmp_path / "out"
    generate(options, destination, root)

    assert (destination / "pyproject.toml").read_text() != "from the overlay\n"
    assert 'name = "shop"' in (destination / "pyproject.toml").read_text()


def test_generate_creates_missing_parent_directories(tmp_path):
    options = Options(name="shop")
    root = _template_root(
        tmp_path / "templates", {"base/a/b/c/deep.txt": "deep\n"}, options
    )
    destination = tmp_path / "out" / "nested"
    generate(options, destination, root)

    assert (destination / "a" / "b" / "c" / "deep.txt").read_text() == "deep\n"


def test_generate_composites_overlays_in_option_order(tmp_path):
    """The overlay that ``Options`` puts last is the one on disk."""
    root = _tree(
        tmp_path / "templates",
        {
            "base/myapp/main.py": "base\n",
            "app_async/myapp/main.py": "async\n",
            "app_sync/myapp/main.py": "sync\n",
        },
    )

    generate(Options(name="shop", postgres=False, alembic=False), tmp_path / "a", root)
    assert (tmp_path / "a" / "shop" / "main.py").read_text() == "async\n"

    generate(
        Options(name="shop", is_async=False, postgres=False, alembic=False),
        tmp_path / "s",
        root,
    )
    assert (tmp_path / "s" / "shop" / "main.py").read_text() == "sync\n"


def test_generate_with_the_shipped_templates(tmp_path):
    """Loose smoke test of the real tree; the combinations themselves are the
    job of ``scripts/scaffold_matrix.sh``."""
    destination = tmp_path / "shop"
    written = generate(Options(name="shop"), destination)
    relative = _written(written, destination)

    assert relative >= {
        "pyproject.toml",
        "README.md",
        ".env.example",
        ".gitignore",
        "tests/conftest.py",
        "shop/__init__.py",
        "shop/main.py",
        "shop/asgi.py",
        "shop/settings.py",
    }
    # Nothing keeps the placeholder, in a path or in a file.
    assert not list(destination.rglob(PLACEHOLDER))
    for path in destination.rglob("*"):
        if path.is_file():
            assert not re.search(rf"\b{PLACEHOLDER}\b", path.read_text("utf-8"))


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


class _Terminal(io.StringIO):
    """A stdin that claims to be interactive, so the prompt path runs."""

    def isatty(self) -> bool:
        return True


@pytest.fixture
def not_a_terminal(monkeypatch):
    """The CLI prompts only on a terminal; most tests want the quiet path."""
    monkeypatch.setattr(_cli.sys, "stdin", io.StringIO())


def _options_for(argv: list[str]) -> Options:
    return _cli._resolve(_cli._build_parser().parse_args(argv))


@pytest.mark.parametrize("name", ["my-app", "my app", "9lives", "", "my.app", "café!"])
def test_a_name_that_is_not_an_identifier_is_rejected(name, capsys):
    with pytest.raises(SystemExit) as exit_info:
        _cli.main(["new", name])

    assert exit_info.value.code == 2
    assert "not usable as a Python package name" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["class", "import", "None", "lambda"])
def test_a_python_keyword_is_rejected(name, capsys):
    with pytest.raises(SystemExit) as exit_info:
        _cli.main(["new", name])

    assert exit_info.value.code == 2
    assert "not usable as a Python package name" in capsys.readouterr().err


def test_a_non_empty_destination_is_refused(tmp_path, capsys):
    (tmp_path / "existing.txt").write_text("keep me\n")

    with pytest.raises(SystemExit) as exit_info:
        _cli.main(["new", "shop", "--directory", str(tmp_path)])

    assert exit_info.value.code == 2
    assert "already exists and is not empty" in capsys.readouterr().err
    assert (tmp_path / "existing.txt").read_text() == "keep me\n"


def test_an_empty_existing_destination_is_accepted(tmp_path, not_a_terminal):
    destination = tmp_path / "empty"
    destination.mkdir()

    assert _cli.main(["new", "shop", "--directory", str(destination), "-y"]) == 0
    assert (destination / "pyproject.toml").exists()


def test_the_destination_defaults_to_the_name(tmp_path, monkeypatch, not_a_terminal):
    monkeypatch.chdir(tmp_path)

    assert _cli.main(["new", "shop", "-y"]) == 0
    assert (tmp_path / "shop" / "shop" / "main.py").exists()


def test_main_reports_what_it_wrote_and_what_to_do_next(
    tmp_path, not_a_terminal, capsys
):
    destination = tmp_path / "shop"

    assert _cli.main(["new", "shop", "--directory", str(destination), "-y"]) == 0

    out = capsys.readouterr().out
    assert f"Created {destination}/" in out
    assert "uv sync" in out
    assert "docker compose up -d --wait db test-db" in out
    assert "uv run alembic upgrade head" in out


@pytest.mark.parametrize(
    "argv, expected",
    [
        ([], Options(name="shop")),
        (["--async", "--postgres", "--alembic"], Options(name="shop")),
        (["--sync"], Options(name="shop", is_async=False)),
        (["--sqlite"], Options(name="shop", postgres=False)),
        (["--create-all"], Options(name="shop", alembic=False)),
        (
            ["--sync", "--sqlite", "--create-all"],
            Options(name="shop", is_async=False, postgres=False, alembic=False),
        ),
    ],
    ids=["defaults", "explicit", "sync", "sqlite", "create-all", "all-flipped"],
)
def test_flags_flip_the_defaults(argv, expected, not_a_terminal):
    assert _options_for(["new", "shop", *argv]) == expected


def test_a_later_flag_wins_over_an_earlier_one(not_a_terminal):
    assert _options_for(["new", "shop", "--sync", "--async"]).is_async is True
    assert _options_for(["new", "shop", "--async", "--sync"]).is_async is False


def test_unanswered_choices_take_the_defaults_off_a_terminal(not_a_terminal):
    """Nothing may block on input in CI, so a non-interactive run never asks."""
    assert _options_for(["new", "shop"]) == Options(name="shop")


def test_a_terminal_is_prompted_for_the_unanswered_choices(monkeypatch):
    monkeypatch.setattr(_cli.sys, "stdin", _Terminal())
    answers = iter(["sync", "sqlite", "create-all"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert _options_for(["new", "shop"]) == Options(
        name="shop", is_async=False, postgres=False, alembic=False
    )


def test_a_flag_answers_its_question_so_it_is_not_asked(monkeypatch):
    monkeypatch.setattr(_cli.sys, "stdin", _Terminal())
    asked = []

    def record(prompt: str) -> str:
        asked.append(prompt)
        return ""

    monkeypatch.setattr("builtins.input", record)
    options = _options_for(["new", "shop", "--sync"])

    assert options == Options(name="shop", is_async=False)
    assert len(asked) == 2
    assert not any("Async" in prompt for prompt in asked)


def test_a_prefix_answers_the_prompt(monkeypatch):
    """The prompt renders as ``[async/sync]``, so a single letter has to work.

    Without this, `startswith` could become `==` with the suite still green.
    """
    monkeypatch.setattr(_cli.sys, "stdin", _Terminal())
    answers = iter(["s", "sq", "c"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert _options_for(["new", "shop"]) == Options(
        name="shop", is_async=False, postgres=False, alembic=False
    )


def test_answering_with_the_default_side_is_taken_as_an_answer(monkeypatch):
    """Pins that a prompt can return True, which taking the defaults cannot show."""
    monkeypatch.setattr(_cli.sys, "stdin", _Terminal())
    answers = iter(["a", "postgres", "alembic"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert _options_for(["new", "shop"]) == Options(
        name="shop", is_async=True, postgres=True, alembic=True
    )


def test_next_steps_mention_compose_only_for_postgres():
    """A SQLite project has no compose.yaml, so it must not be told to run it."""
    postgres = _cli._next_steps(Options(name="shop"))
    sqlite = _cli._next_steps(Options(name="shop", postgres=False))

    assert any("docker compose" in step for step in postgres)
    assert not any("docker compose" in step for step in sqlite)


def test_an_unrecognised_answer_keeps_the_default(monkeypatch):
    monkeypatch.setattr(_cli.sys, "stdin", _Terminal())
    answers = iter(["maybe", "", "  "])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert _options_for(["new", "shop"]) == Options(name="shop")


def test_yes_takes_the_defaults_without_asking(monkeypatch):
    monkeypatch.setattr(_cli.sys, "stdin", _Terminal())

    def refuse(prompt: str) -> str:
        raise AssertionError(f"prompted despite --yes: {prompt}")

    monkeypatch.setattr("builtins.input", refuse)

    assert _options_for(["new", "shop", "-y"]) == Options(name="shop")


def test_a_missing_subcommand_is_rejected(capsys):
    with pytest.raises(SystemExit) as exit_info:
        _cli.main([])

    assert exit_info.value.code == 2
    assert "required" in capsys.readouterr().err
