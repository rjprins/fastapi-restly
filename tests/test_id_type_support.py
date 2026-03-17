from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import FastAPI, Response
from pydantic import BaseModel
from sqlalchemy import ForeignKey, Uuid
from sqlalchemy.orm import Mapped, relationship

import fastapi_restly as fr

from .conftest import create_tables


class UUIDModel(fr.Base):
    __tablename__ = "uuid_model"

    id: Mapped[UUID] = fr.mapped_column(
        Uuid, primary_key=True, default_factory=uuid4
    )
    name: Mapped[str]


class UUIDSchema(BaseModel):
    id: UUID
    name: str


class UUIDView(fr.AsyncAlchemyView):
    prefix = "/uuid-models"
    model = UUIDModel
    schema = UUIDSchema
    id_type = UUID

    async def process_get(self, id: UUID):
        return SimpleNamespace(id=id, name="demo")

    async def process_patch(self, id: UUID, schema_obj: BaseModel):
        return SimpleNamespace(id=id, name=getattr(schema_obj, "name", "demo"))

    async def process_delete(self, id: UUID):
        return Response(status_code=204)


def test_view_id_type_controls_openapi_path_parameter():
    app = FastAPI()
    fr.include_view(app, UUIDView)

    parameter_schema = app.openapi()["paths"]["/uuid-models/{id}"]["get"]["parameters"][0]["schema"]
    assert parameter_schema["type"] == "string"
    assert parameter_schema["format"] == "uuid"


def test_idschema_accepts_uuid_relation_ids(client):
    class Author(fr.Base):
        __tablename__ = "uuid_author"

        id: Mapped[UUID] = fr.mapped_column(
            Uuid, primary_key=True, default_factory=uuid4
        )
        name: Mapped[str]

    class Article(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[UUID] = fr.mapped_column(
            Uuid, ForeignKey("uuid_author.id")
        )
        author: Mapped[Author] = relationship()

    class AuthorSchema(fr.BaseSchema):
        id: fr.ReadOnly[UUID]
        name: str

    class ArticleSchema(fr.IDSchema):
        title: str
        author_id: fr.IDSchema[Author]

    @fr.include_view(client.app)
    class AuthorView(fr.AsyncAlchemyView):
        prefix = "/uuid-authors"
        model = Author
        schema = AuthorSchema
        id_type = UUID

    @fr.include_view(client.app)
    class ArticleView(fr.AsyncAlchemyView):
        prefix = "/uuid-articles"
        model = Article
        schema = ArticleSchema

    create_tables()

    author_response = client.post("/uuid-authors/", json={"name": "Alice"})
    author_id = author_response.json()["id"]

    article_response = client.post(
        "/uuid-articles/",
        json={"title": "Hello", "author_id": {"id": author_id}},
    )

    assert article_response.status_code == 201
    payload = article_response.json()
    assert payload["title"] == "Hello"
    assert payload["author_id"]["id"] == author_id
