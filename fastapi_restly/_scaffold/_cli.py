"""The ``restly`` command."""

from __future__ import annotations

import argparse
import keyword
import re
import sys
from pathlib import Path

from ._generate import Options, generate

_DESCRIPTION = "Create a new FastAPI-Restly project."

# The name is both a Python package and the project name in pyproject.toml, and
# PEP 508 forbids a leading or trailing underscore in the latter.
_PROJECT_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*[A-Za-z0-9]|[A-Za-z]")

# Names that would land the package on top of something the generator also
# writes, or shadow a module a generated project imports. `pythonpath = ["."]`
# puts the project root first, so `restly new datetime` gives a project whose
# own models.py cannot import datetime.
_GENERATED_DIRECTORIES = frozenset({"alembic", "tests"})

# Distributions a generated project imports, which the standard library listing
# below cannot know about. A test derives the templates' imports and fails if
# this falls behind them.
_IMPORTED_DISTRIBUTIONS = frozenset(
    {
        "aiosqlite",
        "asyncpg",
        "fastapi",
        "fastapi_restly",
        "psycopg",
        "pydantic",
        "pydantic_settings",
        "pytest",
        "sqlalchemy",
    }
)

RESERVED_NAMES = (
    _GENERATED_DIRECTORIES | _IMPORTED_DISTRIBUTIONS | sys.stdlib_module_names
)

# The generated conftest writes `from <name>.settings import Settings`, which is
# 30 columns plus the name. Past 58 ruff wants it wrapped, and the project fails
# its own `ruff check` on the first command. Refuse well before that.
MAX_NAME_LENGTH = 50


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="restly", description=_DESCRIPTION)
    subcommands = parser.add_subparsers(dest="command", required=True)

    new = subcommands.add_parser("new", help=_DESCRIPTION)
    # Errors about `new`'s own arguments should print `new`'s usage, so main
    # needs the subparser back.
    new.set_defaults(subparser=new)

    # Optional so that an interactive run can ask for it. main enforces it
    # everywhere else, because argparse cannot tell the two cases apart.
    new.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Project and package name, e.g. myapp. Asked for if omitted",
    )
    new.add_argument(
        "--directory",
        type=Path,
        default=None,
        help="Where to write it (default: ./<name>)",
    )

    # `--async` needs an explicit dest: argparse would derive `args.async`,
    # which is a keyword and cannot be written down.
    new.add_argument(
        "--async",
        dest="is_async",
        action="store_true",
        default=None,
        help="Async views and driver (default)",
    )
    new.add_argument(
        "--sync",
        dest="is_async",
        action="store_false",
        help="Synchronous views and driver",
    )

    new.add_argument(
        "--postgres",
        dest="postgres",
        action="store_true",
        default=None,
        help="PostgreSQL, with a compose file (default)",
    )
    new.add_argument(
        "--sqlite",
        dest="postgres",
        action="store_false",
        help="SQLite, in a file beside the project",
    )

    new.add_argument(
        "--alembic",
        dest="alembic",
        action="store_true",
        default=None,
        help="Alembic migrations (default)",
    )
    new.add_argument(
        "--create-all",
        dest="alembic",
        action="store_false",
        help="No migrations: build the schema from the models, at startup and in tests",
    )

    new.add_argument(
        "--yes", "-y", action="store_true", help="Take the defaults without prompting"
    )
    return parser


def _name_problem(name: str) -> str | None:
    """Why *name* cannot be a package and project name, or None if it can."""
    if (
        not name.isidentifier()
        or keyword.iskeyword(name)
        # Also the project name in pyproject.toml, where PEP 508 forbids a
        # leading or trailing underscore.
        or not _PROJECT_NAME_RE.fullmatch(name)
    ):
        return (
            f"{name!r} is not usable as a package and project name. Use letters, "
            "digits and underscores, starting with a letter and not ending in "
            "an underscore."
        )
    if len(name) > MAX_NAME_LENGTH:
        return (
            f"{name!r} is {len(name)} characters. Keep it to {MAX_NAME_LENGTH} or "
            "fewer, or the generated imports outgrow the project's own line length."
        )
    if name in RESERVED_NAMES:
        return (
            f"{name!r} would collide with a directory the project already has, "
            "or shadow a standard library module that the project imports."
        )
    return None


def _destination_problem(destination: Path) -> str | None:
    """Why *destination* cannot be written into, or None if it can."""
    if not destination.exists():
        return None
    if not destination.is_dir():
        return f"{str(destination)!r} exists and is not a directory."
    if any(destination.iterdir()):
        return f"{str(destination)!r} already exists and is not empty."
    return None


def _interactive(args: argparse.Namespace) -> bool:
    """Prompts run on a terminal only, and never under --yes."""
    return not args.yes and sys.stdin.isatty()


