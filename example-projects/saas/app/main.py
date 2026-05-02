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
- V1 (JSONAPI-style) and V2 (standard HTTP) query modifiers
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

import fastapi_restly as fr

from .views import (
    LabelView,
    OrganizationView,
    ProjectView,
    TaskLabelView,
    TaskView,
    UserView,
)

# Set up database connection
fr.configure(async_database_url="sqlite+aiosqlite:///saas.db")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create database tables on startup."""
    engine = fr.get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(fr.DataclassBase.metadata.create_all)
    yield


# Create FastAPI app
app = FastAPI(
    title="SaaS Example API",
    description="Multi-tenant project management API built with FastAPI-Restly",
    version="0.1.0",
    lifespan=lifespan,
)

# Register views
fr.include_view(app, OrganizationView)
fr.include_view(app, UserView)
fr.include_view(app, ProjectView)
fr.include_view(app, TaskView)
fr.include_view(app, LabelView)
fr.include_view(app, TaskLabelView)


# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}
