from fastapi import FastAPI
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import fastapi_restly as fr

app = FastAPI()


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class Author(fr.IDMixin, Base):
    name: Mapped[str]


class Article(fr.IDMixin, Base):
    title: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
    author: Mapped[Author]


class ArticleSchema(fr.IDSchema[Article]):
    title: str
    author_id: fr.IDSchema[Author]


@fr.include_view(app)
class ArticleView(fr.AsyncRestView):
    prefix = "/articles"
    model = Article
    schema = ArticleSchema
