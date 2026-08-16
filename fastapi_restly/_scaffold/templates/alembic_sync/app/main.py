"""Application factory."""

import fastapi_restly as fr
from fastapi import FastAPI

from .settings import Settings
from .users.views import UserView

VIEWS = (UserView,)


def create_app() -> FastAPI:
    settings = Settings.current
    app = FastAPI(title="myapp")
    fr.configure(app, database_url=settings.database_url, health="/health")

    for view in VIEWS:
        fr.include_view(app, view)

    return app
