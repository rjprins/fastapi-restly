from typing import Any

from fastapi import FastAPI
from sqlalchemy.orm import DeclarativeBase, Mapped

import fastapi_restly as fr

app = FastAPI()


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class Post(fr.IDMixin, Base):
    title: Mapped[str]
    published: Mapped[bool]


class PostSchema(fr.IDSchema[Post]):
    title: str
    published: fr.ReadOnly[bool]


class TrashMixin:
    class Action(fr.Action):
        RESTORE = "restore"


@fr.include_view(app)
class PostView(TrashMixin, fr.AsyncRestView):
    prefix = "/posts"
    model = Post
    schema = PostSchema

    # The view's actions, those of the mixin and fr.Action included.
    class Action(TrashMixin.Action):
        PUBLISH = "publish"

    @fr.post("/{id}/publish")
    async def publish(self, id: int) -> Post:
        post: Post = await self.get_one(id)
        async with self.write_action(self.Action.PUBLISH, obj=post):
            post.published = True
        return post

    async def authorize(
        self, action: str, obj: Any | None = None, data: Any = None
    ) -> None:
        if action in (self.Action.RESTORE, self.Action.GET_ONE):
            return


publish: str = PostView.Action.PUBLISH
restore: str = PostView.Action.RESTORE
update: str = PostView.Action.UPDATE


class ItemView(fr.RestView):
    pass


# A view without actions of its own has the five CRUD actions.
delete: str = ItemView.Action.DELETE
