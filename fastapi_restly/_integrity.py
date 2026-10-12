"""Turn a SQLAlchemy ``IntegrityError`` into an HTTP 409 inside Restly's writes.

A unique, foreign-key, not-null or check violation raises ``IntegrityError``
at a flush or at the commit. The outermost write block (a ``write_action``
outside any ``shared_write_action_commit()``, or the outermost shared block)
turns it into :class:`~fastapi_restly.exc.Conflict`, so the client gets a 409
instead of a 500. Blocks inside a shared block let the raw error through, so a
bulk route can catch a failing row. Code outside a write block, such as a bare
``View`` route that commits itself, gets the raw error.

The detail text is best-effort: it knows the common PostgreSQL SQLSTATE codes
(``orig.sqlstate`` / ``orig.pgcode``) and the SQLite messages. For other
drivers it falls back to a general message with the start of the driver's
text.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from sqlalchemy.exc import IntegrityError

from .exc import Conflict

# Maximum length of original-error text we are willing to echo back. Keeps
# response bodies sane and avoids accidentally leaking long SQL strings.
_MAX_ORIG_TEXT_LENGTH = 500

# ---------------------------------------------------------------------------
# Detail extraction
# ---------------------------------------------------------------------------


# PostgreSQL SQLSTATE codes — see
# https://www.postgresql.org/docs/current/errcodes-appendix.html (class 23
# "Integrity Constraint Violation").
_PG_SQLSTATE_DETAILS: dict[str, str] = {
    "23505": "Unique constraint violated",
    "23503": "Foreign key constraint violated",
    "23502": "Not-null constraint violated",
    "23514": "Check constraint violated",
    "23000": "Integrity constraint violated",
    "23001": "Restrict violation",
    "23P01": "Exclusion constraint violated",
}


def _extract_postgres_detail(orig: Any) -> str | None:
    """Return a user-facing detail message for a Postgres-driver error.

    Reads the SQLSTATE code and, when psycopg's ``orig.diag`` is present, its
    ``constraint_name`` / ``column_name`` to enrich the message. The attribute
    holding the code differs by driver: psycopg 3 exposes ``sqlstate``, psycopg 2
    exposes ``pgcode`` -- read whichever is present. Reading only ``pgcode``
    silently degraded every psycopg 3 error to the generic fallback.
    """
    pgcode = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if not pgcode:
        return None

    base = _PG_SQLSTATE_DETAILS.get(pgcode)
    if base is None:
        return None

    # ``diag`` is a psycopg-specific attribute holding fielded error info.
    diag = getattr(orig, "diag", None)
    constraint_name = getattr(diag, "constraint_name", None) if diag else None
    column_name = getattr(diag, "column_name", None) if diag else None

    if pgcode == "23505" and constraint_name:
        return f"{base}: {constraint_name}"
    if pgcode == "23503" and constraint_name:
        return f"{base}: {constraint_name}"
    if pgcode == "23502" and column_name:
        return f"{base} on column {column_name!r}"
    if pgcode == "23514" and constraint_name:
        return f"{base}: {constraint_name}"
    return base


# Mapping from SQLite error-message prefixes to a clean detail message.
# SQLite's IntegrityError.args[0] (and ``str(orig)``) follow predictable
# patterns, e.g. ``"UNIQUE constraint failed: user.username"``.
_SQLITE_PREFIX_DETAILS: tuple[tuple[str, str], ...] = (
    ("UNIQUE constraint failed:", "Unique constraint violated"),
    ("FOREIGN KEY constraint failed", "Foreign key constraint violated"),
    ("NOT NULL constraint failed:", "Not-null constraint violated"),
    ("CHECK constraint failed:", "Check constraint violated"),
    ("PRIMARY KEY must be unique", "Unique constraint violated (primary key)"),
)


def _extract_sqlite_detail(orig: Any) -> str | None:
    """Return a user-facing detail message for a SQLite-driver error."""
    text = str(orig).strip()
    if not text:
        return None

    for prefix, base in _SQLITE_PREFIX_DETAILS:
        if not text.startswith(prefix):
            continue
        # Try to surface the column / constraint info that SQLite tacks on
        # after the colon. ``UNIQUE constraint failed: user.username`` →
        # ``"Unique constraint violated on user.username"``.
        remainder = text[len(prefix) :].strip().lstrip(":").strip()
        if remainder:
            return f"{base} on {remainder}"
        return base
    return None


def _build_integrity_detail(exc: IntegrityError) -> str:
    """Build a clean HTTP 409 detail message from a SQLAlchemy IntegrityError.

    Best-effort across dialects:

    * PostgreSQL — switches on the SQLSTATE code (``exc.orig.sqlstate`` /
      ``pgcode``, class 23).
    * SQLite — pattern-matches ``str(exc.orig)`` against known prefixes.
    * Anything else — returns a generic fallback that includes a truncated
      copy of the original error text so the body is still useful for
      debugging without being huge.
    """
    orig = getattr(exc, "orig", None)
    if orig is not None:
        pg_detail = _extract_postgres_detail(orig)
        if pg_detail is not None:
            return pg_detail

        sqlite_detail = _extract_sqlite_detail(orig)
        if sqlite_detail is not None:
            return sqlite_detail

    # Generic fallback. Prefer the original driver error text (it's usually
    # the most informative); truncate so we don't dump a giant SQL statement.
    raw = str(orig) if orig is not None else str(exc)
    raw = raw.strip()
    if len(raw) > _MAX_ORIG_TEXT_LENGTH:
        raw = raw[:_MAX_ORIG_TEXT_LENGTH] + "...(truncated)"

    base = "Conflict with current state of the resource"
    if raw:
        return f"{base}: {raw}"
    return base


@contextlib.contextmanager
def translate_integrity_errors(outermost: bool) -> Iterator[None]:
    """Raise :class:`~fastapi_restly.exc.Conflict` for an ``IntegrityError``.

    Only when ``outermost`` is true: a block inside a shared commit lets the
    raw error through, so the code around it can still catch it.
    """
    if not outermost:
        yield
        return
    try:
        yield
    except IntegrityError as exc:
        raise Conflict(_build_integrity_detail(exc)) from exc
