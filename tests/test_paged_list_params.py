"""``fr.query.PagedListParams``: list params of your own with pages.

A view fills the two page fields from its ``pagination`` when it registers:
the names in the URL, the default page size, the bounds and the offset
check. List params of your own without page fields fail on a view with
pages, because its list would return every row while its response says that
it has pages.
"""

from typing import Any

import pydantic
import pytest
import sqlalchemy
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError

from ._models import SharedBase
from .conftest import create_tables


class Search(fr.query.PagedListParams):
    q: str | None = None


def _item_model() -> type[Any]:
    class Item(fr.IDMixin, SharedBase):
        name: Mapped[str]

    return Item


def _add_items(client: Any, count: int) -> None:
    create_tables()
    for number in range(count):
        client.post("/items", json={"name": f"item {number:03}"})


def test_the_view_pages_with_the_page_fields(client):
    """Restly's own apply_list_params applies the page, and the response
    tells the client what it got. The view keeps the class it declares."""
    item = _item_model()

    @fr.include_view(client.app)
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = item
        schema_list_params = Search

    _add_items(client, 60)

    first = client.get("/items").json()
    assert len(first["data"]) == 50
    assert {key: first[key] for key in first if key != "data"} == {
        "total_count": 60,
        "page": 1,
        "page_size": 50,
        "total_pages": 2,
    }
    second = client.get("/items", params={"page": "2", "page_size": "25"}).json()
    assert [row["name"] for row in second["data"]][0] == "item 025"
    assert ItemView.schema_list_params is Search


def test_the_page_fields_follow_the_pagination_of_the_view(client):
    """A renamed page size, its default and its bounds come from the view's
    pagination. Code reads ``list_params.page_size`` whatever the URL calls
    it."""
    item = _item_model()
    seen: list[int] = []

    @fr.include_view(client.app)
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = item
        schema_list_params = Search
        pagination = fr.NumberedPagination(
            page_size_query_param="size", default_page_size=25, max_page_size=100
        )

        async def apply_filters(self, stmt, list_params: Search):
            seen.append(list_params.page_size)
            return stmt

    _add_items(client, 30)

    assert len(client.get("/items").json()["data"]) == 25
    body = client.get("/items", params={"size": "10"}).json()
    assert (len(body["data"]), body["page_size"]) == (10, 10)
    assert seen == [25, 10]
    for params in ({"page_size": "10"}, {"size": "500"}):
        client.get("/items", params=params, assert_status_code=422)

    parameters = client.get("/openapi.json").json()["paths"]["/items"]["get"]
    size = next(p for p in parameters["parameters"] if p["name"] == "size")
    assert size["schema"]["maximum"] == 100
    assert size["schema"]["default"] == 25


def test_one_class_serves_views_with_other_pages(client):
    item = _item_model()

    class BaseItemView(fr.AsyncRestView):
        model = item
        schema_list_params = Search

    @fr.include_view(client.app)
    class ItemView(BaseItemView):
        prefix = "/items"

    @fr.include_view(client.app)
    class LogView(BaseItemView):
        prefix = "/log"
        pagination = fr.NumberedPagination(default_page_size=10)

    _add_items(client, 30)

    assert len(client.get("/items").json()["data"]) == 30
    assert len(client.get("/log").json()["data"]) == 10


def test_a_page_beyond_what_a_database_takes_is_a_client_error(client):
    item = _item_model()

    @fr.include_view(client.app)
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = item
        schema_list_params = Search
        pagination = fr.NumberedPagination(page_size_query_param="size")

    create_tables()
    response = client.get(
        "/items",
        params={"page": "100000000000000000", "size": "100"},
        assert_status_code=422,
    )
    assert response.json()["detail"][0]["msg"] == (
        "Value error, page and size produce an offset above 9223372036854775807"
    )


def test_outside_a_view_the_fields_keep_their_defaults():
    assert (Search().page, Search().page_size) == (1, fr.query.DEFAULT_PAGE_SIZE)
    with pytest.raises(pydantic.ValidationError):
        Search(page_size=fr.query.MAX_PAGE_SIZE + 1)
    with pytest.raises(pydantic.ValidationError):
        Search(page=10**17, page_size=1000)


def test_list_params_of_your_own_without_page_fields_fail_on_a_view_with_pages():
    item = _item_model()

    class NoPages(pydantic.BaseModel):
        q: str | None = None

    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = item
        schema_list_params = NoPages

    with pytest.raises(RestlyConfigurationError) as caught:
        fr.include_view(FastAPI(), ItemView)
    assert str(caught.value) == (
        "ItemView.schema_list_params has no 'page' and no 'page_size' field, "
        "so the list would return every row while its response says that it "
        "has pages. Subclass fr.query.PagedListParams, which gets the page "
        "fields of the view's pagination, or set pagination = None for a list "
        "without pages."
    )


def test_page_fields_written_by_hand_must_have_the_names_of_the_pagination():
    item = _item_model()

    class OwnPages(pydantic.BaseModel):
        page: int = 1
        page_size: int = 20

    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = item
        schema_list_params = OwnPages

    fr.include_view(FastAPI(), ItemView)

    class SizedView(ItemView):
        pagination = fr.NumberedPagination(page_size_query_param="size")

    with pytest.raises(RestlyConfigurationError, match="has no 'size' field"):
        fr.include_view(FastAPI(), SizedView)


def test_paged_list_params_need_a_view_with_pages():
    item = _item_model()

    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = item
        schema_list_params = Search
        pagination = None

    with pytest.raises(RestlyConfigurationError, match="the view has no pages"):
        fr.include_view(FastAPI(), ItemView)


def test_an_override_reads_the_page_from_the_list_params(client):
    """An override of apply_list_params applies the page itself, with the
    primary key last, as the docs show."""
    item = _item_model()

    @fr.include_view(client.app)
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = item
        schema_list_params = Search

        async def apply_list_params(self, stmt, list_params: Search):
            stmt = stmt.order_by(sqlalchemy.desc(item.name))
            stmt = fr.query.order_by_primary_key(stmt, self.model)
            offset = (list_params.page - 1) * list_params.page_size
            return stmt.limit(list_params.page_size).offset(offset)

    _add_items(client, 5)

    body = client.get("/items", params={"page": "2", "page_size": "2"}).json()
    assert [row["name"] for row in body["data"]] == ["item 002", "item 001"]
    assert body["total_pages"] == 3
