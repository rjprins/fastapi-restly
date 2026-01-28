"""
Multi-tenant SaaS example for FastAPI-Restly.

This example demonstrates:
- Multi-tenant data model (Organization as tenant)
- One-to-many relationships (Org→Users, Org→Projects, Project→Tasks)
- Foreign key references in schemas
- Enum fields (role, status, priority)
- Filtering, sorting, and pagination
"""

from fastapi import FastAPI

import fastapi_restly as fr

from .models import Organization, User, Project, Task, Label, TaskLabel
from .views import OrganizationView, UserView, ProjectView, TaskView, LabelView, TaskLabelView

# Set up database connection
fr.setup_async_database_connection(
    async_database_url="sqlite+aiosqlite:///saas.db"
)

# Create FastAPI app
app = FastAPI(
    title="SaaS Example API",
    description="Multi-tenant project management API built with FastAPI-Restly",
    version="0.1.0",
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
