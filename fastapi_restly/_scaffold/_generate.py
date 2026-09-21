"""Build a new project from the bundled templates.

Two mechanisms, and the split between them is the design:

* **Overlay directories** hold whole files that are identical within one axis.
  They are composited in order, later winning, so choosing ``app_sync`` over
  ``app_async`` selects a different real ``users/views.py``, and choosing
  ``createall_async`` over ``alembic_async`` a different ``main.py``. The
  package is ``app`` in the tree and in the output, so a template is ordinary
  Python rather than markup. It is linted as a generated project, not as repo
  source: here ``fastapi_restly`` is first-party and there it is third-party, so
  one import order cannot satisfy both. ``scripts/scaffold_matrix.sh`` is the
  gate.
* **Built files** are the ones whose content mixes axes -- ``pyproject.toml``,
  ``.env.example``, ``tests/conftest.py``, ``README.md`` and
  ``pyrightconfig.json``. Their content is assembled here rather than templated,
  which is why no template engine is needed at all.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

# The package is the same in every generated project, so every import path in
# the docs, the examples and a reader's own code is the one they already read.
# The project name names the project: the directory, pyproject, the databases.
PACKAGE = "app"

PLACEHOLDER = "myapp"

# Not \bmyapp\b: an underscore is a word character, so that would leave
# `myapp_test` (the compose test database) alone and desynchronise it from the
# URL in tests/conftest.py. Identifiers *derived* from the package name have to
# move with it; only a longer word containing it must not.
_PLACEHOLDER_RE = re.compile(rf"(?<![A-Za-z0-9_]){PLACEHOLDER}(?![A-Za-z0-9])")

# Stored dot-less in the template tree for two reasons: a real ``.gitignore``
# there would be applied by git to the templates themselves, and setuptools'
# package-data glob does not match leading dots, so the file would never ship.
DOTFILE_NAMES = {"gitignore": ".gitignore", "gitkeep": ".gitkeep"}

_TEMPLATE_PACKAGE = "fastapi_restly._scaffold.templates"


@dataclass(frozen=True)
class Options:
    """One generated project's answers."""

    name: str
    is_async: bool = True
    postgres: bool = True
    alembic: bool = True

    @property
    def overlays(self) -> tuple[str, ...]:
        """Template directories to composite, in order. Later ones win."""
        suffix = "async" if self.is_async else "sync"
        names = ["base", f"app_{suffix}"]
        if self.postgres:
            names.append("db_postgres")
        # main.py lives here rather than in app_*: its lifespan depends on the
        # schema choice as well, since a create_all project builds its tables at
        # startup and an Alembic one must not.
        names.append(f"{'alembic' if self.alembic else 'createall'}_{suffix}")
        return tuple(names)

    @property
    def driver(self) -> str | None:
        """The database driver to depend on, or None when stdlib covers it."""
        if self.postgres:
            return "asyncpg>=0.30.0" if self.is_async else "psycopg[binary]>=3.2.0"
        # SQLite's synchronous driver is in the standard library.
        return "aiosqlite>=0.21.0" if self.is_async else None

    @property
    def scheme(self) -> str:
        if self.postgres:
            return "postgresql+asyncpg" if self.is_async else "postgresql+psycopg"
        return "sqlite+aiosqlite" if self.is_async else "sqlite"

    @property
    def database_url(self) -> str:
        """The development database, as written into ``.env.example``."""
        if self.postgres:
            return f"{self.scheme}://postgres:postgres@localhost:5432/{self.name}"
        return f"{self.scheme}:///./{self.name}.db"

    @property
    def test_database_url(self) -> str:
        """The database the test suite builds its schema in.

        SQLite gets a file, never ``:memory:``. An in-memory database lives
        inside its connection, and two things here open a second one: Alembic
        migrates through its own connection, and the async lifespan disposes
        the engine, which the test client runs at the end of every test. Either
        one leaves the suite looking at an empty database.
        """
        if self.postgres:
            return f"{self.scheme}://postgres:postgres@localhost:5433/{self.name}_test"
        return f"{self.scheme}:///./test.db"


def _walk(node: Any, prefix: Path = Path()) -> Iterator[tuple[Path, Any]]:
    """Yield ``(relative path, file)`` for every file under ``node``.

    Walks the ``Traversable`` API directly rather than going through
    ``importlib.resources.as_file()``, which only supports directory
    traversables from Python 3.12 while Restly still supports 3.10.
    """
    for child in sorted(node.iterdir(), key=lambda c: c.name):
        relative = prefix / child.name
        if child.is_dir():
            yield from _walk(child, relative)
        else:
            yield relative, child


