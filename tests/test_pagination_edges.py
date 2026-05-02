"""Tests for pagination edge cases (I1).

Pins down behavior for:
  - empty result set
  - negative limit / offset
  - zero limit
  - offset past end of dataset
  - very large limit (cap or no cap?)
  - V2 page out of range, page=0, etc.
"""

from sqlalchemy.orm import Mapped

import fastapi_restly as fr

from .conftest import create_tables


def _setup_v1_view(client, *, include_metadata: bool = False):
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


def _setup_v2_view(client, *, include_metadata: bool = False):
    class WidgetV2(fr.IDBase):
        name: Mapped[str]

    class WidgetV2Schema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class WidgetV2View(fr.AsyncRestView):
        prefix = "/widgets-v2"
        model = WidgetV2
        schema = WidgetV2Schema
        include_pagination_metadata = include_metadata
        query_modifier_version = fr.QueryModifierVersion.V2

    create_tables()
    return WidgetV2, WidgetV2Schema


# ---------------------------------------------------------------------------
# Empty result set
# ---------------------------------------------------------------------------


def test_v1_empty_result_returns_empty_list(client):
    _setup_v1_view(client)

    response = client.get("/widgets/")
    assert response.status_code == 200
    assert response.json() == []


def test_v1_empty_result_with_metadata(client):
    """Empty list + pagination metadata should still produce a well-formed envelope."""
    _setup_v1_view(client, include_metadata=True)

    response = client.get("/widgets/")
    assert response.status_code == 200
    payload = response.json()

    assert payload["total"] == 0
    assert payload["items"] == []
    # V1 has no implicit page; only limit/offset.
    assert payload["page"] is None
    assert payload["page_size"] is None
    assert payload["total_pages"] is None


def test_v2_empty_result_with_metadata(client):
    """V2 with empty data: total=0, page=1, total_pages should be 0 (no pages of data)."""
    _setup_v2_view(client, include_metadata=True)

    response = client.get("/widgets-v2/")
    assert response.status_code == 200
    payload = response.json()

    assert payload["total"] == 0
    assert payload["items"] == []
    assert payload["page"] == 1
    assert payload["page_size"] == fr.query.DEFAULT_PAGE_SIZE
    # total_pages is the ceiling of total/page_size; 0/N = 0.
    assert payload["total_pages"] == 0


# ---------------------------------------------------------------------------
# Negative limit / offset (V1)
# ---------------------------------------------------------------------------


def test_v1_negative_limit_returns_422(client):
    """Negative limit is rejected by Pydantic with a standard 422 (single error
    format for both negative and non-integer cases)."""
    _setup_v1_view(client)
    client.post("/widgets/", json={"name": "A"})

    response = client.get("/widgets/?limit=-5", assert_status_code=422)
    assert response.status_code == 422
    body = response.json()
    # FastAPI 422 envelope has a list of validation errors; the limit field
    # should be referenced among them.
    assert any("limit" in str(err).lower() for err in body.get("detail", []))


def test_v1_negative_offset_returns_422(client):
    """Negative offset is rejected by Pydantic with a standard 422."""
    _setup_v1_view(client)
    client.post("/widgets/", json={"name": "A"})

    response = client.get("/widgets/?offset=-1", assert_status_code=422)
    assert response.status_code == 422
    body = response.json()
    assert any("offset" in str(err).lower() for err in body.get("detail", []))


def test_v1_non_integer_limit_returns_422(client):
    """Non-integer limit is caught at the Pydantic Query validation layer.

    Together with negative-value rejection (also 422), this gives a single,
    consistent error format for all pagination errors.
    """
    _setup_v1_view(client)

    response = client.get("/widgets/?limit=abc", assert_status_code=422)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Zero limit (V1)
# ---------------------------------------------------------------------------


def test_v1_zero_limit_returns_empty_list(client):
    """limit=0 is allowed and yields an empty list (current contract).

    SQLite's LIMIT 0 returns zero rows; the framework does not special-case 0.
    """
    _setup_v1_view(client)
    client.post("/widgets/", json={"name": "A"})
    client.post("/widgets/", json={"name": "B"})

    response = client.get("/widgets/?limit=0")
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Offset past end of dataset
# ---------------------------------------------------------------------------


def test_v1_offset_past_end_returns_empty_list_not_404(client):
    """Skipping past the data should return an empty list, never 404."""
    _setup_v1_view(client)
    client.post("/widgets/", json={"name": "A"})
    client.post("/widgets/", json={"name": "B"})

    response = client.get("/widgets/?offset=100")
    assert response.status_code == 200
    assert response.json() == []


def test_v1_offset_past_end_with_metadata(client):
    _setup_v1_view(client, include_metadata=True)
    client.post("/widgets/", json={"name": "A"})

    response = client.get("/widgets/?offset=50&limit=10")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"] == []
    assert payload["limit"] == 10
    assert payload["offset"] == 50


# ---------------------------------------------------------------------------
# Very large limit
# ---------------------------------------------------------------------------


