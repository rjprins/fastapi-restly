"""Many-to-many relationships, as docs/relationships.md shows them.

Through a plain association table (``secondary``), a list of ids sets the
links and a PATCH replaces them. An association object with columns of its
own is a model with a view of its own.
"""

import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy import Column, ForeignKey, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient

from ._models import SharedBase
from .conftest import create_tables


def _include(app, view_base, *routes):
    for prefix, model, schema in routes:
        name = f"{schema.__name__}View"
        attributes = {"prefix": prefix, "model": model, "schema": schema}
        fr.include_view(app, type(name, (view_base,), attributes))


def _register_tagged_posts(app, view_base):
    linked_post_tag = Table(
        "linked_post_tag",
        SharedBase.metadata,
        Column("post_id", ForeignKey("linked_post.id"), primary_key=True),
        Column("tag_id", ForeignKey("linked_tag.id"), primary_key=True),
    )

    class LinkedTag(fr.IDMixin, SharedBase):
        name: Mapped[str]
        posts: Mapped[list["LinkedPost"]] = relationship(
            secondary=linked_post_tag, back_populates="tags", default_factory=list
        )

    class LinkedPost(fr.IDMixin, SharedBase):
        title: Mapped[str]
        tags: Mapped[list[LinkedTag]] = relationship(
            secondary=linked_post_tag, back_populates="posts", default_factory=list
        )

    class TagSchema(fr.IDSchema):
        name: str
        posts: fr.ReadOnly[list[fr.IDRef[LinkedPost]]] = []

    class PostSchema(fr.IDSchema):
        title: str
        tags: list[fr.IDRef[LinkedTag]] = []

    class TagName(fr.IDSchema):
        name: str

    class PostDetailSchema(fr.IDSchema):
        title: str
        tags: fr.ReadOnly[list[TagName]] = []

    _include(
        app,
        view_base,
        ("/tags", LinkedTag, TagSchema),
        ("/posts", LinkedPost, PostSchema),
        ("/post-details", LinkedPost, PostDetailSchema),
    )


def _assert_tagged_posts(client):
    news = client.post("/tags", json={"name": "news"}).json()["id"]
    tech = client.post("/tags", json={"name": "tech"}).json()["id"]

    post = client.post("/posts", json={"title": "a", "tags": [news, tech]}).json()
    assert sorted(post["tags"]) == sorted([news, tech])
    stored = client.get(f"/posts/{post['id']}").json()
    assert sorted(stored["tags"]) == sorted([news, tech])

    # PATCH replaces the whole list.
    patched = client.patch(f"/posts/{post['id']}", json={"tags": [tech]}).json()
    assert patched["tags"] == [tech]

    missing = client.post(
        "/posts", json={"title": "b", "tags": [news, 999]}, assert_status_code=404
    )
    assert missing.json()["detail"] == "Id not found for tags: [999]"

    # The other side, and the tags as objects.
    assert client.get(f"/tags/{tech}").json()["posts"] == [post["id"]]
    assert client.get(f"/tags/{news}").json()["posts"] == []
    detail = client.get(f"/post-details/{post['id']}").json()
    assert detail["tags"] == [{"id": tech, "name": "tech"}]

    # A nested schema is read-only: it cannot set the links.
    created = client.post(
        "/post-details", json={"title": "c", "tags": [{"id": news, "name": "x"}]}
    ).json()
    assert created["tags"] == []

    # A key cannot go through a list of related rows.
    client.get("/posts", params={"tags.name": "news"}, assert_status_code=422)


def _register_post_links(app, view_base):
    class LinkTag(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class LinkPost(fr.IDMixin, SharedBase):
        title: Mapped[str]
        links: Mapped[list["PostLink"]] = relationship(
            back_populates="post", default_factory=list, cascade="all, delete-orphan"
        )

    class PostLink(fr.IDMixin, SharedBase):
        __table_args__ = (sa.UniqueConstraint("post_id", "tag_id"),)
        post_id: Mapped[int] = mapped_column(ForeignKey("link_post.id"))
        tag_id: Mapped[int] = mapped_column(ForeignKey("link_tag.id"))
        position: Mapped[int] = mapped_column(default=0)
        post: Mapped[LinkPost] = relationship(back_populates="links", init=False)
        tag: Mapped[LinkTag] = relationship(init=False)

    class LinkTagSchema(fr.IDSchema):
        name: str

    class PostLinkSchema(fr.IDSchema):
        post_id: fr.MustExist[int, LinkPost]
        tag_id: fr.MustExist[int, LinkTag]
        position: int = 0
        tag: fr.ReadOnly[LinkTagSchema | None] = None

    class LinkPostSchema(fr.IDSchema):
        title: str
        links: fr.ReadOnly[list[PostLinkSchema]] = []

    _include(
        app,
        view_base,
        ("/tags", LinkTag, LinkTagSchema),
        ("/posts", LinkPost, LinkPostSchema),
        ("/post-links", PostLink, PostLinkSchema),
    )


def _assert_post_links(client):
    tag = client.post("/tags", json={"name": "news"}).json()["id"]
    post = client.post("/posts", json={"title": "a"}).json()["id"]
    body = {"post_id": post, "tag_id": tag, "position": 3}

    client.post("/post-links", json=body)
    client.post("/post-links", json=body, assert_status_code=409)
    client.post("/post-links", json={**body, "tag_id": 999}, assert_status_code=404)

    # Restly loads each level of the nested list.
    (link,) = client.get(f"/posts/{post}").json()["links"]
    assert (link["position"], link["tag"]) == (3, {"id": tag, "name": "news"})


def test_a_many_to_many_through_a_table_async(client):
    _register_tagged_posts(client.app, fr.AsyncRestView)
    create_tables()
    _assert_tagged_posts(client)


def test_a_many_to_many_through_a_table_sync(sync_db):
    engine, _ = sync_db
    app = FastAPI()
    _register_tagged_posts(app, fr.RestView)
    SharedBase.metadata.create_all(engine)
    _assert_tagged_posts(RestlyTestClient(app))


def test_an_association_object_is_a_model_with_its_own_view_async(client):
    _register_post_links(client.app, fr.AsyncRestView)
    create_tables()
    _assert_post_links(client)


def test_an_association_object_is_a_model_with_its_own_view_sync(sync_db):
    engine, _ = sync_db
    app = FastAPI()
    _register_post_links(app, fr.RestView)
    SharedBase.metadata.create_all(engine)
    _assert_post_links(RestlyTestClient(app))
