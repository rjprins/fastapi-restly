"""
Multi-tenant SaaS example for FastAPI-Restly.

This example is a complete showcase of FastAPI-Restly customization patterns:
- Multi-tenant data model (Organization as tenant) with shared base view
- Tenant isolation, row-level, and field-level permissions
- One-to-many and many-to-many relationships across organizations,
  users, projects, tasks, and labels
- Enum fields (role, status, priority, task type)
- Custom create/update schemas with validation
- Custom endpoints alongside auto-generated CRUD
- List-params filtering, sorting, and pagination on every CRUD view

The application is built by a factory. Settings are read and the engine is
built only when ``create_app()`` runs, so the test suite can install its own
settings first. Run it with ``uvicorn app.asgi:app``.

Importing this module is therefore free, and imports every view and model
through ``VIEWS``. Alembic and anything else needing complete metadata import it
for exactly that. Keep it free: never build an app at module level here.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

import fastapi_restly as fr

from .countries.views import CountryView
from .labels.views import LabelView, TaskLabelView
from .organizations.views import OrganizationView
from .projects.views import ProjectView
from .settings import Settings
from .tasks.views import TaskView
from .uploads.views import UploadView
from .users.views import UserView

# The application's views, registered in create_app(). Listing them here is what
# makes importing this module reach every view, and through each view its models.
VIEWS = (
    OrganizationView,
    UserView,
    ProjectView,
    TaskView,
    LabelView,
    TaskLabelView,
    UploadView,
    CountryView,
)


def create_app() -> FastAPI:
    """Build the SaaS application from validated settings."""
    settings = Settings.current
    # Restly's own defaults cover only engines it builds from a URL, so a
    # caller-built engine sets pool_pre_ping itself.
    engine = create_async_engine(
        settings.sqlalchemy_database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        """Dispose the application-owned pool when the process shuts down."""
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="SaaS Example API",
        description="Multi-tenant project management API built with FastAPI-Restly",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Give Restly the application's engine. Alembic owns all schema changes.
    fr.configure(app, async_engine=engine, health="/health")

    for view in VIEWS:
        fr.include_view(app, view)

    return app
