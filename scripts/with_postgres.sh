#!/usr/bin/env bash
# Run a command with RESTLY_TEST_DATABASE_URL pointing at a PostgreSQL server.
#
# Honours an already-set URL; otherwise starts a throwaway container on the same
# image CI uses and removes it afterwards. Without this the dialect leg skips
# itself on a developer machine and only breaks in CI.
set -euo pipefail

if [ -n "${RESTLY_TEST_DATABASE_URL:-}" ]; then
    exec "$@"
fi

if command -v docker >/dev/null 2>&1; then
    engine=docker
elif command -v podman >/dev/null 2>&1; then
    engine=podman
else
    echo "No docker or podman found. Install one, or set RESTLY_TEST_DATABASE_URL" >&2
    echo "to a PostgreSQL server yourself (see tests/postgres/conftest.py)." >&2
    exit 1
fi

image=postgres:17
name="restly-test-pg-$$"

cleanup() { "$engine" rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "Starting throwaway $image container via $engine..."
"$engine" run -d --name "$name" \
    -e POSTGRES_USER=postgres \
    -e POSTGRES_PASSWORD=postgres \
    -e POSTGRES_DB=restly_test \
    -p 127.0.0.1::5432 \
    "$image" >/dev/null

# An ephemeral host port, so a PostgreSQL already on 5432 is left alone.
port=$("$engine" port "$name" 5432/tcp | head -1 | sed 's/.*://')

# Probe over TCP, not the socket: the image runs a socket-only server during
# initdb, which would answer before the real one is listening.
ready=""
for _ in $(seq 1 60); do
    if "$engine" exec "$name" pg_isready -h 127.0.0.1 -U postgres -d restly_test \
        >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
if [ -z "$ready" ]; then
    echo "Container $name never accepted connections." >&2
    "$engine" logs "$name" >&2 || true
    exit 1
fi

export RESTLY_TEST_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:$port/restly_test"
"$@"
