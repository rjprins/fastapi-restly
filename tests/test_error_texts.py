"""Error texts show what the client knows: an id as it is in the URL, and a
field by the name that the client sends in the body."""

import asyncio
import uuid

import pytest
from fastapi import FastAPI
from pydantic import Field
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.exc import NotFound
from fastapi_restly.schemas._base import _body_references
from fastapi_restly.testing import RestlyTestClient

from ._models import SharedBase
from .conftest import create_tables


def _models():
    class TextThing(SharedBase):
        __tablename__ = "text_thing"
        id: Mapped[uuid.UUID] = mapped_column(
            primary_key=True, init=False, default_factory=uuid.uuid4
        )
        name: Mapped[str]

    class TextAuthor(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class TextTag(SharedBase):
        __tablename__ = "text_tag"
        id: Mapped[uuid.UUID] = mapped_column(
            primary_key=True, init=False, default_factory=uuid.uuid4
        )
        name: Mapped[str]

    class TextPost(fr.IDMixin, SharedBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey("text_author.id"))
        author: Mapped[TextAuthor] = relationship(init=False)
        tags: Mapped[list[TextTag]] = relationship(
            secondary="text_post_tag", default_factory=list
        )

    class TextPostTag(SharedBase):
        __tablename__ = "text_post_tag"
        post_id: Mapped[int] = mapped_column(
            ForeignKey("text_post.id"), primary_key=True, init=False
        )
        tag_id: Mapped[uuid.UUID] = mapped_column(
            ForeignKey("text_tag.id"), primary_key=True, init=False
        )

    class TextPostSchema(fr.BaseSchema):
        title: str
        # A validation alias alone, and one beside an alias: the client sends
        # the validation alias.
        author_id: fr.MustExist[int, TextAuthor] = Field(validation_alias="authorId")
        tags: list[fr.IDRef[TextTag]] = Field(
            default_factory=list, alias="tag", validation_alias="tagIds"
        )

    return TextThing, TextAuthor, TextTag, TextPost, TextPostSchema


def _register(app, view_base):
    TextThing, TextAuthor, _, TextPost, TextPostSchema = _models()

    class TextPostResponse(fr.IDSchema):
        title: str

    class ConflictPostSchema(fr.IDSchema):
        title: str
        author_id: fr.IDRef[TextAuthor] = Field(validation_alias="authorId")
        author: fr.IDSchema[TextAuthor] = Field(alias="writer")

    for prefix, model, settings in (
        ("/things", TextThing, {}),
        ("/authors", TextAuthor, {}),
        (
            "/posts",
            TextPost,
            {"schema": TextPostSchema, "schema_response": TextPostResponse},
        ),
        ("/conflict-posts", TextPost, {"schema": ConflictPostSchema}),
    ):
        name = f"{model.__name__}{prefix.strip('/').title().replace('-', '')}View"
        attributes = {"prefix": prefix, "model": model, **settings}
        fr.include_view(app, type(name, (view_base,), attributes))


def _assert_the_texts(client):
    missing = uuid.uuid4()
    response = client.get(f"/things/{missing}", assert_status_code=404)
    assert response.json()["detail"] == f"TextThing with id {missing} was not found"

    no_author = client.post(
        "/posts", json={"title": "t", "authorId": 99}, assert_status_code=404
    )
    assert no_author.json()["detail"] == "Id not found for authorId: 99"

    first = client.post("/authors", json={"name": "ann"}).json()["id"]
    second = client.post("/authors", json={"name": "bob"}).json()["id"]
    no_tag = client.post(
        "/posts",
        json={"title": "t", "authorId": first, "tagIds": [str(missing)]},
        assert_status_code=404,
    )
    assert no_tag.json()["detail"] == f"Id not found for tagIds: [{missing}]"

    conflict = client.post(
        "/conflict-posts",
        json={"title": "t", "authorId": first, "writer": {"id": second}},
        assert_status_code=422,
    )
    assert conflict.json()["detail"] == (
        f"Conflicting references for authorId and writer: {first} != {second}"
    )


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


def test_the_object_helpers_name_the_fields_as_the_client_sends_them(sync_db):
    """Outside a write action, fr.objects checks the references itself."""
    engine, make_session = sync_db
    _, TextAuthor, _, TextPost, TextPostSchema = _models()
    SharedBase.metadata.create_all(engine)
    missing = uuid.uuid4()

    with make_session() as session:
        author = TextAuthor(name="ann")
        session.add(author)
        session.flush()
        no_author = TextPostSchema.model_validate({"title": "t", "authorId": 99})
        with pytest.raises(NotFound, match=r"^.*authorId: 99$"):
            fr.objects.make_new_object(session, TextPost, no_author)
        no_tag = TextPostSchema.model_validate(
            {"title": "t", "authorId": author.id, "tagIds": [str(missing)]}
        )
        with pytest.raises(NotFound) as excinfo:
            fr.objects.make_new_object(session, TextPost, no_tag)
        assert excinfo.value.detail == f"Id not found for tagIds: [{missing}]"
        post = TextPost(title="t", author_id=author.id)
        session.add(post)
        session.flush()
        with pytest.raises(NotFound, match=r"^.*authorId: 99$"):
            fr.objects.update_object(session, post, no_author)


def test_the_async_object_helpers_name_the_fields_as_the_client_sends_them(client):
    _, TextAuthor, _, TextPost, TextPostSchema = _models()
    create_tables()
    missing = uuid.uuid4()

    async def check() -> None:
        async with fr.open_async_session() as session:
            author = TextAuthor(name="ann")
            session.add(author)
            await session.flush()
            no_author = TextPostSchema.model_validate({"title": "t", "authorId": 99})
            with pytest.raises(NotFound, match=r"^.*authorId: 99$"):
                await fr.objects.async_make_new_object(session, TextPost, no_author)
            no_tag = TextPostSchema.model_validate(
                {"title": "t", "authorId": author.id, "tagIds": [str(missing)]}
            )
            with pytest.raises(NotFound) as excinfo:
                await fr.objects.async_make_new_object(session, TextPost, no_tag)
            assert excinfo.value.detail == f"Id not found for tagIds: [{missing}]"
            post = TextPost(title="t", author_id=author.id)
            session.add(post)
            await session.flush()
            with pytest.raises(NotFound, match=r"^.*authorId: 99$"):
                await fr.objects.async_update_object(session, post, no_author)

    asyncio.run(check())


def test_a_reference_under_a_dict_key_is_named_with_dots():
    class DictProduct(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class DictLine(fr.BaseSchema):
        product_id: fr.MustExist[int, DictProduct] = Field(alias="productId")

    class DictOrder(fr.BaseSchema):
        lines: dict[str, DictLine]

    order = DictOrder.model_validate({"lines": {"k": {"productId": 7}}})

    assert [ref.path for ref in _body_references(order)] == ["lines.k.productId"]
    assert [ref.path for ref in _body_references({"first": order})] == [
        "first.lines.k.productId"
    ]
