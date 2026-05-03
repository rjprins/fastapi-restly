import pydantic
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

app = FastAPI()


class Ticket(fr.IDBase):
    full_name: Mapped[str]


class TicketSchema(fr.IDSchema[Ticket]):
    model_config = pydantic.ConfigDict(populate_by_name=True)

    full_name: str = pydantic.Field(alias="fullName")


class TicketBase(fr.AsyncRestView):
    model = Ticket
    schema = TicketSchema
    include_pagination_metadata = True
    exclude_routes = ("delete",)


@fr.include_view(app)
class TicketView(TicketBase):
    prefix = "/tickets"
