from typing import Any, ClassVar

import pydantic
from fastapi import Depends, FastAPI
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

app = FastAPI()


class Ticket(fr.IDBase):
    full_name: Mapped[str]


class TicketRead(fr.IDSchema[Ticket]):
    model_config = pydantic.ConfigDict(populate_by_name=True)

    full_name: str = pydantic.Field(alias="fullName")


class TicketBase(fr.AsyncRestView):
    model = Ticket
    schema = TicketRead
    pagination = None
    exclude_routes = [fr.ViewRoute.DELETE]
    dependencies: ClassVar[list[Any]] = [Depends(lambda: None)]


@fr.include_view(app)
class TicketView(TicketBase):
    prefix = "/tickets"
    route_options = {
        fr.ViewRoute.GET_MANY: {"name": "list_tickets", "operation_id": "list_tickets"},
        "create_endpoint": {"summary": "Create a ticket"},
    }


APP_PAGINATION = fr.NumberedPagination(page_size_query_param="size", max_page_size=100)


class PagedTicketView(fr.AsyncRestView):
    model = Ticket
    schema = TicketRead
    # annotated, so mypy lets a subclass turn pagination off
    pagination: ClassVar[fr.NumberedPagination | fr.NoPagination | None] = (
        APP_PAGINATION
    )


class SmallPagedTicketView(PagedTicketView):
    pagination = APP_PAGINATION.replace(max_page_size=10)


class AllTicketView(PagedTicketView):
    pagination = None


class ExportTicketView(PagedTicketView):
    pagination = fr.NoPagination().replace(envelope=fr.views.Envelope)
