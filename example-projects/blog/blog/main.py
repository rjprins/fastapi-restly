from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

fr.configure(database_url="sqlite:///blog.db")


def create_tables() -> None:
    with fr.open_session() as db_session:
        fr.DataclassBase.metadata.create_all(bind=db_session.get_bind())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_tables()
    yield


app = FastAPI(lifespan=lifespan)


class Blog(fr.IDBase):
    title: Mapped[str]


@fr.include_view(app)
class BlogView(fr.RestView):
    prefix = "/blogs"
    model = Blog
    session: fr.SessionDep
