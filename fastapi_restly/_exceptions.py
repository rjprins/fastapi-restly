"""Default FastAPI exception handlers installed by fastapi-restly.

Currently this module provides a translation layer from SQLAlchemy
:class:`~sqlalchemy.exc.IntegrityError` (unique-constraint, foreign-key,
not-null, and check-constraint violations) into a clean HTTP 409 Conflict
response. Without this handler, an ``IntegrityError`` bubbles up to FastAPI
and turns into a 500 Internal Server Error, which is misleading for clients
(the server is fine; the request conflicts with the current state of the
resource).

The handler is installed automatically by :func:`fastapi_restly.configure`
and as a fallback by :func:`fastapi_restly.include_view`. Users can opt out
by calling ``fr.configure(install_default_exception_handlers=False)`` or by
registering their own handler for ``IntegrityError`` *before* the framework
gets a chance to install one.

The detail-message extraction is best-effort: it understands the most common
PostgreSQL SQLSTATE codes (via psycopg's ``orig.pgcode``) and the SQLite
error-message conventions. For unrecognised dialects/messages we fall back
to a generic conflict message that includes a truncated version of the
underlying error text.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

# Maximum length of original-error text we are willing to echo back. Keeps
# response bodies sane and avoids accidentally leaking long SQL strings.
_MAX_ORIG_TEXT_LENGTH = 500

# Marker stored on ``app.state`` so we know we've already installed our
# handlers on this FastAPI instance. Public so it is easy to inspect from
# tests or user code.
_HANDLERS_INSTALLED_FLAG = "_fr_default_exception_handlers_installed"


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

    Looks at ``orig.pgcode`` (set by psycopg / psycopg2 / asyncpg-via-psycopg)
    and, when available, ``orig.diag.constraint_name`` /
    ``orig.diag.column_name`` to enrich the message.
    """
    pgcode = getattr(orig, "pgcode", None)
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
        remainder = text[len(prefix):].strip().lstrip(":").strip()
        if remainder:
            return f"{base} on {remainder}"
        return base
    return None


def _build_integrity_detail(exc: IntegrityError) -> str:
    """Build a clean HTTP 409 detail message from a SQLAlchemy IntegrityError.

    Best-effort across dialects:

    * PostgreSQL — switches on ``exc.orig.pgcode`` (SQLSTATE class 23).
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


# ---------------------------------------------------------------------------
# The handler & registration helper
# ---------------------------------------------------------------------------


def integrity_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Translate a SQLAlchemy IntegrityError into HTTP 409 Conflict.

    Signature uses ``Exception`` rather than ``IntegrityError`` to satisfy
    Starlette's exception-handler typing; we narrow at runtime.
    """
    assert isinstance(exc, IntegrityError)  # noqa: S101 - registered for IntegrityError only
    detail = _build_integrity_detail(exc)
    return JSONResponse(status_code=409, content={"detail": detail})


def register_default_exception_handlers(app: FastAPI) -> None:
    """Idempotently install fastapi-restly default exception handlers on ``app``.

    * Skips if a handler for :class:`IntegrityError` is already registered on
      ``app`` — we always defer to the user.
    * Skips if we have already installed handlers on this ``app`` instance
      (so calling from both :func:`fastapi_restly.configure` and
      :func:`fastapi_restly.include_view` is safe).
    """
    if getattr(app.state, _HANDLERS_INSTALLED_FLAG, False):
        return

    # Respect a user-registered handler if one is already in place.
    if IntegrityError in app.exception_handlers:
        setattr(app.state, _HANDLERS_INSTALLED_FLAG, True)
        return

    app.add_exception_handler(IntegrityError, integrity_error_handler)
    setattr(app.state, _HANDLERS_INSTALLED_FLAG, True)


__all__ = [
    "integrity_error_handler",
    "register_default_exception_handlers",
]
