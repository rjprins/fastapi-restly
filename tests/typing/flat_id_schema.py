"""Typing fixture: relationships expressed as `IDRef[Model]`.

Verifies that consumer code using `IDRef` for list-of-relations or
single-relation references stays Pyright-clean.
"""
from fastapi import FastAPI
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr

app = FastAPI()


class Tag(fr.IDBase):
    name: Mapped[str]


class Post(fr.IDBase):
    title: Mapped[str]
    primary_tag_id: Mapped[int] = mapped_column(ForeignKey("tag.id"))
    primary_tag: Mapped[Tag] = relationship()


class TagSchema(fr.IDSchema[Tag]):
    name: str


class PostSchema(fr.IDSchema[Post]):
    title: str
    # Single relation as a flat scalar id.
    primary_tag_id: fr.IDRef[Tag]
    # List-of-relation as flat scalar ids (React Admin friendly).
    related_tags: fr.ReadOnly[list[fr.IDRef[Tag]]] = []


@fr.include_view(app)
class TagView(fr.AsyncRestView):
    prefix = "/tags"
    model = Tag
    schema = TagSchema


@fr.include_view(app)
class PostView(fr.AsyncRestView):
    prefix = "/posts"
    model = Post
    schema = PostSchema
