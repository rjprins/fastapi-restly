from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

DB_PATH = Path(__file__).resolve().parents[1] / "blog.db"
fr.configure(async_database_url=f"sqlite+aiosqlite:///{DB_PATH}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    engine = fr.get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(fr.DataclassBase.metadata.create_all)
    yield


app = FastAPI(lifespan=lifespan)


class Blog(fr.IDBase):
    title: Mapped[str]


class BlogSchema(fr.IDSchema):
    title: str


@fr.include_view(app)
class BlogView(fr.AsyncRestView):
    prefix = "/blogs"
    model = Blog
    schema = BlogSchema
