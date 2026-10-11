from fastapi import FastAPI
from sqlalchemy.orm import DeclarativeBase, Mapped

import fastapi_restly as fr

app = FastAPI()


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class Team(fr.IDMixin, Base):
    name: Mapped[str]


class TeamSchema(fr.IDSchema):
    name: str


@fr.include_view(app)
class TeamView(fr.AsyncRestView):
    prefix = "/teams"
    model = Team
    schema = TeamSchema