def test_v1_very_large_limit_is_capped_at_max(client):
    """V1 caps ``limit`` at :data:`fr.query.MAX_LIMIT`. Anything above is 422."""
    _setup_v1_view(client, include_metadata=True)
    for i in range(5):
        client.post("/widgets/", json={"name": f"W{i}"})

    response = client.get(
        f"/widgets/?limit={fr.query.MAX_LIMIT + 1}", assert_status_code=422
    )
    assert response.status_code == 422

    # The maximum value is accepted.
    response = client.get(f"/widgets/?limit={fr.query.MAX_LIMIT}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert len(payload["items"]) == 5


def test_v1_per_view_max_limit_override_propagates_to_schema(client):
    """A subclass that bumps ``max_limit`` should accept higher values."""

    class BigWidget(fr.IDBase):
        name: Mapped[str]

    @fr.include_view(client.app)
    class BigWidgetView(fr.AsyncRestView):
        prefix = "/big-widgets"
        model = BigWidget
        max_limit = 5000

    create_tables()

    # Schema reflects the override.
    limit_field = BigWidgetView.index_param_schema.model_fields["limit"]
    le_meta = next(m for m in limit_field.metadata if hasattr(m, "le"))
    assert le_meta.le == 5000

    # Values above the framework default but below the per-view max pass.
    response = client.get(f"/big-widgets/?limit={fr.query.MAX_LIMIT + 100}")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# V2 page-based pagination edges
# ---------------------------------------------------------------------------


def test_v2_page_zero_returns_422(client):
    """``page=0`` is rejected by the Pydantic schema (``ge=1``) with a 422.

    Previously this slipped through ``_get_int_v2(...) or 1`` and produced
    bogus negative offsets in the metadata payload.
    """
    _setup_v2_view(client, include_metadata=True)
    client.post("/widgets-v2/", json={"name": "A"})

    response = client.get("/widgets-v2/?page=0", assert_status_code=422)
    assert response.status_code == 422
    body = response.json()
    assert any("page" in str(err).lower() for err in body.get("detail", []))


def test_v2_negative_page_returns_422(client):
    """``page=-1`` is rejected by the Pydantic schema (``ge=1``) with a 422."""
    _setup_v2_view(client, include_metadata=True)

    response = client.get("/widgets-v2/?page=-1", assert_status_code=422)
    assert response.status_code == 422


def test_v2_page_size_zero_returns_422(client):
    """``page_size=0`` is rejected by the Pydantic schema (``ge=1``) with a 422.

    Previously this was silently coerced to the default page size in the
    SQL while the metadata echoed the bogus zero value.
    """
    _setup_v2_view(client, include_metadata=True)
    client.post("/widgets-v2/", json={"name": "A"})

    response = client.get("/widgets-v2/?page_size=0", assert_status_code=422)
    assert response.status_code == 422
    body = response.json()
    assert any("page_size" in str(err).lower() for err in body.get("detail", []))


def test_v2_negative_page_size_returns_422(client):
    """Negative ``page_size`` is rejected by the Pydantic schema with a 422."""
    _setup_v2_view(client, include_metadata=True)

    response = client.get("/widgets-v2/?page_size=-10", assert_status_code=422)
    assert response.status_code == 422


def test_v2_page_out_of_range_returns_empty_items(client):
    """Past last page returns empty items but the metadata still describes the
    requested page (not the last one). Pin this contract."""
    _setup_v2_view(client, include_metadata=True)
    for name in ["A", "B", "C", "D"]:
        client.post("/widgets-v2/", json={"name": name})

    response = client.get("/widgets-v2/?page=999&page_size=10")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["items"] == []
    assert payload["page"] == 999
    assert payload["page_size"] == 10
    assert payload["total_pages"] == 1


def test_v2_very_large_page_size_is_capped_at_max(client):
    """V2 caps ``page_size`` at :data:`fr.query.MAX_PAGE_SIZE`. Anything above is 422."""
    _setup_v2_view(client, include_metadata=True)
    for i in range(3):
        client.post("/widgets-v2/", json={"name": f"X{i}"})

    response = client.get(
        f"/widgets-v2/?page_size={fr.query.MAX_PAGE_SIZE + 1}", assert_status_code=422
    )
    assert response.status_code == 422
    body = response.json()
    assert any("page_size" in str(err).lower() for err in body.get("detail", []))


def test_v2_per_view_max_page_size_override_propagates_to_schema(client):
    """A subclass that bumps ``max_page_size`` should accept higher values."""

    class BigItem(fr.IDBase):
        name: Mapped[str]

    @fr.include_view(client.app)
    class BigItemView(fr.AsyncRestView):
        prefix = "/big-items"
        model = BigItem
        query_modifier_version = fr.QueryModifierVersion.V2
        max_page_size = 5000
        default_page_size = 500

    create_tables()

    page_size_field = BigItemView.index_param_schema.model_fields["page_size"]
    le_meta = next(m for m in page_size_field.metadata if hasattr(m, "le"))
    assert le_meta.le == 5000
    assert page_size_field.default == 500

    # Values above the framework default but below the per-view max pass.
    response = client.get(f"/big-items/?page_size={fr.query.MAX_PAGE_SIZE + 100}")
    assert response.status_code == 200