def _plan(overlays: tuple[str, ...], root: Any | None = None) -> dict[Path, Any]:
    """Map each destination path to the template file that wins for it.

    ``root`` defaults to the bundled templates; tests pass a synthetic tree so
    the compositing rule is checked independently of what currently ships.
    """
    if root is None:
        root = files(_TEMPLATE_PACKAGE)
    plan: dict[Path, Any] = {}
    for overlay in overlays:
        directory = root / overlay
        if not directory.is_dir():
            # Otherwise this surfaces as a bare FileNotFoundError from iterdir,
            # naming a path rather than the missing overlay.
            raise FileNotFoundError(
                f"Scaffold template overlay {overlay!r} is missing. The overlay "
                f"set and the template tree have gone out of step."
            )
        for relative, node in _walk(directory):
            plan[relative] = node
    return plan


def _destination(relative: Path, name: str) -> Path:
    """Rewrite a template path: placeholder package renamed, dotfiles restored."""
    parts = [
        name if part == PLACEHOLDER else DOTFILE_NAMES.get(part, part)
        for part in relative.parts
    ]
    return Path(*parts)


def rename(text: str, name: str) -> str:
    """Replace the placeholder package name, and identifiers derived from it.

    ``myapp_test`` becomes ``<name>_test`` and ``myapp-data`` becomes
    ``<name>-data``, because those have to keep agreeing with the package. A
    longer *word* containing the placeholder, such as ``myapplication`` or
    ``my_myapp_thing``, is left alone.
    """
    return _PLACEHOLDER_RE.sub(name, text)


def generate(
    options: Options, destination: Path, root: Any | None = None
) -> list[Path]:
    """Write a project into ``destination``. Returns the paths written, sorted.

    A built file wins over an overlay file of the same path, and is counted
    once, so the caller's file count stays honest either way.
    """
    written: set[Path] = set()

    for relative, node in sorted(_plan(options.overlays, root).items()):
        target = destination / _destination(relative, options.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rename(node.read_text("utf-8"), options.name), "utf-8")
        written.add(target)

    for relative_name, content in build_files(options).items():
        target = destination / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, "utf-8")
        written.add(target)

    return sorted(written)


def build_files(options: Options) -> dict[str, str]:
    """The files whose content depends on more than one axis."""
    return {
        "pyproject.toml": build_pyproject(options),
        ".env.example": build_env_example(options),
        "tests/conftest.py": build_conftest(options),
        "README.md": build_readme(options),
        "pyrightconfig.json": build_pyrightconfig(options),
    }


def restly_requirement(extra: str) -> str:
    """``fastapi-restly`` pinned at or above the version that generated this.

    A generated project uses whatever the scaffold currently emits, so it needs
    at least the release that emitted it. Without a floor, resolution could pick
    an older one that lacks something the templates call.
    """
    from fastapi_restly import __version__

    floor = "" if __version__ == "0+unknown" else f">={__version__}"
    return f"fastapi-restly[{extra}]{floor}"


def build_pyrightconfig(options: Options) -> str:
    """Type-check the application, its tests, and the Alembic environment.

    ``alembic/versions`` is excluded: Alembic writes those, so they are not
    yours to answer for. ``alembic/env.py`` very much is.
    """
    include = f'["{PACKAGE}", "tests"]'
    exclude = '["**/__pycache__", ".venv"]'
    if options.alembic:
        include = f'["{PACKAGE}", "tests", "alembic"]'
        exclude = '["**/__pycache__", ".venv", "alembic/versions"]'
    return (
        "{\n"
        f'    "include": {include},\n'
        f'    "exclude": {exclude},\n'
        '    "pythonVersion": "3.10"\n'
        "}\n"
    )


def build_pyproject(options: Options) -> str:
    dependencies = [restly_requirement("standard")]
    if options.driver is not None:
        dependencies.append(options.driver)
    if options.alembic:
        dependencies.append("alembic>=1.15.2")

    listed = "\n".join(f'    "{item}",' for item in dependencies)
    # Migrations are excluded rather than linted, and both halves are forced:
    # Alembic writes `import sqlalchemy as sa` into every migration whether it
    # is used or not, so an op-only migration is F401 by construction, and
    # autogenerate injects any extra import at a fixed position after
    # `from alembic import op`, which is the wrong sort position for I001.
    # Neither is reachable from script.py.mako.
    ruff_alembic = (
        '[tool.ruff]\nextend-exclude = ["alembic/versions"]\n\n'
        if options.alembic
        else ""
    )
    # The alembic/ directory at the project root makes ruff file `alembic` as
    # first-party, which puts `from alembic import context` in the same block as
    # the `app` imports. Without this the generated project fails its own
    # `ruff check` with I001 on the first command.
    isort_alembic = (
        '\n[tool.ruff.lint.isort]\nknown-third-party = ["alembic"]\n'
        if options.alembic
        else ""
    )
    asyncio_options = (
        '\nasyncio_mode = "auto"\nasyncio_default_fixture_loop_scope = "function"'
        if options.is_async
        else ""
    )
    # Two settings a reader would otherwise wonder about, explained here rather
    # than in the file they land in:
    # * `[tool.uv] package = false` because this is an application and not a
    #   distribution, so there is nothing to build and no backend to configure.
    #   pytest reaches the package through pythonpath instead.
    # * `[tool.fastapi] entrypoint` because `fastapi dev` and `fastapi run`
    #   cannot call a factory, so they need the one module holding an app
    #   object. See asgi.py.
    return f"""[project]
name = "{options.name}"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
{listed}
]

[dependency-groups]
dev = [
    "{restly_requirement("testing")}",
    "pyright>=1.1.390",
    "ruff>=0.8.0",
]

[tool.uv]
package = false

[tool.fastapi]
entrypoint = "{PACKAGE}.asgi:app"

[tool.pytest.ini_options]
pythonpath = ["."]{asyncio_options}

{ruff_alembic}[tool.ruff.lint]
select = ["E4", "E7", "E9", "F", "I"]
{isort_alembic}"""


