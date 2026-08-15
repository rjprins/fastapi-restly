#!/usr/bin/env bash
# Generate every `restly new` combination and run the documented flow against it.
#
# Templates are not lintable as a tree (no overlay is a complete package), so the
# generated project is the gate. Running the real flow -- including the first
# `alembic revision --autogenerate` -- is what proves env.py reaches the models
# and that autogenerate emits a migration the test suite can build a schema from.
#
# Selection:
#   ./scripts/scaffold_matrix.sh              every combination
#   SCAFFOLD_ONLY=sqlite ...                  the four SQLite combinations
#   SCAFFOLD_ONLY=postgres ...                the four PostgreSQL combinations
#
# PostgreSQL combinations need a server. Set RESTLY_TEST_DATABASE_URL, or run
# under scripts/with_postgres.sh, which starts a throwaway container.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

SCAFFOLD_ONLY=${SCAFFOLD_ONLY:-all}
failures=0
ran=0

# Every PostgreSQL combination shares one database, so each has to start from an
# empty schema. Without this the second Alembic combination finds an
# alembic_version row naming the first one's revision, which its own versions/
# directory does not contain. A real project owns its database and never sees
# this; it is purely an artifact of reusing one server across combinations.
reset_postgres_schema() {
    uv run --with "psycopg[binary]" python -c "
import re
import sys

import psycopg

url = re.sub(r'^postgresql\+\w+', 'postgresql', sys.argv[1])
database = psycopg.conninfo.conninfo_to_dict(url).get('dbname', '')

# This drops every table. RESTLY_TEST_DATABASE_URL is developer-settable, so
# refuse anything that is not visibly a throwaway rather than trusting it.
if 'test' not in database:
    raise SystemExit(
        f'Refusing to reset {database!r}: the scaffold matrix drops every table, '
        'so it only runs against a database with \"test\" in its name. Point '
        'RESTLY_TEST_DATABASE_URL at a throwaway database.'
    )

with psycopg.connect(url, autocommit=True) as connection:
    connection.execute('DROP SCHEMA public CASCADE')
    connection.execute('CREATE SCHEMA public')
" "$1"
}

# Rewrite the driver in a PostgreSQL URL, leaving credentials and database alone.
pg_url_with_driver() {
    python3 -c "
import sys
from urllib.parse import urlsplit, urlunsplit
raw, driver = sys.argv[1], sys.argv[2]
parts = urlsplit(raw)
print(urlunsplit((driver, parts.netloc, parts.path, parts.query, parts.fragment)))
" "$1" "$2"
}

run_one() {
    local is_async=$1 database=$2 migrations=$3
    local label="${is_async}+${database}+${migrations}"
    local dir="$WORK/${is_async}_${database}_${migrations}"

    if [ "$database" = postgres ] && [ "$SCAFFOLD_ONLY" = sqlite ]; then return 0; fi
    if [ "$database" = sqlite ] && [ "$SCAFFOLD_ONLY" = postgres ]; then return 0; fi

    echo "=============================================================="
    echo "=== $label"
    echo "=============================================================="
    ran=$((ran + 1))

    mkdir -p "$dir"

    # Not `( ... ) || { ... }`: a subshell used as the left operand of || is a
    # tested context, which suppresses its own `set -e`. Every check before the
    # last one would then be reported and ignored, and only pytest would decide.
    # Capture the status instead.
    set +e
    (
        set -e
        cd "$REPO"
        uv run python -c "
from pathlib import Path
from fastapi_restly._scaffold._generate import Options, generate

options = Options(
    name='demoapp',
    is_async=$( [ "$is_async" = async ] && echo True || echo False ),
    postgres=$( [ "$database" = postgres ] && echo True || echo False ),
    alembic=$( [ "$migrations" = alembic ] && echo True || echo False ),
)
destination = Path('$dir/demoapp')
generate(options, destination)

# Deliberately not the placeholder name: generating as 'myapp' would hide any
# occurrence the rename fails to reach, which is how a stale compose database
# name once survived.
survivors = [
    str(path)
    for path in destination.rglob('*')
    if 'myapp' in path.name
    or (path.is_file() and 'myapp' in path.read_text('utf-8'))
]
if survivors:
    raise SystemExit('placeholder survived renaming in: ' + ', '.join(survivors))

# Install the framework from this checkout rather than PyPI. Deliberately not
# editable: an editable install is a path hook pyright cannot always follow, and
# a real user gets a normal one anyway.
pyproject = destination / 'pyproject.toml'
pyproject.write_text(
    pyproject.read_text()
    + '\n[tool.uv.sources]\nfastapi-restly = { path = \"$REPO\" }\n'
)
"
        cd "$dir/demoapp"

        if [ "$database" = postgres ]; then
            if [ -z "${RESTLY_TEST_DATABASE_URL:-}" ]; then
                echo "!!! $label needs RESTLY_TEST_DATABASE_URL (or scripts/with_postgres.sh)"
                exit 1
            fi
            if [ "$is_async" = async ]; then
                driver=postgresql+asyncpg
            else
                driver=postgresql+psycopg
            fi
            url=$(pg_url_with_driver "$RESTLY_TEST_DATABASE_URL" "$driver")
            ( cd "$REPO" && reset_postgres_schema "$RESTLY_TEST_DATABASE_URL" )
            printf 'DATABASE_URL=%s\n' "$url" > .env
            export TEST_DATABASE_URL="$url"
        else
            cp .env.example .env
        fi

        uv sync --quiet

        if [ "$migrations" = alembic ]; then
            # The scaffold ships no migration on purpose, so the documented
            # first step is to make one.
            uv run alembic revision --autogenerate -m "initial" >/dev/null
            uv run alembic upgrade head >/dev/null
        fi

        uv run ruff check .
        uv run ruff format --check .
        uv run pyright
        uv run pytest -q
    )
    local status=$?
    set -e

    if [ "$status" -ne 0 ]; then
        echo "!!! FAILED: $label"
        failures=$((failures + 1))
    fi
}

for is_async in async sync; do
    for database in postgres sqlite; do
        for migrations in alembic createall; do
            run_one "$is_async" "$database" "$migrations"
        done
    done
done

echo
if [ "$failures" -ne 0 ]; then
    echo "=== scaffold matrix: $failures of $ran combinations FAILED ==="
    exit 1
fi
echo "=== scaffold matrix: $ran combinations passed ==="
