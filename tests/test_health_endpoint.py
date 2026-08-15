"""Tests for the optional health endpoint mounted by ``fr.configure(health=...)``.

The endpoint is a liveness probe: it reports that the process is up and
answering, and deliberately makes no database round-trip. It is off unless the
application names a path, which is also how Rails and Laravel ship theirs (the
generated project writes the route, the framework does not inject it).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError

from .conftest import create_tables

ASYNC_URL = "sqlite+aiosqlite:///:memory:"


def _paths(app: FastAPI) -> set[str]:
    return {
        path
        for path in (getattr(route, "path", None) for route in app.routes)
        if path is not None
    }


# ---------------------------------------------------------------------------
# Off by default
# ---------------------------------------------------------------------------


def test_no_health_route_by_default():
    """Omitting ``health`` adds no route: the framework never injects one."""
    app = FastAPI()
    before = _paths(app)

    fr.configure(app, async_database_url=ASYNC_URL)

    assert _paths(app) == before
    assert "/health" not in _paths(app)


def test_no_health_route_when_configured_without_an_app():
    """The database-only call that most test suites make adds nothing either."""
    app = FastAPI()

    fr.configure(async_database_url=ASYNC_URL)

    assert "/health" not in _paths(app)


def test_default_off_leaves_an_existing_health_route_alone():
    """Why the default is off: ``configure()`` normally runs before the
    application adds its own routes, and Starlette matches in registration
    order, so a default-on route would shadow the application's own."""
    app = FastAPI()
    fr.configure(app, async_database_url=ASYNC_URL)

    @app.get("/health")
    def app_health():
        return {"status": "application's own"}

    client = TestClient(app)
    assert client.get("/health").json() == {"status": "application's own"}


# ---------------------------------------------------------------------------
# Mounting and responding
# ---------------------------------------------------------------------------


def test_health_route_answers_200_ok():
    app = FastAPI()
    fr.configure(app, async_database_url=ASYNC_URL, health="/health")

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/health", "/up", "/healthz", "/_internal/live"])
def test_health_mounts_at_the_given_path(path):
    """The path is the configurable axis: Kubernetes ``httpGet`` probes read
    the status code and never the body, so the payload stays fixed."""
    app = FastAPI()
    fr.configure(app, async_database_url=ASYNC_URL, health=path)

    client = TestClient(app)
    assert client.get(path).json() == {"status": "ok"}
    assert client.get("/health" if path != "/health" else "/up").status_code == 404


def test_health_answers_get_only():
    app = FastAPI()
    fr.configure(app, async_database_url=ASYNC_URL, health="/health")

    client = TestClient(app)
    assert client.post("/health").status_code == 405
    assert client.delete("/health").status_code == 405


# ---------------------------------------------------------------------------
# OpenAPI surface
# ---------------------------------------------------------------------------


def test_health_path_is_in_the_openapi_schema():
    """Visible on purpose: you opt in by naming a path, so hiding the route
    would make ``/docs`` a misleading way to check that it worked."""
    app = FastAPI()
    fr.configure(app, async_database_url=ASYNC_URL, health="/health")

    schema = app.openapi()

    assert "/health" in schema["paths"]
    assert "get" in schema["paths"]["/health"]


def test_health_route_is_named_health():
    """``name="health"`` keeps the private handler out of the generated
    operationId, which client generators turn into a method name."""
    app = FastAPI()
    fr.configure(app, async_database_url=ASYNC_URL, health="/health")

    (route,) = [r for r in app.routes if getattr(r, "path", None) == "/health"]
    assert getattr(route, "name", None) == "health"

    operation_id = app.openapi()["paths"]["/health"]["get"]["operationId"]
    assert not operation_id.startswith("_")

    # Contrast: without name=, FastAPI falls back to the handler's own name and
    # the leading underscore reaches the generated client.
    from fastapi_restly.db._session import _health

    unnamed = FastAPI()
    unnamed.add_api_route("/health", _health, methods=["GET"])
    assert unnamed.openapi()["paths"]["/health"]["get"]["operationId"].startswith("_")


def test_health_route_is_reversible_by_name():
    """A named route can be reversed with ``url_path_for``."""
    app = FastAPI()
    fr.configure(app, async_database_url=ASYNC_URL, health="/_internal/live")

    assert app.url_path_for("health") == "/_internal/live"


