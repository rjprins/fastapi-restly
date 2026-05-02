"""Tests for optional pagination metadata responses."""

import fastapi_restly as fr

from .conftest import create_tables


def test_index_response_defaults_to_plain_list(client):
    class Product(fr.IDBase):
        name: str

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
    assert isinstance(payload, list)
    assert len(payload) == 2


def test_index_can_return_pagination_metadata(client):
    class ProductWithMeta(fr.IDBase):
        name: str

    class ProductWithMetaSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class ProductView(fr.AsyncRestView):
        prefix = "/products"
        model = ProductWithMeta
        schema = ProductWithMetaSchema
        include_pagination_metadata = True

    create_tables()

    for name in ["A", "B", "C", "D"]:
        client.post("/products/", json={"name": name})

    response = client.get("/products/?limit=2&offset=1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["limit"] == 2
    assert payload["offset"] == 1
    assert len(payload["items"]) == 2
    assert payload["page"] is None
    assert payload["page_size"] is None
    assert payload["total_pages"] is None


def test_v2_pagination_metadata_returns_all_items_when_no_page_size(client):
    """V2 default is unlimited: omitting ``page_size`` returns every row."""

    class PaginatedItem(fr.IDBase):
        name: str

    class PaginatedItemSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class PaginatedItemView(fr.AsyncRestView):
        prefix = "/paginated-items"
        model = PaginatedItem
        schema = PaginatedItemSchema
        include_pagination_metadata = True
        query_modifier_version = fr.QueryModifierVersion.V2

    create_tables()

    total_items = 30
    for i in range(total_items):
        client.post("/paginated-items/", json={"name": f"Item {i}"})

    response = client.get("/paginated-items/")
    payload = response.json()

    assert payload["total"] == total_items
    assert payload["page"] is None
    assert payload["page_size"] is None
    assert payload["total_pages"] is None
    assert payload["limit"] is None
    assert payload["offset"] is None
    assert len(payload["items"]) == total_items


def test_v2_pagination_metadata_reports_explicit_page_size(client):
    """When the client passes ``page_size`` the metadata reflects it."""

    class PaginatedThing(fr.IDBase):
        name: str

    class PaginatedThingSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class PaginatedThingView(fr.AsyncRestView):
        prefix = "/paginated-things"
        model = PaginatedThing
        schema = PaginatedThingSchema
        include_pagination_metadata = True
        query_modifier_version = fr.QueryModifierVersion.V2

    create_tables()

    for i in range(7):
        client.post("/paginated-things/", json={"name": f"Item {i}"})

    response = client.get("/paginated-things/?page_size=3&page=2")
    payload = response.json()

    assert payload["total"] == 7
    assert payload["page"] == 2
    assert payload["page_size"] == 3
    assert payload["total_pages"] == 3
    assert payload["limit"] == 3
    assert payload["offset"] == 3
    assert len(payload["items"]) == 3
