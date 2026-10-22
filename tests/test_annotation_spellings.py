"""Other spellings of a field's type mean the same as the plain ones.

A type alias (``type AuthorId = ...``, ``type Many[T] = list[T]``, or
``TypeAliasType``) around a reference, a nested schema or a marker, and a
to-many field typed ``Sequence[X]`` or ``tuple[X, ...]`` instead of
``list[X]``. Restly reads
a field's type to decide what the field is, so each check must see through
these spellings, or it skips the existence check, the loads of the response,
or the guard for a marker that has no effect.
"""

import sys
import typing
from collections.abc import MutableSequence, Sequence
from typing import Any

import pytest
import typing_extensions
from fastapi import FastAPI
from sqlalchemy import JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError
from fastapi_restly.testing import RestlyTestClient
from fastapi_restly.views._openapi import _is_id_ref_annotation

from ._models import SharedBase
from .conftest import create_tables

# ``type X = ...`` makes a ``typing.TypeAliasType`` (Python 3.12 and later).
# typing_extensions has a class of its own on some versions.
ALIAS_CLASSES = {"typing_extensions": typing_extensions.TypeAliasType}
if getattr(typing, "TypeAliasType", typing_extensions.TypeAliasType) not in (
    ALIAS_CLASSES.values()
):
    ALIAS_CLASSES["typing"] = typing.TypeAliasType  # type: ignore[attr-defined]

_T = typing.TypeVar("_T")

TO_MANY_SPELLINGS = {
    "Sequence": lambda item: Sequence[item],
    "MutableSequence": lambda item: MutableSequence[item],
    "tuple": lambda item: tuple[item, ...],
}
for _name, _alias_class in ALIAS_CLASSES.items():
    _many = _alias_class("Many", list[_T], type_params=(_T,))
    TO_MANY_SPELLINGS[f"generic-alias-{_name}"] = lambda item, many=_many: many[item]


def _register_posts(app, view_base, alias_class):
    class AliasUser(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class AliasPost(fr.IDMixin, SharedBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey("alias_user.id"))

    AuthorId = alias_class("AuthorId", fr.MustExist[int, AliasUser])

    class AliasPostSchema(fr.IDSchema):
        title: str
        author_id: AuthorId

    @fr.include_view(app)
    class AliasPostView(view_base):
        prefix = "/posts"
        model = AliasPost
        schema = AliasPostSchema


def _assert_missing_author_is_404(client):
    response = client.post(
        "/posts", json={"title": "t", "author_id": 99}, assert_status_code=404
    )
    assert response.json()["detail"] == "Id not found for author_id: 99"


@pytest.mark.parametrize("alias_class", ALIAS_CLASSES)
def test_must_exist_behind_a_type_alias_is_checked_async(client, alias_class):
    _register_posts(client.app, fr.AsyncRestView, ALIAS_CLASSES[alias_class])
    create_tables()
    _assert_missing_author_is_404(client)


@pytest.mark.parametrize("alias_class", ALIAS_CLASSES)
def test_must_exist_behind_a_type_alias_is_checked_sync(sync_db, alias_class):
    engine, _ = sync_db
    app = FastAPI()
    _register_posts(app, fr.RestView, ALIAS_CLASSES[alias_class])
    SharedBase.metadata.create_all(engine)
    _assert_missing_author_is_404(RestlyTestClient(app))


def _register_authors(app, view_base, spelling):
    class SpellingAuthor(fr.IDMixin, SharedBase):
        name: Mapped[str]
        articles: Mapped[list["SpellingArticle"]] = relationship(default_factory=list)

    class SpellingArticle(fr.IDMixin, SharedBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey("spelling_author.id"))
        tags: Mapped[list["SpellingTag"]] = relationship(default_factory=list)

    class SpellingTag(fr.IDMixin, SharedBase):
        name: Mapped[str]
        article_id: Mapped[int] = mapped_column(ForeignKey("spelling_article.id"))

    class SpellingTagOut(fr.IDSchema):
        name: str

    class SpellingArticleOut(fr.IDSchema):
        title: str
        tags: list[SpellingTagOut]

    class SpellingAuthorSchema(fr.IDSchema):
        name: str
        articles: fr.ReadOnly[spelling(SpellingArticleOut)] = ()

    class SpellingArticleSchema(fr.IDSchema):
        title: str
        author_id: int

    class SpellingTagSchema(fr.IDSchema):
        name: str
        article_id: int

    for prefix, model, schema in (
        ("/authors", SpellingAuthor, SpellingAuthorSchema),
        ("/articles", SpellingArticle, SpellingArticleSchema),
        ("/tags", SpellingTag, SpellingTagSchema),
    ):
        view = type(
            f"{model.__name__}View",
            (view_base,),
            {"prefix": prefix, "model": model, "schema": schema},
        )
        fr.include_view(app, view)


def _assert_the_second_level_is_loaded(client):
    author_id = client.post("/authors", json={"name": "ann"}).json()["id"]
    article_id = client.post(
        "/articles", json={"title": "one", "author_id": author_id}
    ).json()["id"]
    client.post("/tags", json={"name": "news", "article_id": article_id})

    one = client.get(f"/authors/{author_id}").json()
    many = client.get("/authors").json()["data"]

    for author in (one, *many):
        assert author["articles"][0]["tags"][0]["name"] == "news"


@pytest.mark.parametrize("spelling", TO_MANY_SPELLINGS)
def test_a_to_many_nested_schema_loads_its_relationships_async(client, spelling):
    _register_authors(client.app, fr.AsyncRestView, TO_MANY_SPELLINGS[spelling])
    create_tables()
    _assert_the_second_level_is_loaded(client)


@pytest.mark.parametrize("spelling", TO_MANY_SPELLINGS)
def test_a_to_many_nested_schema_loads_its_relationships_sync(sync_db, spelling):
    engine, _ = sync_db
    app = FastAPI()
    _register_authors(app, fr.RestView, TO_MANY_SPELLINGS[spelling])
    SharedBase.metadata.create_all(engine)
    _assert_the_second_level_is_loaded(RestlyTestClient(app))


@pytest.mark.parametrize("alias_class", ALIAS_CLASSES)
@pytest.mark.parametrize("marker", ["ReadOnly", "WriteOnly"])
@pytest.mark.parametrize("generic", [False, True], ids=["plain", "generic"])
def test_a_marker_inside_a_type_alias_is_rejected(alias_class, marker, generic):
    """Pydantic does not apply the marker's field settings through an alias,
    so the field would stay writable, or show in the responses."""
    alias_class = ALIAS_CLASSES[alias_class]
    if generic:
        Hidden = alias_class("Hidden", getattr(fr, marker)[_T], type_params=(_T,))
        annotation = Hidden[str]
    else:
        annotation = alias_class("Hidden", getattr(fr, marker)[str])

    with pytest.raises(RestlyConfigurationError, match="Leaky.secret") as excinfo:
        type("Leaky", (fr.BaseSchema,), {"__annotations__": {"secret": annotation}})

    assert "move the marker out of the alias" in str(excinfo.value)


def test_a_reference_in_a_to_many_spelling_is_a_reference():
    class RefTag(fr.IDMixin, SharedBase):
        name: Mapped[str]

    for spelling in TO_MANY_SPELLINGS.values():
        assert _is_id_ref_annotation(spelling(fr.IDRef[RefTag]))


@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="the type statement needs Python 3.12"
)
def test_a_recursive_type_alias_works(client):
    """The alias comes back inside its own value. A check expands it once."""
    namespace: dict[str, Any] = {}
    exec("type Json = dict[str, Json] | list[Json] | str | int | None", namespace)

    class RecursiveDoc(fr.IDMixin, SharedBase):
        data: Mapped[Any] = mapped_column(JSON)

    class RecursiveDocSchema(fr.IDSchema):
        data: namespace["Json"]

    @fr.include_view(client.app)
    class RecursiveDocView(fr.AsyncRestView):
        prefix = "/docs"
        model = RecursiveDoc
        schema = RecursiveDocSchema

    create_tables()
    created = client.post("/docs", json={"data": {"a": [1, "x"]}}).json()
    assert created["data"] == {"a": [1, "x"]}
    assert "/docs" in client.app.openapi()["paths"]


