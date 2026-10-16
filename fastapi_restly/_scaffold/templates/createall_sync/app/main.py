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
    fr.configure(app, database_url=settings.database_url, health="/health")

    for view in VIEWS:
        fr.include_view(app, view)

    return app


# Async even for sync views: the ASGI lifespan protocol is async either way.
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    fr.db.create_all(Base)
    yield
