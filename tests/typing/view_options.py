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
    delete_endpoint = None
    dependencies: ClassVar[list[Any]] = [Depends(lambda: None)]


@fr.include_view(app)
class TicketView(TicketBase):
    prefix = "/tickets"


APP_PAGINATION = fr.NumberedPagination(page_size_query_param="size", max_page_size=100)


class PagedTicketView(fr.AsyncRestView):
    model = Ticket
    schema = TicketSchema
    # annotated, so mypy lets a subclass choose another style
    pagination: ClassVar[fr.Pagination | None] = APP_PAGINATION


class SmallPagedTicketView(PagedTicketView):
    pagination = APP_PAGINATION.replace(default_page_size=10, max_page_size=10)


class AllTicketView(PagedTicketView):
    pagination = None


class ExportTicketView(PagedTicketView):
    pagination = fr.NoPagination().replace(envelope=fr.views.Envelope)


class OffsetTicketView(PagedTicketView):
    pagination = fr.OffsetPagination(offset_query_param="skip").replace(
        default_limit=20, max_limit=100
    )


class TicketSearch(fr.query.OffsetListParams):
    q: str | None = None


def read_the_fields(search: TicketSearch) -> int:
    return search.limit + search.offset


@fr.include_view(app)
class PublicTicketView(TicketBase):
    prefix = "/public-tickets"
    create_endpoint = None
    update_endpoint = None
    delete_endpoint = None
