"""Rows with NULL, in the filters that say what a value is not.

``__ne`` and ``__not_in`` keep a row with NULL. SQL leaves it out, but a client
that asks for "not archived" also means the row without a status. A dotted key
uses a LEFT OUTER JOIN, so a row without the related row counts as NULL.
"""

import asyncio
from typing import Any

import pytest
from pydantic import Field
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient


class Base(DeclarativeBase):
    pass


class Vendor(fr.IDMixin, Base):
    __tablename__ = "filter_nulls_vendor"
    name: Mapped[str]
    country: Mapped[str | None]


class Task(fr.IDMixin, Base):
    __tablename__ = "filter_nulls_task"
    name: Mapped[str]
    status: Mapped[str | None] = mapped_column(default=None)
    points: Mapped[int | None] = mapped_column(default=None)
    vendor_id: Mapped[int | None] = mapped_column(
        ForeignKey("filter_nulls_vendor.id"), default=None
    )
    vendor: Mapped[Vendor | None] = relationship()


class VendorSchema(fr.IDSchema):
    name: str
    country: str | None = None


class TaskSchema(fr.IDSchema):
    name: str
    status: str | None = None
    points: int | None = None
    vendor: fr.ReadOnly[VendorSchema | None] = Field(default=None)
    vendor_id: int | None = None


@pytest.fixture
def api(client: RestlyTestClient) -> RestlyTestClient:
    @fr.include_view(client.app)
    class TaskView(fr.AsyncRestView):
        prefix = "/tasks"
        model = Task
        schema = TaskSchema
        include_filters = ("*", "vendor.*")
        include_sort_keys = ("*", "vendor.*")

    @fr.include_view(client.app)
    class VendorView(fr.AsyncRestView):
        prefix = "/vendors"
        model = Vendor
        schema = VendorSchema

    asyncio.run(fr.db.async_create_all(Base))
    client.post("/vendors", json={"name": "acme", "country": "NL"})
    client.post("/vendors", json={"name": "globex"})
    for body in (
        {"name": "open", "status": "open", "points": 1, "vendor_id": 1},
        {"name": "done", "status": "done", "points": 2, "vendor_id": 2},
        {"name": "none"},
    ):
        client.post("/tasks", json=body)
    return client


def names(client: RestlyTestClient, **params: Any) -> list[str]:
    params = {key.replace("__dot__", "."): value for key, value in params.items()}
    payload = client.get("/tasks", params=params).json()
    assert payload["total_count"] == len(payload["data"])
    return sorted(row["name"] for row in payload["data"])


def test_ne_keeps_a_row_with_null(api):
    assert names(api, status__ne="done") == ["none", "open"]
    assert names(api, points__ne=1) == ["done", "none"]


def test_not_in_keeps_a_row_with_null(api):
    assert names(api, status__not_in="done,open") == ["none"]
    assert names(api, points__not_in="1,9") == ["done", "none"]
    assert names(api, status__not_in="done") == ["none", "open"]


def test_keys_that_look_for_a_value_leave_out_a_row_with_null(api):
    assert names(api, status="done") == ["done"]
    assert names(api, status__in="done,open") == ["done", "open"]
    assert names(api, points__gte=0) == ["done", "open"]
    assert names(api, status__icontains="o") == ["done", "open"]


def test_a_dotted_key_keeps_a_row_without_the_related_row(api):
    assert names(api, **{"vendor.name__ne": "acme"}) == ["done", "none"]
    assert names(api, **{"vendor.name__not_in": "acme"}) == ["done", "none"]
    assert names(api, **{"vendor.name__isnull": "true"}) == ["none"]
    assert names(api, **{"vendor.id__isnull": "true"}) == ["none"]
    assert names(api, **{"vendor.id__isnull": "false"}) == ["done", "open"]


def test_a_related_null_and_a_missing_related_row_count_alike(api):
    # globex has no country, and the row "none" has no vendor
    assert names(api, **{"vendor.country__isnull": "true"}) == ["done", "none"]
    assert names(api, **{"vendor.country__ne": "NL"}) == ["done", "none"]


def test_a_dotted_key_that_looks_for_a_value_leaves_out_such_a_row(api):
    assert names(api, **{"vendor.name": "acme"}) == ["open"]
    assert names(api, **{"vendor.name__in": "acme,globex"}) == ["done", "open"]
    assert names(api, **{"vendor.name__icontains": "c"}) == ["open"]


def test_a_filter_and_a_sort_share_the_outer_join(api):
    rows = api.get(
        "/tasks", params={"vendor.name__ne": "acme", "sort": "-vendor.name"}
    ).json()["data"]
    assert [row["name"] for row in rows] == ["done", "none"]
