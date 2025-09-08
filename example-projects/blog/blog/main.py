import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

fr.setup_async_database_connection(async_database_url="sqlite+aiosqlite:///blog.db")


app = FastAPI()


class Blog(fr.IDBase):
    title: Mapped[str]


class BlogSchema(fr.IDSchema[Blog]):
    title: str


@fr.include_view(app)
class BlogView(fr.AsyncAlchemyView):
    prefix = "/blogs"
    model = Blog
    schema = BlogSchema
