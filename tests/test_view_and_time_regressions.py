"""Regression tests for view/query and timestamp behavior."""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly.models._base import utc_now
from fastapi_restly.views._async import AsyncRestView
from fastapi_restly.views._base import delete, get, patch, post, put

from ._models import SharedBase


class QueryUser(fr.IDMixin, SharedBase):
    name: Mapped[str]


class QueryUserSchema(BaseModel):
    name: str


class _DummyScalarResult:
    def unique(self):  # mirror ScalarResult.unique() -> returns self for chaining
        return self

    def all(self):
        return []


class _DummySession:
    async def scalar(self, _query):
        return 0

    async def scalars(self, _query):
        return _DummyScalarResult()


class _DummyAsyncView(AsyncRestView):
    prefix = "/dummy"
    model = QueryUser
    schema = QueryUserSchema


@pytest.mark.asyncio
async def test_async_get_many_uses_validated_list_params(monkeypatch):
    """``get_many`` forwards the FastAPI-validated Pydantic model to the
    query-modifier dispatcher rather than the raw request query string.

    Pinned because raw ``request.query_params`` could contain unvalidated
    values (e.g. invalid pagination bounds) that would silently bypass our
    Pydantic Query schema.
    """
    captured: dict[str, list[Any]] = {"list_params": [], "pagination": []}

    def _apply_step(query, list_params, model, schema, *, pagination, **_):
        captured["list_params"].append(list_params)
        captured["pagination"].append(pagination)
        return query

    monkeypatch.setattr("fastapi_restly.views._async._apply_filters", _apply_step)
    monkeypatch.setattr("fastapi_restly.views._async._apply_sort_and_page", _apply_step)

    view = _DummyAsyncView()
    view.session = _DummySession()
    view.request = SimpleNamespace(query_params={"raw": "value"})

    class QueryModel(BaseModel):
        validated: str

    params = QueryModel(validated="value")
    await view.get_many(params)

    # The validated Pydantic model is passed through verbatim — the modifier
    # is responsible for unpacking it. The raw request query params must not
    # be consulted.
    # the filter step, then the sort and page step
    assert captured["list_params"] == [params, params]
    assert all(each is params for each in captured["list_params"])
    # and the view's pagination, which names the page parameters to read
    assert captured["pagination"] == [_DummyAsyncView.pagination] * 2


def test_utc_now_is_timezone_aware_utc():
    now = utc_now()
    assert now.tzinfo is timezone.utc


def test_timestamp_mixin_maps_timezone_aware_columns():
    class TimestampedRecord(fr.TimestampsMixin, fr.IDMixin, SharedBase):
        pass

    assert TimestampedRecord.__table__.c.created_at.type.timezone is True
    assert TimestampedRecord.__table__.c.updated_at.type.timezone is True


def test_datetime_annotations_are_timezone_aware_by_default_with_naive_opt_out():
    class DefaultTimestampRecord(fr.IDMixin, SharedBase):
        occurred_at: Mapped[datetime]

    class NaiveTimestampRecord(fr.IDMixin, SharedBase):
        occurred_at: Mapped[datetime] = mapped_column(DateTime())

    assert DefaultTimestampRecord.__table__.c.occurred_at.type.timezone is True
    assert NaiveTimestampRecord.__table__.c.occurred_at.type.timezone is False


@pytest.mark.parametrize(
    ("decorator", "method", "default_status_code"),
    [
        (get, "GET", 200),
        (post, "POST", 201),
        (put, "PUT", 200),
        (patch, "PATCH", 200),
        (delete, "DELETE", 204),
    ],
)
def test_http_method_decorators_set_method_and_default_status_code(
    decorator, method, default_status_code
):
    @decorator("/hello")
    def endpoint():
        return {"ok": True}

    path, kwargs = endpoint._api_route_args  # type: ignore[attr-defined]
    assert path == "/hello"
    assert kwargs["methods"] == [method]
    assert kwargs["status_code"] == default_status_code


def test_http_method_decorator_status_code_can_be_overridden():
    @patch("/hello", status_code=202)
    def endpoint():
        return {"ok": True}

    _path, kwargs = endpoint._api_route_args  # type: ignore[attr-defined]
    assert kwargs["methods"] == ["PATCH"]
    assert kwargs["status_code"] == 202
