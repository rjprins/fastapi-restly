from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import fastapi_restly as fr

fr.configure(async_database_url="sqlite+aiosqlite:///blog.db")


class Base(DeclarativeBase):
    pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await fr.db.async_create_all(Base)  # dev tables; use Alembic in production
    yield


app = FastAPI(lifespan=lifespan)


class Post(Base):
    __tablename__ = "post"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    content: Mapped[str]
    published: Mapped[bool] = mapped_column(default=False)


@fr.include_view(app)
class PostView(fr.AsyncRestView):
    prefix = "/posts"
    model = Post


class Comment(Base):
    __tablename__ = "comment"

    id: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[str]
    post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))


class CommentSchema(fr.IDSchema):
    content: str
    post_id: fr.MustExist[int, Post]


@fr.include_view(app)
class CommentView(fr.AsyncRestView):
    prefix = "/comments"
    model = Comment
    schema = CommentSchema
