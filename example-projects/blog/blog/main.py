import fastapi_alchmey as fa
from fastapi import FastAPI
from sqlalchemy.orm import Mapped


fa.settings.async_database_url = "sqlite+aiosqlite:///blog.db"


app = FastAPI()


class Blog(fa.IDBase):
    title: Mapped[str]


class BlogSchema(fa.IDSchema[Blog]):
    title: str


@fa.include_view(app)
class BlogView(fa.AsyncAlchemyView):
    prefix = "/blogs"
    model = Blog
    schema = BlogSchema
