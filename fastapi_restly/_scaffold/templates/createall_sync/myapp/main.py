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


def create_app() -> FastAPI:
    """Build the application. Called by ``asgi.py`` and by the test suite."""
    settings = Settings.current
    app = FastAPI(title="myapp", lifespan=lifespan)

    # Restly builds the engine from the URL, with defaults suited to a web
    # application. Pass ``engine=`` instead when you need to size the pool
    # yourself; see the deployment guide.
    fr.configure(app, database_url=settings.database_url, health="/health")

    for view in VIEWS:
        fr.include_view(app, view)

    return app


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create the schema at startup, which is what this project has instead of
    migrations.

    Async even though the views are not: the ASGI lifespan protocol is, whatever
    the endpoints do. Creating tables suits development, but add Alembic before
    you deploy anything you care about, because ``create_all`` adds missing
    tables and never alters an existing one, so it cannot carry a schema
    forward. Nothing is needed on the way down: a synchronous engine needs no
    disposal at shutdown.
    """
    fr.db.create_all(fr.DataclassBase)
    yield
