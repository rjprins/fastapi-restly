import fastapi_ding as fd
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

fd.setup_async_database_connection(async_database_url="sqlite+aiosqlite:///blog.db")


app = FastAPI()


class Blog(fd.IDBase):
    title: Mapped[str]


class BlogSchema(fd.IDSchema[Blog]):
    title: str


@fd.include_view(app)
class BlogView(fd.AsyncAlchemyView):
    prefix = "/blogs"
    model = Blog
    schema = BlogSchema
