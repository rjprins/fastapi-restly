"""End-to-end tests for V1 and V2 query modifiers driven through FastAPI.

These tests exercise the full Starlette -> FastAPI Pydantic-validation ->
``apply_query_modifiers*`` pipeline with a real registered view. The
existing ``tests/test_query_modifiers*.py`` suites build ``QueryParams`` by
hand, which bypasses the schema-validation layer where bugs B2.1 and B2.4
originally lived.

What they pin in place:

* B2.1 — V1 documented operators (``>=``, ``<=``, ``null``/``!null``,
  comma-separated OR lists) work on int and datetime columns. Previously
  the V1 schema typed each ``filter[...]`` as the column's true Python
  type, so Pydantic 422'd every operator-prefixed value before the
  downstream parser ever ran.
* B2.4 — Repeated query parameters are preserved as multiple AND'd
  predicates rather than silently collapsed to one. Affects both V1
  (``filter[created_at]=>=...&filter[created_at]=<...``) and V2
  (``name__contains=hi&name__contains=ho``).
"""

from datetime import datetime

import pytest
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly.query import QueryModifierVersion, set_query_modifier_version

from .conftest import create_tables


class _PersonSchema(fr.IDSchema):
    name: str
    age: int
    created_at: datetime
    deleted_at: datetime | None = None


# ---------------------------------------------------------------------------
# V1 end-to-end
# ---------------------------------------------------------------------------


@pytest.fixture
def v1_client(client):
    """Register a ``Person`` view on the V1 query interface and seed it."""
    set_query_modifier_version(QueryModifierVersion.V1)

    class PersonV1(fr.IDBase):
        name: Mapped[str]
        age: Mapped[int]
        created_at: Mapped[datetime] = mapped_column(default=datetime(2024, 1, 1))
        deleted_at: Mapped[datetime | None] = mapped_column(default=None)

    @fr.include_view(client.app)
    class PersonV1View(fr.AsyncRestView):
        prefix = "/v1people"
        model = PersonV1
        schema = _PersonSchema

    create_tables()

    rows = [
        {"name": "Alice", "age": 17, "created_at": "2024-06-01T00:00:00",
         "deleted_at": None},
        {"name": "Bob", "age": 30, "created_at": "2024-09-15T00:00:00",
         "deleted_at": "2025-02-01T00:00:00"},
        {"name": "Carol", "age": 65, "created_at": "2025-03-20T00:00:00",
         "deleted_at": None},
    ]
    for row in rows:
        client.post("/v1people/", json=row)

    return client


def _ids(rows):
    return sorted(r["id"] for r in rows)


def test_v1_range_operator_on_int_column(v1_client):
    """``filter[age]=>=18`` should hit the int column without 422."""
    response = v1_client.get("/v1people/?filter[age]=>=18")
    names = sorted(r["name"] for r in response.json())
    assert names == ["Bob", "Carol"]


def test_v1_range_operator_on_datetime_column(v1_client):
    """``filter[created_at]=>=2024-09-01`` should hit a datetime column."""
    response = v1_client.get("/v1people/?filter[created_at]=>=2024-09-01")
    names = sorted(r["name"] for r in response.json())
    assert names == ["Bob", "Carol"]


def test_v1_or_list_filter(v1_client):
    """``filter[id]=1,2,3`` should match all three rows (OR-list)."""
    rows_before = v1_client.get("/v1people/").json()
    target_ids = _ids(rows_before)
    csv = ",".join(str(i) for i in target_ids)

    response = v1_client.get(f"/v1people/?filter[id]={csv}")
    assert _ids(response.json()) == target_ids


def test_v1_null_filter(v1_client):
    """``filter[deleted_at]=null`` should return only rows with NULL."""
    response = v1_client.get("/v1people/?filter[deleted_at]=null")
    names = sorted(r["name"] for r in response.json())
    assert names == ["Alice", "Carol"]


def test_v1_negated_string_filter(v1_client):
    """``filter[name]=!Bob`` should exclude Bob."""
    response = v1_client.get("/v1people/?filter[name]=!Bob")
    names = sorted(r["name"] for r in response.json())
    assert names == ["Alice", "Carol"]


def test_v1_repeated_param_ands_predicates(v1_client):
    """B2.4: repeated ``filter[created_at]`` should AND the predicates.

    The window 2024-09-01 ≤ created_at < 2025-01-01 covers only Bob.
    Before the fix, the second value silently overwrote the first and
    Carol leaked into the result set.
    """
    response = v1_client.get(
        "/v1people/"
        "?filter[created_at]=>=2024-09-01"
        "&filter[created_at]=<2025-01-01"
    )
    names = sorted(r["name"] for r in response.json())
    assert names == ["Bob"]


# ---------------------------------------------------------------------------
# V2 end-to-end
# ---------------------------------------------------------------------------


@pytest.fixture
def v2_client(client):
    set_query_modifier_version(QueryModifierVersion.V2)

    class PersonV2(fr.IDBase):
        name: Mapped[str]
        age: Mapped[int]
        created_at: Mapped[datetime] = mapped_column(default=datetime(2024, 1, 1))
        deleted_at: Mapped[datetime | None] = mapped_column(default=None)

    @fr.include_view(client.app)
    class PersonV2View(fr.AsyncRestView):
        prefix = "/v2people"
        model = PersonV2
        schema = _PersonSchema

    create_tables()

    rows = [
        {"name": "Alice", "age": 17, "created_at": "2024-06-01T00:00:00",
         "deleted_at": None},
        {"name": "Bob", "age": 30, "created_at": "2024-09-15T00:00:00",
         "deleted_at": "2025-02-01T00:00:00"},
        {"name": "Carol", "age": 65, "created_at": "2025-03-20T00:00:00",
         "deleted_at": None},
    ]
    for row in rows:
        client.post("/v2people/", json=row)

    return client


