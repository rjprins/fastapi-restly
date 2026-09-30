"""A SQLModel table as a view's model."""

from fastapi import FastAPI
from sqlmodel import Field, SQLModel

import fastapi_restly as fr

app = FastAPI()


class Item(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str


class ItemRead(fr.IDSchema):
    title: str


@fr.include_view(app)
class ItemView(fr.AsyncRestView[Item, ItemRead, ItemRead, ItemRead, int]):
    prefix = "/items"
    model = Item
    schema = ItemRead


@fr.include_view(app)
class BareItemView(fr.AsyncRestView):
    prefix = "/bare-items"
    model = Item
    schema = ItemRead
