"""Other spellings of a field's type mean the same as the plain ones.

A type alias (``type AuthorId = ...``, or ``TypeAliasType``) around a
reference, a nested schema or a marker, and a to-many nested schema typed
``Sequence[...]`` or ``tuple[..., ...]`` instead of ``list[...]``. Restly reads
a field's type to decide what the field is, so each check must see through
these spellings, or it skips the existence check, the loads of the response,
or the guard for a marker that has no effect.
"""

import typing
from collections.abc import Sequence

import pytest
import typing_extensions
from fastapi import FastAPI
from sqlalchemy import ForeignKey
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

TO_MANY_SPELLINGS = {
    "Sequence": lambda item: Sequence[item],
    "tuple": lambda item: tuple[item, ...],
}


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
def test_a_marker_inside_a_type_alias_is_rejected(alias_class, marker):
    """Pydantic does not apply the marker's field settings through an alias,
    so the field would stay writable, or show in the responses."""
    Hidden = ALIAS_CLASSES[alias_class]("Hidden", getattr(fr, marker)[str])

    with pytest.raises(RestlyConfigurationError, match="Leaky.secret"):

        class Leaky(fr.BaseSchema):
            secret: Hidden


def test_a_reference_in_a_to_many_spelling_is_a_reference():
    class RefTag(fr.IDMixin, SharedBase):
        name: Mapped[str]

    for spelling in TO_MANY_SPELLINGS.values():
        assert _is_id_ref_annotation(spelling(fr.IDRef[RefTag]))