def test_v2_range_operator_on_int_column(v2_client):
    response = v2_client.get("/v2people/?age__gte=18")
    names = sorted(r["name"] for r in response.json())
    assert names == ["Bob", "Carol"]


def test_v2_range_operator_on_datetime_column(v2_client):
    response = v2_client.get("/v2people/?created_at__gte=2024-09-01")
    names = sorted(r["name"] for r in response.json())
    assert names == ["Bob", "Carol"]


def test_v2_or_list_filter(v2_client):
    rows_before = v2_client.get("/v2people/").json()
    target_ids = _ids(rows_before)
    csv = ",".join(str(i) for i in target_ids)

    response = v2_client.get(f"/v2people/?id={csv}")
    assert _ids(response.json()) == target_ids


def test_v2_or_list_filter_on_datetime_column(v2_client):
    """B2.3: OR-comma on a typed (non-string) column must also work.

    The pre-fix V2 schema typed each filter as ``Optional[<column type>]``,
    which meant Pydantic rejected ``2024-06-01T00:00:00,2024-09-15T00:00:00``
    with 422 because the value isn't a single datetime. Once filters are
    typed as ``Optional[list[str]]`` and split downstream, the OR-comma
    promised by the docs works on every column type.
    """
    response = v2_client.get(
        "/v2people/?created_at=2024-06-01T00:00:00,2024-09-15T00:00:00"
    )
    names = sorted(r["name"] for r in response.json())
    assert names == ["Alice", "Bob"]


def test_v2_isnull_filter(v2_client):
    response = v2_client.get("/v2people/?deleted_at__isnull=true")
    names = sorted(r["name"] for r in response.json())
    assert names == ["Alice", "Carol"]


def test_v2_ne_filter(v2_client):
    response = v2_client.get("/v2people/?name__ne=Bob")
    names = sorted(r["name"] for r in response.json())
    assert names == ["Alice", "Carol"]


def test_v2_range_operators_not_emitted_for_bool(client):
    """B2.2: ``__gte``/``__lte``/... must not be generated for boolean fields.

    Booleans aren't orderable in SQL — emitting ``WHERE active >= true``
    raises ``sqlalchemy.exc.ArgumentError`` and bubbles up as HTTP 500.
    The schema should reject the parameter at validation time (422)
    instead.
    """
    set_query_modifier_version(QueryModifierVersion.V2)

    class FlaggedThing(fr.IDBase):
        name: Mapped[str]
        active: Mapped[bool] = mapped_column(default=True)

    class FlaggedSchema(fr.IDSchema):
        name: str
        active: bool

    @fr.include_view(client.app)
    class FlaggedView(fr.AsyncRestView):
        prefix = "/flagged"
        model = FlaggedThing
        schema = FlaggedSchema

    create_tables()

    client.post("/flagged/", json={"name": "x", "active": True})
    client.post("/flagged/", json={"name": "y", "active": False})

    # ``active`` (eq) and ``active__isnull`` and ``active__ne`` are valid.
    response = client.get("/flagged/?active=true")
    assert sorted(r["name"] for r in response.json()) == ["x"]
    response = client.get("/flagged/?active__ne=true")
    assert sorted(r["name"] for r in response.json()) == ["y"]
    response = client.get("/flagged/?active__isnull=false")
    assert len(response.json()) == 2

    # Pre-fix this returned HTTP 500 because the V2 schema generated
    # ``active__gte`` and SQLAlchemy then raised ``ArgumentError`` when
    # comparing a boolean column with ``>=``. After the fix the schema
    # omits the operator for booleans, FastAPI ignores the unknown query
    # parameter, and the request succeeds with all rows returned.
    response = client.get("/flagged/?active__gte=true")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_v2_repeated_contains_ands_predicates(v2_client):
    """B2.4: repeating ``__contains`` should AND the predicates.

    ``c`` matches Alice (ali-c-e) and Carol; ``e`` matches only Alice.
    ANDing the two yields Alice only. Before the fix the schema collapsed
    the two repeated values to one, so the second filter silently
    disappeared and the result quietly included Carol.
    """
    # Sanity check: the first term alone matches two rows.
    response = v2_client.get("/v2people/?name__contains=c")
    names = sorted(r["name"] for r in response.json())
    assert names == ["Alice", "Carol"]

    # Both terms together should narrow to Alice.  The order is chosen so
    # that, if the schema-validation layer keeps only the LAST value (the
    # pre-fix behaviour), the surviving filter is the less restrictive
    # ``c`` and Carol leaks in. The fix preserves both filters.
    response = v2_client.get(
        "/v2people/?name__contains=e&name__contains=c"
    )
    names = sorted(r["name"] for r in response.json())
    assert names == ["Alice"]

    # Fully disjoint terms must return no rows. Same ordering rationale:
    # if only the last filter survives, ``ho`` matches nothing — but if
    # we swap and only the *first* survives, ``hi`` would still match
    # nothing. The dropped-filter case is more interesting when one of
    # the terms is non-empty against the dataset; the assertion below
    # mainly guards against accidental wildcarding.
    response = v2_client.get(
        "/v2people/?name__contains=hi&name__contains=ho"
    )
    assert response.json() == []
