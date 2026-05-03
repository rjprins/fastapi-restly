"""Regression tests for view/query and timestamp behavior."""

from datetime import timezone
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Mapped
from starlette.datastructures import QueryParams

import fastapi_restly as fr
from fastapi_restly.models._base import utc_now
from fastapi_restly.views._async import AsyncRestView
from fastapi_restly.views._base import get


class QueryUser(fr.IDBase):
    name: Mapped[str]


class QueryUserSchema(BaseModel):
    name: str


class _DummyScalarResult:
    def all(self):
        return []


class _DummySession:
    async def scalars(self, _query):
        return _DummyScalarResult()


class _DummyAsyncView(AsyncRestView):
    prefix = "/dummy"
    model = QueryUser
    schema = QueryUserSchema


@pytest.mark.asyncio
async def test_async_handle_list_uses_validated_query_params(monkeypatch):
    """``handle_list`` forwards the FastAPI-validated Pydantic model to the
    query-modifier dispatcher rather than the raw request query string.

    Pinned because raw ``request.query_params`` could contain unvalidated
    values (e.g. invalid pagination bounds) that would silently bypass our
    Pydantic Query schema.
    """
    captured = {}

    def _apply_query_modifiers(first_arg, query, model, schema):
        captured["first_arg"] = first_arg
        captured["model"] = model
        captured["schema"] = schema
        return query

    monkeypatch.setattr("fastapi_restly.views._async.apply_query_modifiers", _apply_query_modifiers)

    view = _DummyAsyncView()
    view.session = _DummySession()
    view.request = SimpleNamespace(query_params={"raw": "value"})

    class QueryModel(BaseModel):
        validated: str

    params = QueryModel(validated="value")
    await view.handle_list(params)

    # The validated Pydantic model is passed through verbatim — the modifier
    # is responsible for unpacking it. The raw request query params must not
    # be consulted.
    assert captured["first_arg"] is params


def test_utc_now_is_timezone_aware_utc():
    now = utc_now()
    assert now.tzinfo is timezone.utc


def test_get_decorator_sets_http_method_explicitly():
    @get("/hello")
    def endpoint():
        return {"ok": True}

    path, kwargs = endpoint._api_route_args  # type: ignore[attr-defined]
    assert path == "/hello"
    assert kwargs["methods"] == ["GET"]
    assert kwargs["status_code"] == 200
