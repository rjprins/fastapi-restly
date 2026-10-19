"""The final reads, typed: where_id and not_found in a generic base view,
and the typed loads in a subclass."""

from typing import Any, TypeVar, cast

from fastapi import FastAPI
from sqlalchemy import ColumnElement
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedAsDataclass

import fastapi_restly as fr

app = FastAPI()


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class HasSlug(MappedAsDataclass):
    """A model mixin: every model with a slug."""

    slug: Mapped[str]


class Recipe(fr.IDMixin, HasSlug, Base):
    name: Mapped[str]


SlugT = TypeVar("SlugT", bound=HasSlug)


class SlugView(fr.AsyncRestView[SlugT, Any, Any, Any, str]):
    """A base view for every model with a slug: the item routes take the
    slug."""

    def where_id(self, id: str) -> ColumnElement[bool]:
        model = cast(type[HasSlug], self.model)
        return model.slug == id

    def not_found(self, id: str) -> fr.exc.NotFound:
        return fr.exc.NotFound(f"No {self.model.__name__} has the slug {id!r}")

    async def twin(self, slug: str) -> SlugT:
        # the final load keeps the model's type in a generic base
        row: SlugT = await self.get_one(slug)
        return row


@fr.include_view(app)
class RecipeView(SlugView[Recipe]):
    prefix = "/recipes"
    model = Recipe

    @fr.get("/{id}/same-name")
    async def same_name(self, id: str) -> list[str]:
        recipe: Recipe = await self.get_one(id)
        stmt = self.select().where(Recipe.name == recipe.name)
        rows: list[Recipe] = list(await self.session.scalars(stmt))
        return [row.slug for row in rows]
