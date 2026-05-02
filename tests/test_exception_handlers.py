"""Tests for fastapi-restly's default exception handlers.

These tests cover the registration helper itself and the
dialect-classification logic of the IntegrityError handler. The
end-to-end "real SQLite IntegrityError → HTTP 409" path is covered by
``tests/test_errors_integrity.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly._exceptions import (
    _HANDLERS_INSTALLED_FLAG,
    _build_integrity_detail,
    integrity_error_handler,
    register_default_exception_handlers,
)

from .conftest import create_tables

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_integrity_error(orig: Any) -> IntegrityError:
    """Construct a synthetic IntegrityError wrapping ``orig`` (no real DB)."""
    return IntegrityError("INSERT INTO foo VALUES (1)", {}, orig)


# ---------------------------------------------------------------------------
# Registration: idempotent / opt-out / user precedence
# ---------------------------------------------------------------------------


def test_register_is_idempotent():
    """Calling the helper twice on the same app installs only once."""
    app = FastAPI()
    register_default_exception_handlers(app)
    handler_first = app.exception_handlers[IntegrityError]

    register_default_exception_handlers(app)
    handler_second = app.exception_handlers[IntegrityError]

    # Same handler object — we did not re-register.
    assert handler_first is handler_second
    assert getattr(app.state, _HANDLERS_INSTALLED_FLAG) is True


def test_configure_twice_registers_handler_once():
    """``fr.configure`` is also idempotent w.r.t. handler registration."""
    app = FastAPI()
    fr.configure(app, async_database_url="sqlite+aiosqlite:///:memory:")
    fr.configure(app, async_database_url="sqlite+aiosqlite:///:memory:")
    # Handler is present and the flag is set exactly once.
    assert IntegrityError in app.exception_handlers
    assert getattr(app.state, _HANDLERS_INSTALLED_FLAG) is True


def test_user_handler_takes_precedence():
    """A pre-registered IntegrityError handler is respected; we do not
    overwrite it."""
    app = FastAPI()

    async def custom_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=418, content={"detail": "I am a teapot"})

    app.add_exception_handler(IntegrityError, custom_handler)

    register_default_exception_handlers(app)

    # The user's handler is still wired up.
    assert app.exception_handlers[IntegrityError] is custom_handler

    # And it actually runs end-to-end.
    @app.get("/boom")
    def boom():
        raise _make_integrity_error(Exception("synthetic"))

    client = TestClient(app)
    response = client.get("/boom")
    assert response.status_code == 418
    assert response.json() == {"detail": "I am a teapot"}


def test_opt_out_via_configure_flag():
    """``install_default_exception_handlers=False`` skips registration; an
    IntegrityError then bubbles to FastAPI's default 500 handler."""
    app = FastAPI()
    fr.configure(
        app,
        async_database_url="sqlite+aiosqlite:///:memory:",
        install_default_exception_handlers=False,
    )
    assert IntegrityError not in app.exception_handlers

    @app.get("/boom")
    def boom():
        raise _make_integrity_error(Exception("synthetic"))

    # ``raise_server_exceptions=False`` makes the test client return the
    # 500 response instead of re-raising the exception.
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom")
    assert response.status_code == 500


def test_include_view_registers_handler_as_fallback(client):
    """If the user calls ``include_view`` without first calling
    ``fr.configure(app=...)``, mounting the view still installs the
    handler. (The autouse fixture configures without an ``app``.)"""
    assert IntegrityError not in client.app.exception_handlers

    class Widget(fr.IDBase):
        name: Mapped[str] = mapped_column(unique=True)

    class WidgetSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class WidgetView(fr.AsyncRestView):
        prefix = "/widgets"
        model = Widget
        schema = WidgetSchema

    create_tables()

    assert IntegrityError in client.app.exception_handlers
    assert getattr(client.app.state, _HANDLERS_INSTALLED_FLAG) is True


