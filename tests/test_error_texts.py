"""Error texts show what the client knows: the id as it is in the URL, and a
field by the name that the client sends."""

import uuid

from fastapi import FastAPI
from pydantic import Field
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient

from ._models import SharedBase
from .conftest import create_tables


def _register(app, view_base):
    class TextThing(SharedBase):
        __tablename__ = "text_thing"
        id: Mapped[uuid.UUID] = mapped_column(
            primary_key=True, init=False, default_factory=uuid.uuid4
        )
        name: Mapped[str]

    class TextAuthor(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class TextTag(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class TextPost(fr.IDMixin, SharedBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey("text_author.id"))
        tags: Mapped[list[TextTag]] = relationship(
            secondary="text_post_tag", default_factory=list
        )

    class TextPostTag(SharedBase):
        __tablename__ = "text_post_tag"
        post_id: Mapped[int] = mapped_column(
            ForeignKey("text_post.id"), primary_key=True, init=False
        )
        tag_id: Mapped[int] = mapped_column(
            ForeignKey("text_tag.id"), primary_key=True, init=False
        )

    class TextPostSchema(fr.IDSchema):
        title: str
        author_id: fr.MustExist[int, TextAuthor] = Field(alias="authorId")
        tags: list[fr.IDRef[TextTag]] = Field(default_factory=list, alias="tagIds")

    for prefix, model, schema in (
        ("/things", TextThing, None),
        ("/authors", TextAuthor, None),
        ("/posts", TextPost, TextPostSchema),
    ):
        attributes = {"prefix": prefix, "model": model}
        if schema is not None:
            attributes["schema"] = schema
        fr.include_view(app, type(f"{model.__name__}View", (view_base,), attributes))


def _assert_the_texts(client):
    missing = uuid.uuid4()
    response = client.get(f"/things/{missing}", assert_status_code=404)
    assert response.json()["detail"] == f"TextThing with id {missing} was not found"

    no_author = client.post(
        "/posts", json={"title": "t", "authorId": 99}, assert_status_code=404
    )
    assert no_author.json()["detail"] == "Id not found for authorId: 99"

    author_id = client.post("/authors", json={"name": "ann"}).json()["id"]
    no_tag = client.post(
        "/posts",
        json={"title": "t", "authorId": author_id, "tagIds": [99]},
        assert_status_code=404,
    )
    assert no_tag.json()["detail"] == "Id not found for tagIds: [99]"


def test_the_error_texts_async(client):
    _register(client.app, fr.AsyncRestView)
    create_tables()
    _assert_the_texts(client)


def test_the_error_texts_sync(sync_db):
    engine, _ = sync_db
    app = FastAPI()
    _register(app, fr.RestView)
    SharedBase.metadata.create_all(engine)
    _assert_the_texts(RestlyTestClient(app))