def build_env_example(options: Options) -> str:
    where = (
        "The development database from compose.yaml."
        if options.postgres
        else "A file beside the project root; delete it to start over."
    )
    return f"# Copy to .env. {where}\nDATABASE_URL={options.database_url}\n"


def build_conftest(options: Options) -> str:
    schema_setup = "alembic_upgrade=True" if options.alembic else "create_all=True"
    # A file rather than :memory: is the one choice here that looks like an
    # oversight, so it keeps its reason. Everything else is left to read as
    # ordinary test setup.
    memory_note = (
        ""
        if options.postgres
        else "# A file rather than :memory:, which lives inside its connection and\n"
        "# would be lost the first time anything opened a second one.\n"
    )
    return f'''import os

import fastapi_restly as fr

from {PACKAGE}.main import create_app
from {PACKAGE}.settings import Settings

{memory_note}TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "{options.test_database_url}",
)

# _env_file=None so a local .env cannot point the suite at the development
# database. The ignore is for the type checker: pydantic-settings accepts the
# underscore arguments at runtime but does not declare them.
Settings.use(Settings(database_url=TEST_DATABASE_URL, _env_file=None))  # type: ignore[call-arg]

app = create_app()
fr.testing.configure_tests(app=app, base=fr.DataclassBase, {schema_setup})
'''


def build_readme(options: Options) -> str:
    steps = ["uv sync"]
    if options.postgres:
        steps.append("docker compose up -d --wait db test-db")
    steps.append("cp .env.example .env")
    if options.alembic:
        steps.append('uv run alembic revision --autogenerate -m "initial"')
        steps.append("uv run alembic upgrade head")
    steps.append("uv run fastapi dev")

    migrations = (
        f"""
## Migrations

Alembic owns the schema. After changing a model:

```bash
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

`alembic/env.py` imports `{PACKAGE}.main`, which reaches every view and
through each view its models, so autogenerate sees the whole schema. A model
that no view reaches must be imported wherever it is used. Run `alembic check`
in CI to catch one that is missed.
"""
        if options.alembic
        else f"""
## Schema

This project has no migrations. `{PACKAGE}/main.py` creates the tables at
startup, and the test suite builds its own the same way.

Add Alembic before you deploy anything you care about. `create_all` adds tables
that are missing and never alters one that exists, so it cannot carry a schema
forward: a renamed column or a new constraint will not appear. See the
deployment guide.
"""
    )

    compose = (
        """
## Services

`compose.yaml` runs two PostgreSQL databases: `db` for development and `test-db`
for the test suite, so a test run never touches development data.
"""
        if options.postgres
        else ""
    )

    test_note = (
        "\nThe suite builds its schema by running the migrations, so it needs at"
        " least one\nbefore it passes. Its database is separate from the"
        " development one.\n"
        if options.alembic
        else "\nThe suite builds its schema from the models, in a database"
        " separate from the\ndevelopment one.\n"
    )

    docker_note = ", and Docker for the databases" if options.postgres else ""

    numbered = "\n".join(steps)
    return f"""# {options.name}

A REST API built with [FastAPI-Restly](https://www.fastapi-restly.org).

Requires [uv](https://docs.astral.sh/uv/){docker_note}.

## Getting started

```bash
{numbered}
```

The API is then at <http://127.0.0.1:8000>, with interactive documentation at
<http://127.0.0.1:8000/docs> and a liveness endpoint at
<http://127.0.0.1:8000/health>.

## Layout

```text
{PACKAGE}/
├── main.py        Application factory and the VIEWS it registers
├── asgi.py        app = create_app(), the only module a server imports
├── settings.py    Environment settings
└── users/         One resource: model, schemas, view
tests/
```

Each resource is a package holding its model, its schemas, and its view,
because those three change together. Add a package beside `users/`, then add
its view to `VIEWS` in `main.py`.

Importing `main.py` must stay free of side effects: it defines `create_app()`
and builds nothing, which is what lets the test suite name its own database.
{compose}{migrations}
## Tests

```bash
uv run pytest
```
{test_note}
## Further reading

- [Project structure](https://www.fastapi-restly.org/howto_project_structure.html)
- [Deploying](https://www.fastapi-restly.org/deploying.html)
- [Testing](https://www.fastapi-restly.org/howto_testing.html)
"""
