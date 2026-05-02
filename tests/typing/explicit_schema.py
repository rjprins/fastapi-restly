from fastapi import FastAPI
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

app = FastAPI()


class Team(fr.IDBase):
    name: Mapped[str]


class TeamSchema(fr.IDSchema):
    name: str


@fr.include_view(app)
class TeamView(fr.AsyncRestView):
    prefix = "/teams"
    model = Team
    schema = TeamSchema