# ---------------------------------------------------------------------------
# Liveness only: no database round-trip
# ---------------------------------------------------------------------------


def test_health_does_not_touch_the_database():
    """A liveness probe that fails because a dependency is down restarts a
    process that was working. The engine here can never connect; the endpoint
    answers anyway because it never asks it to."""
    app = FastAPI()
    fr.configure(
        app,
        async_database_url="sqlite+aiosqlite:////nonexistent-dir/unreachable.sqlite3",
        health="/health",
    )

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_works_with_no_database_configured_at_all():
    app = FastAPI()
    fr.configure(app, health="/health")

    assert TestClient(app).get("/health").json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["health", "healthz", "", "up"])
def test_path_without_leading_slash_raises(path):
    """Starlette's own failure is a bare AssertionError from ``Route.__init__``
    that never names the caller."""
    app = FastAPI()

    with pytest.raises(RestlyConfigurationError, match="starting with"):
        fr.configure(app, async_database_url=ASYNC_URL, health=path)


def test_bad_path_does_not_partially_configure():
    """Validation runs before anything is applied."""
    app = FastAPI()

    with pytest.raises(RestlyConfigurationError):
        fr.configure(app, health="health", warn_on_misuse=True)

    from fastapi_restly.db._globals import _fr_globals

    assert _fr_globals.warn_on_misuse is False
    assert IntegrityError not in app.exception_handlers


def test_health_without_app_raises():
    """It does not silently no-op, and unlike the default exception handlers it
    is not deferred to the first ``include_view()``: a route is visible surface
    and must not land on whichever app happens to mount a view first."""
    with pytest.raises(RestlyConfigurationError, match="needs the app"):
        fr.configure(health="/health")


def test_health_without_app_still_raises_alongside_a_database_url():
    with pytest.raises(RestlyConfigurationError, match="needs the app"):
        fr.configure(async_database_url=ASYNC_URL, health="/health")


def test_health_is_not_deferred_to_include_view():
    """Follow-up on the above: no pending route is stashed for a later app."""
    app = FastAPI()

    with pytest.raises(RestlyConfigurationError):
        fr.configure(health="/health")

    fr.configure(async_database_url=ASYNC_URL)

    class Widget(fr.IDBase):
        name: Mapped[str] = mapped_column()

    class WidgetSchema(fr.IDSchema):
        name: str

    @fr.include_view(app)
    class WidgetView(fr.AsyncRestView):
        prefix = "/widgets"
        model = Widget
        schema = WidgetSchema

    assert "/health" not in _paths(app)


# ---------------------------------------------------------------------------
# Integration with the rest of configure()
# ---------------------------------------------------------------------------


def test_health_alone_satisfies_the_at_least_one_argument_check():
    """``health`` is a setup argument, so it does not need a companion."""
    app = FastAPI()

    fr.configure(app, health="/health")

    assert TestClient(app).get("/health").status_code == 200


def test_health_counts_even_with_exception_handlers_opted_out():
    app = FastAPI()

    fr.configure(app, health="/health", install_default_exception_handlers=False)

    assert IntegrityError not in app.exception_handlers
    assert TestClient(app).get("/health").status_code == 200


def test_health_does_not_configure_a_database():
    """Mounting the route must not stand in for database configuration."""
    from fastapi_restly.db._globals import _fr_globals

    _fr_globals.async_make_session = None
    app = FastAPI()

    fr.configure(app, health="/health")

    assert _fr_globals.async_make_session is None


def test_health_composes_with_a_full_database_configuration(sync_db):
    """The usual production shape: database, exception handlers, views, health."""
    engine, _ = sync_db
    app = FastAPI()
    fr.configure(app, async_database_url=ASYNC_URL, health="/health")

    class Gadget(fr.IDBase):
        name: Mapped[str] = mapped_column(unique=True)

    class GadgetSchema(fr.IDSchema):
        name: str

    @fr.include_view(app)
    class GadgetView(fr.AsyncRestView):
        prefix = "/gadgets"
        model = Gadget
        schema = GadgetSchema

    create_tables()

    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.post("/gadgets", json={"name": "widget"}).status_code == 201
    assert client.get("/health").json() == {"status": "ok"}

    schema = app.openapi()
    assert "/health" in schema["paths"]
    assert "/gadgets" in schema["paths"]
    assert IntegrityError in app.exception_handlers
