"""The application factory.

This module is the one every tool imports. It reaches ``VIEWS``, each view
imports its model, so importing ``myapp.main`` is what gives the test suite,
and any schema tool you add later, the complete set of models.

Importing it must stay free of side effects: it defines ``create_app()`` and
builds nothing. Never put ``app = create_app()`` at the bottom here, or
importing the module would require a configured environment and the test suite
is the first thing to break. The application object lives in ``asgi.py``.
"""

from contextlib import asynccontextmanager

import fastapi_restly as fr
from fastapi import FastAPI

from .settings import Settings
from .tasks.views import TaskView

# Every view the application serves. Add yours here; each one pulls in its own
# model, which is how this module ends up seeing the whole schema.
VIEWS = (TaskView,)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create the schema on the way up, close the pool on the way down.

    Creating tables at startup suits development, and is what this project has
    instead of migrations. Add Alembic before you deploy anything you care
    about: ``create_all`` adds missing tables and never alters an existing one,
    so it cannot carry a schema forward.

    Disposing is async only. An async engine that is never disposed drops its
    connections instead of closing them, which leaves a ResourceWarning per
    connection and, if a request is still in flight, an "Event loop is closed"
    traceback. A synchronous engine needs none of that.
    """
    await fr.db.async_create_all(fr.DataclassBase)
    yield
    await fr.db.get_async_engine().dispose()


def create_app() -> FastAPI:
    """Build the application. Called by ``asgi.py`` and by the test suite."""
    settings = Settings.current
    app = FastAPI(title="myapp", lifespan=lifespan)

    # Restly builds the engine from the URL, with defaults suited to a web
    # application. Pass ``async_engine=`` instead when you need to size the pool
    # yourself; see the deployment guide.
    fr.configure(app, async_database_url=settings.database_url, health="/health")

    for view in VIEWS:
        fr.include_view(app, view)

    return app
