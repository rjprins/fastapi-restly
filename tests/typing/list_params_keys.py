"""``fr.query.FilterKey`` and ``fr.query.SortKey`` in list params of your own,
as in the docs, and ``fr.in_utc`` in a business method. A SQLModel table is a
model too."""

from datetime import datetime
from typing import Any

import pydantic
import sqlalchemy
from fastapi import FastAPI
from sqlalchemy.orm import DeclarativeBase, Mapped
from sqlmodel import Field, SQLModel

import fastapi_restly as fr

app = FastAPI()


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class Meeting(fr.IDMixin, Base):
    name: Mapped[str]
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]


class MeetingSchema(fr.IDSchema):
    name: str
    starts_at: fr.UTCDatetime
    ends_at: fr.UTCDatetime


class MeetingListParams(pydantic.BaseModel):
    filter: pydantic.Json[dict[str, Any]] | None = None
    sort: str | None = None
    page: int = pydantic.Field(1, ge=1)
    page_size: int = pydantic.Field(20, ge=1, le=100)


@fr.include_view(app)
class MeetingView(
    fr.AsyncRestView[Meeting, MeetingSchema, MeetingSchema, MeetingSchema, int]
):
    prefix = "/meetings"
    model = Meeting
    schema = MeetingSchema
    schema_list_params = MeetingListParams

    async def apply_filters(
        self, stmt: sqlalchemy.Select[Any], list_params: MeetingListParams
    ) -> sqlalchemy.Select[Any]:
        for name, value in (list_params.filter or {}).items():
            key = fr.query.FilterKey(self.model, self.schema_response, name)
            stmt = stmt.where(key.column == key.parse(value))
        return stmt

    async def apply_list_params(
        self, stmt: sqlalchemy.Select[Any], list_params: MeetingListParams
    ) -> sqlalchemy.Select[Any]:
        stmt = await self.apply_filters(stmt, list_params)
        if list_params.sort:
            name = list_params.sort.removeprefix("-")
            key = fr.query.SortKey(self.model, self.schema_response, name)
            descending = list_params.sort.startswith("-")
            stmt = stmt.order_by(
                key.term(descending=descending, sort_nulls=self.sort_nulls)
            )
        offset = (list_params.page - 1) * list_params.page_size
        return stmt.order_by(Meeting.id).limit(list_params.page_size).offset(offset)

    async def update(self, obj: Meeting, data: MeetingSchema) -> Meeting:
        if data.ends_at < fr.in_utc(obj.starts_at):
            raise ValueError("A meeting ends after it starts.")
        return await super().update(obj, data)


class Hero(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str


class HeroSchema(fr.IDSchema):
    name: str


def find_heroes(name: str, sort: str) -> sqlalchemy.Select[Any]:
    """The keys take a SQLModel table as the model."""
    name_key = fr.query.FilterKey(Hero, HeroSchema, "name")
    sort_key = fr.query.SortKey(Hero, HeroSchema, sort)
    return (
        sqlalchemy.select(Hero)
        .where(name_key.column == name_key.parse(name))
        .order_by(sort_key.term())
    )


def list_heroes(query: dict[str, str]) -> sqlalchemy.Select[Any]:
    """The functions of ``fr.query`` take a SQLModel table too."""
    list_params = fr.query.derive_schema_list_params(HeroSchema, Hero)
    return fr.query.apply_list_params(
        sqlalchemy.select(Hero), list_params.model_validate(query), Hero, HeroSchema
    )
