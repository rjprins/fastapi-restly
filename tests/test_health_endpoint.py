"""Tests for the health endpoint mounted by ``fr.configure(health=...)``."""

from __future__ import annotations

import warnings

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


def _routes_at(app: FastAPI, path: str) -> list[object]:
    return [route for route in app.routes if getattr(route, "path", None) == path]


# ---------------------------------------------------------------------------
# Off by default
# ---------------------------------------------------------------------------


def test_no_health_route_by_default():
    app = FastAPI()
    before = _paths(app)

    fr.configure(app, async_database_url=ASYNC_URL)

    assert _paths(app) == before
    assert "/health" not in _paths(app)


def test_no_health_route_when_configured_without_an_app():
    app = FastAPI()

    fr.configure(async_database_url=ASYNC_URL)

    assert "/health" not in _paths(app)


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
    app = FastAPI()
    fr.configure(app, async_database_url=ASYNC_URL, health="/health")

    schema = app.openapi()

    assert "/health" in schema["paths"]
    assert "get" in schema["paths"]["/health"]


def test_health_route_is_named_health():
    app = FastAPI()
    fr.configure(app, async_database_url=ASYNC_URL, health="/health")

    (route,) = _routes_at(app, "/health")
    assert getattr(route, "name", None) == "health"

    operation_id = app.openapi()["paths"]["/health"]["get"]["operationId"]
    assert not operation_id.startswith("_")

    # Without name=, the private handler's own name reaches the operationId.
    from fastapi_restly.db._session import _health

    unnamed = FastAPI()
    unnamed.add_api_route("/health", _health, methods=["GET"])
    assert unnamed.openapi()["paths"]["/health"]["get"]["operationId"].startswith("_")


def test_health_route_is_reversible_by_name():
    app = FastAPI()
    fr.configure(app, async_database_url=ASYNC_URL, health="/_internal/live")

    assert app.url_path_for("health") == "/_internal/live"


# ---------------------------------------------------------------------------
# Liveness only: no database round-trip
# ---------------------------------------------------------------------------


def test_health_does_not_touch_the_database():
    """The engine here can never connect; the endpoint answers anyway."""
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
# Deferring to a route already at that path
# ---------------------------------------------------------------------------


def test_configuring_twice_mounts_one_route():
    app = FastAPI()
    fr.configure(app, async_database_url=ASYNC_URL, health="/health")
    fr.configure(app, async_database_url=ASYNC_URL, health="/health")

    assert len(_routes_at(app, "/health")) == 1


def test_configuring_twice_leaves_the_schema_warning_free():
    """A second route at the same path would collide on the operation ID."""
    app = FastAPI()
    fr.configure(app, async_database_url=ASYNC_URL, health="/health")
    fr.configure(app, async_database_url=ASYNC_URL, health="/health")

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        schema = app.openapi()

    assert "/health" in schema["paths"]


def test_an_existing_route_at_that_path_is_left_in_place():
    app = FastAPI()

    @app.get("/health")
    def app_health():
        return {"status": "the application's own"}

    fr.configure(app, async_database_url=ASYNC_URL, health="/health")

    assert len(_routes_at(app, "/health")) == 1
    assert TestClient(app).get("/health").json() == {"status": "the application's own"}


def test_an_existing_route_defers_whatever_its_method():
    """The check is on the path alone, so a POST-only route defers too."""
    app = FastAPI()

    @app.post("/health")
    def app_health():
        return {"status": "posted"}

    fr.configure(app, async_database_url=ASYNC_URL, health="/health")

    assert len(_routes_at(app, "/health")) == 1
    assert TestClient(app).get("/health").status_code == 405


def test_two_calls_with_different_paths_mount_both():
    app = FastAPI()
    fr.configure(app, async_database_url=ASYNC_URL, health="/health")
    fr.configure(app, async_database_url=ASYNC_URL, health="/healthz")

    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/healthz").json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["health", "healthz", "", "up"])
def test_path_without_leading_slash_raises(path):
    """Starlette's own failure is a bare AssertionError naming nothing."""
    app = FastAPI()

    with pytest.raises(RestlyConfigurationError, match="starting with"):
        fr.configure(app, async_database_url=ASYNC_URL, health=path)


def test_bad_path_does_not_partially_configure():
    app = FastAPI()

    with pytest.raises(RestlyConfigurationError):
        fr.configure(app, health="health", warn_on_misuse=True)

    from fastapi_restly.db._globals import _fr_globals

    assert _fr_globals.warn_on_misuse is False
    assert IntegrityError not in app.exception_handlers


def test_health_without_app_raises():
    with pytest.raises(RestlyConfigurationError, match="needs the app"):
        fr.configure(health="/health")


def test_health_without_app_still_raises_alongside_a_database_url():
    with pytest.raises(RestlyConfigurationError, match="needs the app"):
        fr.configure(async_database_url=ASYNC_URL, health="/health")


def test_health_is_not_deferred_to_include_view():
    """No pending route is stashed for whichever app mounts a view first."""
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
    app = FastAPI()

    fr.configure(app, health="/health")

    assert TestClient(app).get("/health").status_code == 200


def test_health_counts_even_with_exception_handlers_opted_out():
    app = FastAPI()

    fr.configure(app, health="/health", install_default_exception_handlers=False)

    assert IntegrityError not in app.exception_handlers
    assert TestClient(app).get("/health").status_code == 200


def test_health_does_not_configure_a_database():
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
