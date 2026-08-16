"""Application factory."""

from contextlib import asynccontextmanager

import fastapi_restly as fr
from fastapi import FastAPI

from .settings import Settings
from .users.views import UserView

VIEWS = (UserView,)


def create_app() -> FastAPI:
    settings = Settings.current
    app = FastAPI(title="myapp", lifespan=lifespan)
    fr.configure(app, async_database_url=settings.database_url, health="/health")

    for view in VIEWS:
        fr.include_view(app, view)

    return app


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await fr.db.async_create_all(fr.DataclassBase)
    yield
    # An async engine that is never disposed drops its connections rather than
    # closing them, leaving a ResourceWarning for each one.
    await fr.db.get_async_engine().dispose()
