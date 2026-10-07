"""A SQLModel table as a view's model."""

from fastapi import FastAPI
from sqlmodel import Field, SQLModel

import fastapi_restly as fr

app = FastAPI()


class Item(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str


class ItemSchema(fr.IDSchema):
    title: str


@fr.include_view(app)
class ItemView(fr.AsyncRestView[Item, ItemSchema, ItemSchema, ItemSchema, int]):
    prefix = "/items"
    model = Item
    schema = ItemSchema


@fr.include_view(app)
class BareItemView(fr.AsyncRestView):
    prefix = "/bare-items"
    model = Item
    schema = ItemSchema


class Team(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str


class Hero(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    team_id: int | None = Field(default=None, foreign_key="team.id")


class HeroSchema(fr.IDSchema[Hero]):
    name: str
    team: fr.IDRef[Team] | None = None
    team_id: fr.MustExist[int, Team] | None = None
    nested_team: fr.IDSchema[Team] | None = None


@fr.include_view(app)
class HeroView(fr.AsyncRestView[Hero, HeroSchema, HeroSchema, HeroSchema, int]):
    prefix = "/heroes"
    model = Hero
    schema = HeroSchema
