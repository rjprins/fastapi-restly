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
    fr.configure(app, database_url=settings.database_url, health="/health")

    for view in VIEWS:
        fr.include_view(app, view)

    return app


# Async even for sync views: the ASGI lifespan protocol is async either way.
@asynccontextmanager
async def lifespan(_app: FastAPI):
    fr.db.create_all(fr.DataclassBase)
    yield
