"""Tests for the list response envelope and pagination metadata."""

import pytest
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


def test_paginated_view_rejects_out_of_range_default_page_size(client):
    """A paginated view whose ``default_page_size`` falls outside
    ``[1, max_page_size]`` (e.g. ``0``) is rejected at registration rather than
    silently returning empty pages."""

    class Gizmo(fr.IDBase):
        name: Mapped[str]

    class GizmoSchema(fr.IDSchema):
        name: str

    with pytest.raises(ValueError, match="set 'paginated = False'"):

        @fr.include_view(client.app)
        class GizmoView(fr.AsyncRestView):
            prefix = "/gizmos"
            model = Gizmo
            schema = GizmoSchema
            default_page_size = 0


def test_paginated_view_rejects_none_default_page_size(client):
    """``default_page_size = None`` (the pre-envelope "no cap" idiom) now raises
    at registration instead of 500ing on the first list request."""

    class Doohickey(fr.IDBase):
        name: Mapped[str]

    class DoohickeySchema(fr.IDSchema):
        name: str

    with pytest.raises(ValueError, match="set 'paginated = False'"):

        @fr.include_view(client.app)
        class DoohickeyView(fr.AsyncRestView):
            prefix = "/doohickeys"
            model = Doohickey
            schema = DoohickeySchema
            default_page_size = None  # type: ignore[assignment]


def test_unpaginated_view_ignores_default_page_size(client):
    """``default_page_size`` is unused when ``paginated = False``, so an
    otherwise-invalid value must not trip the guard."""

    class Sprocket(fr.IDBase):
        name: Mapped[str]

    class SprocketSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class SprocketView(fr.AsyncRestView):
        prefix = "/sprockets"
        model = Sprocket
        schema = SprocketSchema
        paginated = False
        default_page_size = 0  # unused; must not raise

    create_tables()

    response = client.get("/sprockets/")
    assert response.status_code == 200
    assert set(response.json()) == {"data"}


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


def test_openapi_list_route_refs_the_envelope_component(client):
    """The list route's 200 response references the generated envelope component
    (``PaginatedEnvelope_<Schema>_`` when paginated, ``Envelope_<Schema>_`` when
    not), wrapping the response schema in ``data``. This pins the public OpenAPI
    contract the docs promise Restly keeps in sync."""

    class Crate(fr.IDBase):
        name: Mapped[str]

    class CrateSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class CrateView(fr.AsyncRestView):
        prefix = "/crates"
        model = Crate
        schema = CrateSchema

    class Barrel(fr.IDBase):
        name: Mapped[str]

    class BarrelSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class BarrelView(fr.AsyncRestView):
        prefix = "/barrels"
        model = Barrel
        schema = BarrelSchema
        paginated = False

    create_tables()
    spec = client.app.openapi()

    def list_200_component(prefix):
        for candidate in (prefix, prefix + "/"):
            route = spec["paths"].get(candidate, {}).get("get")
            if route is not None:
                ref = route["responses"]["200"]["content"]["application/json"][
                    "schema"
                ]["$ref"]
                return ref.removeprefix("#/components/schemas/")
        raise AssertionError(f"no list GET registered for {prefix}")

    components = spec["components"]["schemas"]

    # Paginated (default): PaginatedEnvelope wrapping the schema, all fields required.
    paginated = list_200_component("/crates")
    assert paginated == "PaginatedEnvelope_CrateSchema_"
    paginated_schema = components[paginated]
    assert paginated_schema["properties"]["data"]["type"] == "array"
    assert (
        paginated_schema["properties"]["data"]["items"]["$ref"]
        == "#/components/schemas/CrateSchema"
    )
    assert set(paginated_schema["required"]) == {
        "data",
        "total_count",
        "page",
        "page_size",
        "total_pages",
    }

    # Unpaginated: plain Envelope, only ``data``.
    unpaginated = list_200_component("/barrels")
    assert unpaginated == "Envelope_BarrelSchema_"
    unpaginated_schema = components[unpaginated]
    assert set(unpaginated_schema["properties"]) == {"data"}
    assert unpaginated_schema["required"] == ["data"]
