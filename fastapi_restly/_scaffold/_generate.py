"""Build a new project from the bundled templates.

Two mechanisms, and the split between them is the design:

* **Overlay directories** hold whole files that are identical within one axis.
  They are composited in order, later winning, so choosing ``app_sync`` over
  ``app_async`` selects a different real ``main.py``. The placeholder package
  name is a valid identifier, so a template is ordinary Python rather than
  markup. It is linted as a generated project, not as repo source: here
  ``fastapi_restly`` is first-party and there it is third-party, so one import
  order cannot satisfy both. ``scripts/scaffold_matrix.sh`` is the gate.
* **Built files** are the ones whose content mixes axes -- ``pyproject.toml``,
  ``.env.example``, ``tests/conftest.py``, ``README.md``. Their content is
  assembled here rather than templated, which is why no template engine is
  needed at all.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

PLACEHOLDER = "myapp"
_PLACEHOLDER_RE = re.compile(rf"\b{PLACEHOLDER}\b")

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
        names = ["base", "app_async" if self.is_async else "app_sync"]
        if self.postgres:
            names.append("db_postgres")
        if self.alembic:
            names.append("alembic_async" if self.is_async else "alembic_sync")
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
    """Replace whole-word occurrences of the placeholder package name.

    Word-bounded so a longer identifier containing it is left alone.
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
    }


def build_pyproject(options: Options) -> str:
    dependencies = ["fastapi-restly[standard]"]
    if options.driver is not None:
        dependencies.append(options.driver)
    if options.alembic:
        dependencies.append("alembic>=1.15.2")

    listed = "\n".join(f'    "{item}",' for item in dependencies)
    asyncio_options = (
        '\nasyncio_mode = "auto"\nasyncio_default_fixture_loop_scope = "function"'
        if options.is_async
        else ""
    )
    return f"""[project]
name = "{options.name}"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
{listed}
]

[dependency-groups]
dev = [
    "fastapi-restly[testing]",
    "pyright>=1.1.390",
    "ruff>=0.8.0",
]

# Not a distribution: this is an application, so there is nothing to build and
# no build backend to configure. pytest finds the package through pythonpath.
[tool.uv]
package = false

# `fastapi dev` and `fastapi run` cannot call a factory, so point them at the
# one module that holds an application object.
[tool.fastapi]
entrypoint = "{options.name}.asgi:app"

[tool.pytest.ini_options]
pythonpath = ["."]{asyncio_options}

# Alembic writes migrations, so their layout is not yours to answer for.
[tool.ruff]
extend-exclude = ["alembic/versions"]

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F", "I"]
"""


def build_env_example(options: Options) -> str:
    where = (
        "The development database from compose.yaml."
        if options.postgres
        else "A file beside the project root; delete it to start over."
    )
    return f"# Copy to .env. {where}\nDATABASE_URL={options.database_url}\n"


def build_conftest(options: Options) -> str:
    schema_setup = "alembic_upgrade=True" if options.alembic else "create_all=True"
    schema_comment = (
        "Build the schema by running the migrations, so the tests exercise the\n"
        "# same path a deployment does."
        if options.alembic
        else "Build the schema straight from the models. Swap to\n"
        "# alembic_upgrade=True once you start keeping migrations."
    )
    memory_note = (
        ""
        if options.postgres
        else "# A file rather than :memory:. An in-memory database lives inside its\n"
        "# connection, and the test client would lose it the first time something\n"
        "# else opened one. Delete test.db to start the suite from scratch.\n"
    )
    return f'''"""Test configuration.

The suite builds its own settings and installs them before calling the factory,
so no environment variable has to be set before an import.
"""

import os

import fastapi_restly as fr

from {options.name}.main import create_app
from {options.name}.settings import Settings

{memory_note}TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "{options.test_database_url}",
)

# _env_file=None so a developer's local .env cannot redirect the test suite.
# The ignore is for the type checker only: pydantic-settings accepts these
# underscore arguments at runtime, but they are not in the synthesized __init__.
Settings.use(Settings(database_url=TEST_DATABASE_URL, _env_file=None))  # type: ignore[call-arg]

app = create_app()

# {schema_comment}
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

`alembic/env.py` imports `{options.name}.main`, which reaches every view and
through each view its models, so autogenerate sees the whole schema. A model no
view reaches must be imported wherever it is used. Run `alembic check` in CI to
catch one that is missed.
"""
        if options.alembic
        else f"""
## Schema

The test suite builds the schema from the models with `create_all`, and
`{options.name}/main.py` does not create tables at startup. Add Alembic before
you deploy anything you care about; see the deployment guide.
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

    numbered = "\n".join(steps)
    return f"""# {options.name}

A REST API built with [FastAPI-Restly](https://www.fastapi-restly.org).

## Getting started

```bash
{numbered}
```

The API is then at <http://127.0.0.1:8000>, with interactive documentation at
<http://127.0.0.1:8000/docs> and a liveness endpoint at
<http://127.0.0.1:8000/health>.

## Layout

```text
{options.name}/
├── main.py        Application factory and the VIEWS it registers
├── asgi.py        app = create_app(), the only module a server imports
├── settings.py    Environment settings
└── tasks/         One resource: model, schemas, view
tests/
```

Each resource is a package holding its model, its schemas, and its view,
because those three change together. Add a package beside `tasks/`, then add
its view to `VIEWS` in `main.py`.

Importing `main.py` must stay free of side effects: it defines `create_app()`
and builds nothing, which is what lets the test suite name its own database.
{compose}{migrations}
## Tests

```bash
uv run pytest
```

## Further reading

- [Structure a project](https://www.fastapi-restly.org/howto_project_structure.html)
- [Deploying](https://www.fastapi-restly.org/deploying.html)
- [Testing](https://www.fastapi-restly.org/howto_testing.html)
"""
