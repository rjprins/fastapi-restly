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

The application is built by a factory. Database setup runs only when
``create_app()`` is called, so the test suite can build the app against its
own database. Run it with ``uvicorn --factory app.main:create_app``.

Importing this module is therefore free, and imports every view and model
through ``api``. Alembic and anything else needing complete metadata import it
for exactly that. Keep it free: never build an app at module level here.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

import fastapi_restly as fr

from .api import register_views
from .database import create_engine_from
from .settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the SaaS application from validated settings."""
    settings = settings or Settings()  # type: ignore[call-arg]
    engine = create_engine_from(settings)

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
    fr.configure(app, async_engine=engine)
    # Exposed so tests can assert engine identity; not part of the documented pattern.
    app.state.engine = engine

    register_views(app)

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "ok"}

    return app
