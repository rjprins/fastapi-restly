"""Application factory."""

import fastapi_restly as fr
from fastapi import FastAPI

from .settings import Settings, get_settings
from .users.views import UserView

VIEWS = (UserView,)


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()
    app = FastAPI(title="myapp")
    fr.configure(app, database_url=settings.database_url, health="/health")

    for view in VIEWS:
        fr.include_view(app, view)

    return app
