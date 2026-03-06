from contextlib import asynccontextmanager
from pathlib import Path

import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

DB_PATH = Path(__file__).resolve().parents[1] / "blog.db"
fr.setup_async_database_connection(async_database_url=f"sqlite+aiosqlite:///{DB_PATH}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    engine = fr.FRAsyncSession.kw["bind"]
    async with engine.begin() as conn:
        await conn.run_sync(fr.Base.metadata.create_all)
    yield


app = FastAPI(lifespan=lifespan)


class Blog(fr.IDBase):
    title: Mapped[str]


class BlogSchema(fr.IDSchema[Blog]):
    title: str


@fr.include_view(app)
class BlogView(fr.AsyncAlchemyView):
    prefix = "/blogs"
    model = Blog
    schema = BlogSchema
