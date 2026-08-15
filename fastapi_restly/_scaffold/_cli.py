"""The ``restly`` command."""

from __future__ import annotations

import argparse
import keyword
import sys
from pathlib import Path

from ._generate import Options, generate

_DESCRIPTION = "Create a new FastAPI-Restly project."


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="restly", description=_DESCRIPTION)
    subcommands = parser.add_subparsers(dest="command", required=True)

    new = subcommands.add_parser("new", help=_DESCRIPTION)
    new.add_argument("name", help="Project and package name, e.g. myapp")
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
        help="Build the test schema from the models instead of migrations",
    )

    new.add_argument(
        "--yes", "-y", action="store_true", help="Take the defaults without prompting"
    )
    return parser


def _ask(question: str, default_yes: bool, first: str, second: str) -> bool:
    """Prompt for one either/or choice. Anything unrecognised keeps the default."""
    shown = f"{first}/{second}" if default_yes else f"{second}/{first}"
    answer = input(f"{question} [{shown}]: ").strip().lower()
    if not answer:
        return default_yes
    if first.lower().startswith(answer):
        return True
    if second.lower().startswith(answer):
        return False
    return default_yes


def _resolve(args: argparse.Namespace) -> Options:
    """Fill unanswered choices, prompting only on a terminal."""
    is_async, postgres, alembic = args.is_async, args.postgres, args.alembic
    unanswered = [value is None for value in (is_async, postgres, alembic)]

    if any(unanswered) and not args.yes and sys.stdin.isatty():
        if is_async is None:
            is_async = _ask("Async views?", True, "async", "sync")
        if postgres is None:
            postgres = _ask("Database?", True, "postgres", "sqlite")
        if alembic is None:
            alembic = _ask("Migrations?", True, "alembic", "create-all")

    return Options(
        name=args.name,
        is_async=True if is_async is None else is_async,
        postgres=True if postgres is None else postgres,
        alembic=True if alembic is None else alembic,
    )


def _next_steps(options: Options) -> list[str]:
    steps = [f"cd {options.name}", "uv sync"]
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

    name = args.name
    if not name.isidentifier() or keyword.iskeyword(name):
        parser.error(
            f"{name!r} is not usable as a Python package name. Use letters, "
            "digits and underscores, starting with a letter."
        )

    destination = args.directory if args.directory is not None else Path(name)
    if destination.exists() and any(destination.iterdir()):
        parser.error(f"{str(destination)!r} already exists and is not empty.")

    options = _resolve(args)
    written = generate(options, destination)

    print(f"Created {destination}/ ({len(written)} files)\n")
    print("Next:")
    for step in _next_steps(options):
        print(f"  {step}")
    if options.alembic:
        print(
            "\nThe tests run the migrations, so they need that first migration "
            "before they pass."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
