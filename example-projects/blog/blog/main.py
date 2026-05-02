from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

fr.configure(async_database_url="sqlite+aiosqlite:///blog.db")


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
