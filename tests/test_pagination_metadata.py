"""Tests for the list response envelope and pagination metadata."""

from sqlalchemy.orm import Mapped

import fastapi_restly as fr

from .conftest import create_tables


def test_index_response_defaults_to_paginated_envelope(client):
    """A view paginates by default: the list is wrapped in the ``data``
    envelope with pagination metadata."""

    class Product(fr.IDBase):
        name: Mapped[str]

    class ProductSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class ProductView(fr.AsyncRestView):
        prefix = "/products"
        model = Product
        schema = ProductSchema

    create_tables()

    client.post("/products/", json={"name": "A"})
    client.post("/products/", json={"name": "B"})

    response = client.get("/products/")
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["data"], list)
    assert len(payload["data"]) == 2
    assert payload["total_count"] == 2
    assert payload["page"] == 1
    assert payload["page_size"] == fr.query.DEFAULT_PAGE_SIZE
    assert payload["total_pages"] == 1


def test_unpaginated_view_returns_data_envelope_without_metadata(client):
    """``paginated = False`` returns every row in a plain ``{"data": [...]}``
    envelope -- no pagination metadata, and no ``page``/``page_size`` params."""

    class Widget(fr.IDBase):
        name: Mapped[str]

    class WidgetSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class WidgetView(fr.AsyncRestView):
        prefix = "/widgets"
        model = Widget
        schema = WidgetSchema
        paginated = False

    create_tables()

    for i in range(30):
        client.post("/widgets/", json={"name": f"Item {i}"})

    response = client.get("/widgets/")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["data"]) == 30
    assert set(payload) == {"data"}  # no total_count/page/page_size/total_pages

    # page_size is not a parameter on an unpaginated view.
    rejected = client.get("/widgets/?page_size=5", assert_status_code=422)
    locs = [item.get("loc") for item in rejected.json().get("detail", [])]
    assert ["query", "page_size"] in locs


def test_default_page_size_caps_a_large_result_set(client):
    """Omitting ``page_size`` caps at ``default_page_size`` rather than
    returning the whole table."""

    class PaginatedItem(fr.IDBase):
        name: Mapped[str]

    class PaginatedItemSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class PaginatedItemView(fr.AsyncRestView):
        prefix = "/paginated-items"
        model = PaginatedItem
        schema = PaginatedItemSchema
        default_page_size = 10

    create_tables()

    total_items = 30
    for i in range(total_items):
        client.post("/paginated-items/", json={"name": f"Item {i}"})

    response = client.get("/paginated-items/")
    payload = response.json()

    assert payload["total_count"] == total_items
    assert payload["page"] == 1
    assert payload["page_size"] == 10
    assert payload["total_pages"] == 3
    assert len(payload["data"]) == 10


def test_pagination_metadata_reports_explicit_page_size(client):
    """When the client passes ``page_size`` the metadata reflects it."""

    class PaginatedThing(fr.IDBase):
        name: Mapped[str]

    class PaginatedThingSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class PaginatedThingView(fr.AsyncRestView):
        prefix = "/paginated-things"
        model = PaginatedThing
        schema = PaginatedThingSchema

    create_tables()

    for i in range(7):
        client.post("/paginated-things/", json={"name": f"Item {i}"})

    response = client.get("/paginated-things/?page_size=3&page=2")
    payload = response.json()

    assert payload["total_count"] == 7
    assert payload["page"] == 2
    assert payload["page_size"] == 3
    assert payload["total_pages"] == 3
    assert len(payload["data"]) == 3
