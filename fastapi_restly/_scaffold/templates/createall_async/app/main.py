"""Application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import fastapi_restly as fr
from fastapi import FastAPI

from .models import Base
from .settings import Settings, get_settings
from .users.views import UserView

VIEWS = (UserView,)


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()
    app = FastAPI(title="myapp", lifespan=lifespan)
    fr.configure(app, async_database_url=settings.database_url, health="/health")

    for view in VIEWS:
        fr.include_view(app, view)

    return app


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await fr.db.async_create_all(Base)
    yield
    # An async engine that is never disposed drops its connections rather than
    # closing them, leaving a ResourceWarning for each one.
    await fr.db.get_async_engine().dispose()
