import os
from contextlib import asynccontextmanager
from typing import Annotated

import fastapi
from fastapi import FastAPI, Header
from sqlalchemy import ForeignKey, or_
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr

fr.configure(
    async_database_url=os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///blog.db")
)


class Current(fr.ContextNamespace):
    user_id: fr.ContextParam[int | None]


def get_user_id(
    user_id: Annotated[int | None, Header(alias="X-User-ID")] = None,
) -> int | None:
    # Demo stand-in for real authentication. None means an anonymous reader.
    return user_id


class Post(fr.TimestampsMixin, fr.IDBase):
    title: Mapped[str]
    content: Mapped[str]
    published: Mapped[bool] = mapped_column(default=False)
    author_id: Mapped[int] = mapped_column(init=False, insert_default=Current.user_id)


class PostClauses(fr.ClauseNamespace):
    model = Post

    default_scope = fr.where_clause(
        or_(Post.published, Post.author_id == Current.user_id)
    )


class Comment(fr.TimestampsMixin, fr.IDBase):
    content: Mapped[str]
    post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))
    author_id: Mapped[int] = mapped_column(init=False, insert_default=Current.user_id)
    post: Mapped[Post] = relationship(init=False)


class CommentClauses(fr.ClauseNamespace):
    model = Comment

    default_scope = fr.where_clause(Comment.post.has(PostClauses.default_scope))


class PostSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    title: str
    content: str
    published: fr.ReadOnly[bool]
    author_id: fr.ReadOnly[int]


class CommentSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    content: str
    post_id: fr.MustExist[int, Post]
    author_id: fr.ReadOnly[int]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await fr.db.async_create_all(fr.IDBase)
    yield


app = FastAPI(lifespan=lifespan, dependencies=[Current.depends(user_id=get_user_id)])


async def notify_subscribers(post):
    print(f"New post: {post.title}")  # stand-in for an email or a webhook


class AuthoredView(fr.AsyncRestView):
    async def authorize(self, action, obj=None, data=None):
        if action in ("get_many", "get_one"):
            return
        if Current.user_id() is None:
            raise fastapi.HTTPException(401, "Log in to write")
        if obj is not None and obj.author_id != Current.user_id():
            raise fr.exc.Forbidden("Only the author can change this")


@fr.include_view(app)
class PostView(AuthoredView):
    prefix = "/posts"
    model = Post
    schema = PostSchema

    @fr.post("/{id}/publish", status_code=200)
    async def publish(self, id: int):
        post = await self.get_one(id)
        async with self.write_action("publish", obj=post):
            if post.published:
                raise fr.exc.Conflict("Already published")
            post.published = True
        return self.to_response(post)

    async def after_action_commit(self, action, new, old=None):
        if action == "publish":
            await notify_subscribers(new)


@fr.include_view(app)
class CommentView(AuthoredView):
    prefix = "/comments"
    model = Comment
    schema = CommentSchema
