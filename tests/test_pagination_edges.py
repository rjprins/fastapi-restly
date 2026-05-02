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
    assert payload["page_size"] == 100
    # total_pages is the ceiling of total/page_size; 0/100 = 0.
    assert payload["total_pages"] == 0


# ---------------------------------------------------------------------------
# Negative limit / offset (V1)
# ---------------------------------------------------------------------------


def test_v1_negative_limit_returns_400(client):
    """Negative limit must be rejected — pin behavior at 400 (current contract)."""
    _setup_v1_view(client)
    client.post("/widgets/", json={"name": "A"})

    response = client.get("/widgets/?limit=-5", assert_status_code=400)
    assert response.status_code == 400
    assert "limit" in response.json()["detail"].lower()


def test_v1_negative_offset_returns_400(client):
    _setup_v1_view(client)
    client.post("/widgets/", json={"name": "A"})

    response = client.get("/widgets/?offset=-1", assert_status_code=400)
    assert response.status_code == 400
    assert "offset" in response.json()["detail"].lower()


def test_v1_non_integer_limit_returns_422(client):
    """Non-integer limit is caught at the Pydantic Query validation layer first
    (the dedicated query-param schema), so this returns 422 not 400. The 400
    code from _get_int still fires when the lower-level apply_pagination is
    called directly with raw QueryParams (covered in test_query_modifiers.py).
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


def test_v1_very_large_limit_is_not_capped(client):
    """Document current behavior: V1 does NOT cap the limit.

    If the framework later adds a cap, this test will fail and force a docs
    update — that is intentional.
    """
    _setup_v1_view(client, include_metadata=True)
    for i in range(5):
        client.post("/widgets/", json={"name": f"W{i}"})

    response = client.get("/widgets/?limit=1000000")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert payload["limit"] == 1000000
    assert len(payload["items"]) == 5


# ---------------------------------------------------------------------------
# V2 page-based pagination edges
# ---------------------------------------------------------------------------


def test_v2_page_zero_silently_treated_as_one_in_query(client):
    """KNOWN ISSUE: page=0 is treated as falsy by `_get_int_v2(...) or 1`,
    so the SQL query runs with page=1 (returning data) — but the metadata
    payload echoes the user's page=0 and computes offset=-100.

    The current behavior is inconsistent: the validation `page < 1` never
    fires for `page=0`, and the metadata reports a negative offset. This
    test pins the buggy behavior so a fix will deliberately break it.
    """
    _setup_v2_view(client, include_metadata=True)
    client.post("/widgets-v2/", json={"name": "A"})

    response = client.get("/widgets-v2/?page=0")
    assert response.status_code == 200
    payload = response.json()
    # Data comes back because the query treated 0 as 1
    assert payload["total"] == 1
    assert len(payload["items"]) == 1
    # But metadata echoes the bogus user input
    assert payload["page"] == 0
    assert payload["offset"] == -100


def test_v2_negative_page_returns_400(client):
    """page=-1 IS truthy so the `page < 1` validation fires."""
    _setup_v2_view(client, include_metadata=True)

    response = client.get("/widgets-v2/?page=-1", assert_status_code=400)
    assert response.status_code == 400


def test_v2_page_size_zero_silently_treated_as_one_hundred(client):
    """KNOWN ISSUE: same issue as page=0 — `page_size=0 or 100` gives 100
    in the SQL but the metadata echoes `page_size=0` (and thus
    `total_pages=0` from the divide-by-zero guard). Pin behavior."""
    _setup_v2_view(client, include_metadata=True)
    client.post("/widgets-v2/", json={"name": "A"})

    response = client.get("/widgets-v2/?page_size=0")
    assert response.status_code == 200
    payload = response.json()
    # Items come back: query used the default 100
    assert payload["total"] == 1
    assert len(payload["items"]) == 1
    # Metadata echoes 0
    assert payload["page_size"] == 0
    assert payload["total_pages"] == 0


def test_v2_negative_page_size_returns_400(client):
    """Negative page_size IS truthy so the `page_size <= 0` check fires."""
    _setup_v2_view(client, include_metadata=True)

    response = client.get("/widgets-v2/?page_size=-10", assert_status_code=400)
    assert response.status_code == 400


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


def test_v2_very_large_page_size_is_not_capped(client):
    """V2 does NOT cap page_size. Pin this so any future cap update breaks
    the test deliberately."""
    _setup_v2_view(client, include_metadata=True)
    for i in range(3):
        client.post("/widgets-v2/", json={"name": f"X{i}"})

    response = client.get("/widgets-v2/?page_size=1000000")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["page_size"] == 1000000
    assert len(payload["items"]) == 3