def _register_tagged_articles(app, view_base):
    class TupleTag(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class TupleArticle(fr.IDMixin, SharedBase):
        title: Mapped[str]
        tags: Mapped[list[TupleTag]] = relationship(
            secondary="tuple_article_tag", default_factory=list
        )

    class TupleArticleTag(SharedBase):
        __tablename__ = "tuple_article_tag"
        article_id: Mapped[int] = mapped_column(
            ForeignKey("tuple_article.id"), primary_key=True, init=False
        )
        tag_id: Mapped[int] = mapped_column(
            ForeignKey("tuple_tag.id"), primary_key=True, init=False
        )

    class TupleTagSchema(fr.IDSchema):
        name: str

    class TupleArticleSchema(fr.IDSchema):
        title: str
        tags: tuple[fr.IDRef[TupleTag], ...] = ()

    for prefix, model, schema in (
        ("/tags", TupleTag, TupleTagSchema),
        ("/articles", TupleArticle, TupleArticleSchema),
    ):
        view = type(
            f"{model.__name__}View",
            (view_base,),
            {"prefix": prefix, "model": model, "schema": schema},
        )
        fr.include_view(app, view)


def _assert_a_tuple_of_references_is_written(client):
    tag_id = client.post("/tags", json={"name": "news"}).json()["id"]

    created = client.post("/articles", json={"title": "one", "tags": [tag_id]})
    assert created.json()["tags"] == [tag_id]
    article = client.get(f"/articles/{created.json()['id']}").json()
    assert article["tags"] == [tag_id]

    missing = client.post(
        "/articles", json={"title": "two", "tags": [999]}, assert_status_code=404
    )
    assert missing.json()["detail"] == "Id not found for tags: [999]"


def test_a_tuple_of_references_is_written_async(client):
    _register_tagged_articles(client.app, fr.AsyncRestView)
    create_tables()
    _assert_a_tuple_of_references_is_written(client)


def test_a_tuple_of_references_is_written_sync(sync_db):
    engine, _ = sync_db
    app = FastAPI()
    _register_tagged_articles(app, fr.RestView)
    SharedBase.metadata.create_all(engine)
    _assert_a_tuple_of_references_is_written(RestlyTestClient(app))
