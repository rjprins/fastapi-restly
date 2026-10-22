from typing import Any, ClassVar

import pydantic
from fastapi import Depends, FastAPI
from sqlalchemy.orm import DeclarativeBase, Mapped

import fastapi_restly as fr

app = FastAPI()


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class Ticket(fr.IDMixin, Base):
    full_name: Mapped[str]


class TicketSchema(fr.IDSchema[Ticket]):
    model_config = pydantic.ConfigDict(populate_by_name=True)

    full_name: str = pydantic.Field(alias="fullName")


class TicketBase(fr.AsyncRestView):
    model = Ticket
    schema = TicketSchema
    pagination = None
    exclude_routes = [fr.ViewRoute.DELETE]
    dependencies: ClassVar[list[Any]] = [Depends(lambda: None)]


@fr.include_view(app)
class TicketView(TicketBase):
    prefix = "/tickets"


APP_PAGINATION = fr.NumberedPagination(page_size_query_param="size", max_page_size=100)


class PagedTicketView(fr.AsyncRestView):
    model = Ticket
    schema = TicketSchema
    # annotated, so mypy lets a subclass turn pagination off
    pagination: ClassVar[fr.NumberedPagination | fr.NoPagination | None] = (
        APP_PAGINATION
    )


class SmallPagedTicketView(PagedTicketView):
    pagination = APP_PAGINATION.replace(default_page_size=10, max_page_size=10)


class AllTicketView(PagedTicketView):
    pagination = None


class ExportTicketView(PagedTicketView):
    pagination = fr.NoPagination().replace(envelope=fr.views.Envelope)


class MappedTicketView(PagedTicketView):
    # Restly reads exclude_routes more than once, and a map() can be read only
    # once. mypy's warn_unused_ignores fails here if the type starts to accept it.
    exclude_routes = map(fr.ViewRoute, ("delete_endpoint",))  # type: ignore[assignment]