def _ask_name(directory: Path | None) -> str:
    """Ask for the project name until the answer is usable.

    Empty asks again rather than complaining, since there is no default here
    for enter to mean. When the destination follows the name, a directory
    already in use is a reason to ask again as well.
    """
    while True:
        name = input("Project name: ").strip()
        if not name:
            continue
        problem = _name_problem(name)
        if problem is None and directory is None:
            problem = _destination_problem(Path(name))
        if problem is None:
            return name
        print(f"  {problem}")


def _ask(
    question: str, default_yes: bool, first: tuple[str, str], second: tuple[str, str]
) -> bool:
    """Prompt for one either/or choice, by number or by name.

    Each choice is a name and a line saying what it does, because a name can
    only carry so much. Where a flag names a mechanism the choice is free to
    name the outcome instead, and leave the mechanism to the description.

    Enter takes the default. Anything else unrecognised asks again, rather
    than taking the default on the reader's behalf and leaving them to find
    out from the generated project which answer was recorded.
    """
    choices = (first, second)
    default = 0 if default_yes else 1
    width = max(len(name) for name, _ in choices)

    print(f"\n{question}")
    for position, (name, description) in enumerate(choices):
        marker = "(default)" if position == default else ""
        print(f"  {position + 1}  {name:<{width}}  {marker:<9}  {description}")

    while True:
        answer = input(f"Choice [{default + 1}]: ").strip().lower()
        if not answer:
            return default_yes
        if answer in ("1", "2"):
            return answer == "1"
        for position, (name, _) in enumerate(choices):
            if name.lower().startswith(answer):
                return position == 0
        # The choices stay on screen above, so repeat only what to type.
        print(f"  Answer 1 or 2, {first[0]} or {second[0]}, or enter for the default.")


def _resolve(args: argparse.Namespace) -> Options:
    """Fill unanswered choices, prompting only on a terminal."""
    is_async, postgres, alembic = args.is_async, args.postgres, args.alembic
    unanswered = [value is None for value in (is_async, postgres, alembic)]

    if any(unanswered) and _interactive(args):
        if is_async is None:
            is_async = _ask(
                "Async views?",
                True,
                ("async", "fr.AsyncRestView, an async session and driver"),
                ("sync", "fr.RestView, a synchronous session and driver"),
            )
        if postgres is None:
            postgres = _ask(
                "Database?",
                True,
                ("postgres", "PostgreSQL, with a compose.yaml"),
                ("sqlite", "SQLite, in a file beside the project"),
            )
        if alembic is None:
            alembic = _ask(
                "Migrations?",
                True,
                ("alembic", "versioned migrations, written by you"),
                # Named for the outcome, where --create-all names the
                # mechanism. The description carries the mechanism instead.
                ("no migrations", "the schema is built from the models by create_all"),
            )

    return Options(
        name=args.name,
        is_async=True if is_async is None else is_async,
        postgres=True if postgres is None else postgres,
        alembic=True if alembic is None else alembic,
    )


def _next_steps(options: Options, destination: Path) -> list[str]:
    steps = [f"cd {destination}", "uv sync"]
    if options.postgres:
        steps.append("docker compose up -d --wait db test-db")
    steps.append("cp .env.example .env")
    if options.alembic:
        steps.append('uv run alembic revision --autogenerate -m "initial"')
        steps.append("uv run alembic upgrade head")
    steps.append("uv run pytest")
    steps.append("uv run fastapi dev")
    return steps


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _new(args)
    except (EOFError, KeyboardInterrupt):
        # Ctrl-D or Ctrl-C part way through the interview. Both prompts loop
        # until answered, so neither ends on its own.
        print("\nCancelled.")
        return 1


def _new(args: argparse.Namespace) -> int:
    # Reading it off the namespace loses the type, and with it the fact that
    # `error` never returns.
    subparser: argparse.ArgumentParser = args.subparser

    name: str | None = args.name
    if name is None:
        if not _interactive(args):
            subparser.error("the following arguments are required: name")
        # _resolve reads the name off the namespace along with the choices.
        name = args.name = _ask_name(args.directory)
    elif (problem := _name_problem(name)) is not None:
        subparser.error(problem)

    destination = args.directory if args.directory is not None else Path(name)
    if (problem := _destination_problem(destination)) is not None:
        subparser.error(problem)

    options = _resolve(args)
    written = generate(options, destination)

    print(f"Created {destination}/ ({len(written)} files)\n")
    print("Next:")
    for step in _next_steps(options, destination):
        print(f"  {step}")
    if options.alembic:
        print(
            "\nThe tests run the migrations, so they need that first migration "
            "before they pass."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
