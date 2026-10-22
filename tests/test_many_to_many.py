"""Many-to-many relationships, as docs/relationships.md shows them.

Through a plain association table (``secondary``), a list of ids sets the
links and a PATCH replaces them. An association object with columns of its
own is a model with a view of its own.
"""

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy import Column, ColumnElement, ForeignKey, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError
from fastapi_restly.testing import RestlyTestClient

from ._models import SharedBase
from .conftest import create_tables


def _view(view_base, prefix, model, schema, **attributes):
    name = f"{prefix.strip('/').title().replace('-', '')}View"
    attributes = {"prefix": prefix, "model": model, "schema": schema, **attributes}
    return type(name, (view_base,), attributes)


def _tagged_post_models():
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

    return LinkedTag, LinkedPost


def _register_tagged_posts(app, view_base):
    LinkedTag, LinkedPost = _tagged_post_models()

    class TagSchema(fr.IDSchema):
        name: str
        posts: fr.ReadOnly[list[fr.IDRef[LinkedPost]]] = []

    class PostSchema(fr.IDSchema):
        title: str
        tags: list[fr.IDRef[LinkedTag]] = []

    class TagName(fr.IDSchema):
        name: str

    class PostResponse(fr.IDSchema):
        title: str
        tags: list[TagName] = []

    def filter_tag(self, value: list[str]) -> ColumnElement[bool]:
        return LinkedPost.tags.any(LinkedTag.name.in_(value))

    for view in (
        _view(view_base, "/tags", LinkedTag, TagSchema),
        _view(
            view_base,
            "/posts",
            LinkedPost,
            PostSchema,
            filter_tag=fr.list_filter("tag")(filter_tag),
        ),
        _view(
            view_base,
            "/posts-with-tags",
            LinkedPost,
            PostSchema,
            schema_response=PostResponse,
        ),
    ):
        fr.include_view(app, view)


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
    listed = client.get("/posts").json()["data"]
    assert [(row["id"], row["tags"]) for row in listed] == [(post["id"], [tech])]

    missing = client.post(
        "/posts", json={"title": "b", "tags": [news, 999]}, assert_status_code=404
    )
    assert missing.json()["detail"] == "Id not found for tags: [999]"

    # The other side shows the ids of its posts.
    assert client.get(f"/tags/{tech}").json()["posts"] == [post["id"]]
    assert client.get(f"/tags/{news}").json()["posts"] == []

    # Ids in, objects out, with a response class of its own.
    with_tags = client.post(
        "/posts-with-tags", json={"title": "c", "tags": [news]}
    ).json()
    assert with_tags["tags"] == [{"id": news, "name": "news"}]

    # A filter of its own reaches through the list of tags.
    tagged = client.get("/posts", params={"tag": "tech"}).json()["data"]
    assert [row["id"] for row in tagged] == [post["id"]]
    either = client.get("/posts", params=[("tag", "tech"), ("tag", "news")]).json()
    assert sorted(row["id"] for row in either["data"]) == [post["id"], with_tags["id"]]


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


def test_a_dotted_key_cannot_go_through_a_list_of_related_rows():
    LinkedTag, LinkedPost = _tagged_post_models()

    class TagName(fr.IDSchema):
        name: str

    class PostResponse(fr.IDSchema):
        title: str
        tags: list[TagName] = []

    class PostSchema(fr.IDSchema):
        title: str
        tags: list[fr.IDRef[LinkedTag]] = []

    view = _view(
        fr.AsyncRestView,
        "/posts",
        LinkedPost,
        PostSchema,
        schema_response=PostResponse,
        include_filters=("*", "tags.*"),
    )
    with pytest.raises(RestlyConfigurationError, match="'tags.\\*' matches no"):
        fr.include_view(FastAPI(), view)


def _register_post_links(app, view_base, *, cascade=True):
    class LinkTag(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class PostLink(fr.IDMixin, SharedBase):
        __table_args__ = (sa.UniqueConstraint("post_id", "tag_id"),)
        post_id: Mapped[int] = mapped_column(ForeignKey("link_post.id"))
        tag_id: Mapped[int] = mapped_column(ForeignKey("link_tag.id"))
        position: Mapped[int] = mapped_column(default=0)
        tag: Mapped[LinkTag] = relationship(init=False)

    class LinkPost(fr.IDMixin, SharedBase):
        title: Mapped[str]
        links: Mapped[list[PostLink]] = relationship(
            cascade=(
                fr.models.CASCADE_ALL_DELETE_ORPHAN_ASYNC
                if cascade
                else "save-update, merge"
            ),
            default_factory=list,
        )

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

    for view in (
        _view(view_base, "/tags", LinkTag, LinkTagSchema),
        _view(view_base, "/posts", LinkPost, LinkPostSchema),
        _view(view_base, "/post-links", PostLink, PostLinkSchema),
    ):
        fr.include_view(app, view)


def _assert_post_links(client):
    tag = client.post("/tags", json={"name": "news"}).json()["id"]
    post = client.post("/posts", json={"title": "a"}).json()["id"]
    body = {"post_id": post, "tag_id": tag, "position": 3}

    link = client.post("/post-links", json=body).json()["id"]
    client.post("/post-links", json=body, assert_status_code=409)
    client.post("/post-links", json={**body, "tag_id": 999}, assert_status_code=404)

    # Restly loads each level of the nested list.
    (shown,) = client.get(f"/posts/{post}").json()["links"]
    assert (shown["position"], shown["tag"]) == (3, {"id": tag, "name": "news"})

    # The cascade deletes the links with the post.
    client.delete(f"/posts/{post}")
    client.get(f"/post-links/{link}", assert_status_code=404)


def test_without_the_cascade_a_post_with_links_cannot_be_deleted(sync_db):
    engine, _ = sync_db
    app = FastAPI()
    _register_post_links(app, fr.RestView, cascade=False)
    SharedBase.metadata.create_all(engine)
    client = RestlyTestClient(app)
    tag = client.post("/tags", json={"name": "news"}).json()["id"]
    post = client.post("/posts", json={"title": "a"}).json()["id"]
    client.post("/post-links", json={"post_id": post, "tag_id": tag})

    client.delete(f"/posts/{post}", assert_status_code=409)


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
