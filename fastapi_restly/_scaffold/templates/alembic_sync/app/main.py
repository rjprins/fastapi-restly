"""The application factory.

This module is the one every tool imports. It reaches ``VIEWS``, each view
imports its model, so importing ``app.main`` is what gives Alembic and the
test suite the complete schema.

Importing it must stay free of side effects: it defines ``create_app()`` and
builds nothing. Never put ``app = create_app()`` at the bottom here, or
importing the module would require a configured environment and the test suite
is the first thing to break. The application object lives in ``asgi.py``.
"""

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
    # No lifespan: a synchronous engine needs no disposal at shutdown.
    app = FastAPI(title="myapp")

    # Restly builds the engine from the URL, with defaults suited to a web
    # application. Pass ``engine=`` instead when you need to size the pool
    # yourself; see the deployment guide.
    fr.configure(app, database_url=settings.database_url, health="/health")

    for view in VIEWS:
        fr.include_view(app, view)

    return app