# ---------------------------------------------------------------------------
# Detail-string classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pgcode, expected_substring",
    [
        ("23505", "unique"),
        ("23503", "foreign key"),
        ("23502", "not-null"),
        ("23514", "check"),
    ],
)
def test_postgres_pgcode_classification(pgcode, expected_substring):
    """Postgres SQLSTATE codes drive the human-readable detail."""
    orig = SimpleNamespace(
        pgcode=pgcode,
        diag=SimpleNamespace(constraint_name=None, column_name=None),
    )
    exc = _make_integrity_error(orig)
    detail = _build_integrity_detail(exc)
    assert expected_substring in detail.lower()


def test_postgres_pgcode_includes_constraint_name():
    """When psycopg's ``orig.diag`` carries a constraint name, we surface it."""
    orig = SimpleNamespace(
        pgcode="23505",
        diag=SimpleNamespace(
            constraint_name="uq_user_email", column_name=None
        ),
    )
    exc = _make_integrity_error(orig)
    detail = _build_integrity_detail(exc)
    assert "uq_user_email" in detail
    assert "unique" in detail.lower()


def test_postgres_pgcode_not_null_includes_column():
    orig = SimpleNamespace(
        pgcode="23502",
        diag=SimpleNamespace(constraint_name=None, column_name="email"),
    )
    detail = _build_integrity_detail(_make_integrity_error(orig))
    assert "not-null" in detail.lower()
    assert "email" in detail


def test_sqlite_unique_message_classification():
    """A real SQLite-style error message is parsed and surfaces the column."""
    orig = Exception("UNIQUE constraint failed: user.username")
    detail = _build_integrity_detail(_make_integrity_error(orig))
    assert "unique" in detail.lower()
    assert "user.username" in detail


def test_sqlite_foreign_key_message_classification():
    orig = Exception("FOREIGN KEY constraint failed")
    detail = _build_integrity_detail(_make_integrity_error(orig))
    assert "foreign key" in detail.lower()


def test_sqlite_not_null_message_classification():
    orig = Exception("NOT NULL constraint failed: user.email")
    detail = _build_integrity_detail(_make_integrity_error(orig))
    assert "not-null" in detail.lower()
    assert "user.email" in detail


def test_sqlite_check_message_classification():
    orig = Exception("CHECK constraint failed: positive_quantity")
    detail = _build_integrity_detail(_make_integrity_error(orig))
    assert "check" in detail.lower()


def test_fallback_for_unknown_dialect():
    """Unrecognised ``orig`` (no pgcode, unknown message) falls back to
    the generic conflict message and includes the truncated original."""
    orig = Exception("some weird custom database error nobody has seen before")
    detail = _build_integrity_detail(_make_integrity_error(orig))
    assert "conflict" in detail.lower()
    assert "weird custom database error" in detail


def test_fallback_truncates_long_orig_text():
    long_text = "x" * 2000
    orig = Exception(long_text)
    detail = _build_integrity_detail(_make_integrity_error(orig))
    assert "truncated" in detail
    # We cap at 500 chars + the "...(truncated)" suffix; the resulting
    # detail must therefore be much shorter than the raw 2000 chars.
    assert len(detail) < 700


# ---------------------------------------------------------------------------
# integrity_error_handler returns the right response shape
# ---------------------------------------------------------------------------


def test_handler_response_is_json_409():
    """Calling the handler directly returns a JSONResponse with status 409
    and a ``detail`` key."""
    app = FastAPI()
    register_default_exception_handlers(app)

    @app.get("/boom")
    def boom():
        raise _make_integrity_error(
            Exception("UNIQUE constraint failed: foo.bar")
        )

    client = TestClient(app)
    response = client.get("/boom")
    assert response.status_code == 409
    body = response.json()
    assert "detail" in body
    assert "unique" in body["detail"].lower()
    assert "foo.bar" in body["detail"]


def test_handler_callable_signature():
    """The handler module-level callable has a Starlette-compatible
    signature so it can be registered directly."""
    app = FastAPI()
    app.add_exception_handler(IntegrityError, integrity_error_handler)
    assert app.exception_handlers[IntegrityError] is integrity_error_handler
