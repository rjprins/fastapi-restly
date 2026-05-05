"""Tests for pagination edge cases.

Pins down behavior for:
  - empty result set
  - page out of range, page=0, etc.
  - page_size bounds and per-view overrides
"""

from sqlalchemy.orm import Mapped

import fastapi_restly as fr

from .conftest import create_tables


def _setup_view(client, *, include_metadata: bool = False):
    class Widget(fr.IDBase):
        name: Mapped[str]

    class WidgetSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class WidgetView(fr.AsyncRestView):
        prefix = "/widgets"
        model = Widget
        schema = WidgetSchema
        include_pagination_metadata = include_metadata

    create_tables()
    return Widget, WidgetSchema


# ---------------------------------------------------------------------------
# Empty result set
# ---------------------------------------------------------------------------


def test_empty_result_returns_empty_list(client):
    _setup_view(client)

    response = client.get("/widgets/")
    assert response.status_code == 200
    assert response.json() == []


def test_empty_result_with_metadata_unlimited(client):
    """Default behaviour: no implicit page size — metadata leaves page* keys None."""
    _setup_view(client, include_metadata=True)

    response = client.get("/widgets/")
    assert response.status_code == 200
    payload = response.json()

    assert payload["total"] == 0
    assert payload["items"] == []
    assert payload["page"] is None
    assert payload["page_size"] is None
    assert payload["total_pages"] is None


def test_empty_result_with_metadata_explicit_page_size(client):
    """When ``page_size`` is provided explicitly the metadata is populated."""
    _setup_view(client, include_metadata=True)

    response = client.get("/widgets/?page_size=10")
    assert response.status_code == 200
    payload = response.json()

    assert payload["total"] == 0
    assert payload["items"] == []
    assert payload["page"] == 1
    assert payload["page_size"] == 10
    # total_pages is the ceiling of total/page_size; 0/N = 0.
    assert payload["total_pages"] == 0


# ---------------------------------------------------------------------------
# Page bounds
# ---------------------------------------------------------------------------


def test_page_zero_returns_422(client):
    """``page=0`` is rejected by the Pydantic schema (``ge=1``) with a 422."""
    _setup_view(client, include_metadata=True)
    client.post("/widgets/", json={"name": "A"})

    response = client.get("/widgets/?page=0", assert_status_code=422)
    assert response.status_code == 422
    body = response.json()
    assert any("page" in str(err).lower() for err in body.get("detail", []))


def test_negative_page_returns_422(client):
    """``page=-1`` is rejected by the Pydantic schema (``ge=1``) with a 422."""
    _setup_view(client, include_metadata=True)

    response = client.get("/widgets/?page=-1", assert_status_code=422)
    assert response.status_code == 422


def test_page_size_zero_returns_422(client):
    """``page_size=0`` is rejected by the Pydantic schema (``ge=1``) with a 422."""
    _setup_view(client, include_metadata=True)
    client.post("/widgets/", json={"name": "A"})

    response = client.get("/widgets/?page_size=0", assert_status_code=422)
    assert response.status_code == 422
    body = response.json()
    assert any("page_size" in str(err).lower() for err in body.get("detail", []))


def test_negative_page_size_returns_422(client):
    """Negative ``page_size`` is rejected by the Pydantic schema with a 422."""
    _setup_view(client, include_metadata=True)

    response = client.get("/widgets/?page_size=-10", assert_status_code=422)
    assert response.status_code == 422


def test_page_out_of_range_returns_empty_items(client):
    """Past last page returns empty items but the metadata still describes the
    requested page (not the last one). Pin this contract."""
    _setup_view(client, include_metadata=True)
    for name in ["A", "B", "C", "D"]:
        client.post("/widgets/", json={"name": name})

    response = client.get("/widgets/?page=999&page_size=10")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["items"] == []
    assert payload["page"] == 999
    assert payload["page_size"] == 10
    assert payload["total_pages"] == 1


def test_very_large_page_size_is_capped_at_max(client):
    """``page_size`` is capped at :data:`fr.query.MAX_PAGE_SIZE`. Anything above is 422."""
    _setup_view(client, include_metadata=True)
    for i in range(3):
        client.post("/widgets/", json={"name": f"X{i}"})

    response = client.get(
        f"/widgets/?page_size={fr.query.MAX_PAGE_SIZE + 1}", assert_status_code=422
    )
    assert response.status_code == 422
    body = response.json()
    assert any("page_size" in str(err).lower() for err in body.get("detail", []))


def test_per_view_max_page_size_override_propagates_to_schema(client):
    """A subclass that bumps ``max_page_size`` should accept higher values."""

    class BigItem(fr.IDBase):
        name: Mapped[str]

    @fr.include_view(client.app)
    class BigItemView(fr.AsyncRestView):
        prefix = "/big-items"
        model = BigItem
        max_page_size = 5000
        default_page_size = 500

    create_tables()

    page_size_field = BigItemView.listing_param_schema.model_fields["page_size"]
    le_meta = next(m for m in page_size_field.metadata if hasattr(m, "le"))
    assert le_meta.le == 5000
    assert page_size_field.default == 500

    # Values above the framework default but below the per-view max pass.
    response = client.get(f"/big-items/?page_size={fr.query.MAX_PAGE_SIZE + 100}")
    assert response.status_code == 200
