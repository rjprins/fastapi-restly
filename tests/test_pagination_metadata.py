"""Tests for optional pagination metadata responses."""

import fastapi_restly as fr

from .conftest import create_tables


def test_index_response_defaults_to_plain_list(client):
    class Product(fr.IDBase):
        name: str

    class ProductSchema(fr.IDSchema[Product]):
        name: str

    @fr.include_view(client.app)
    class ProductView(fr.AsyncAlchemyView):
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

    class ProductWithMetaSchema(fr.IDSchema[ProductWithMeta]):
        name: str

    @fr.include_view(client.app)
    class ProductView(fr.AsyncAlchemyView):
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
