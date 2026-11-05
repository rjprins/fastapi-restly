"""End-to-end tests for list-params filtering driven through FastAPI.

These tests exercise the full Starlette → FastAPI Pydantic-validation →
:func:`apply_list_params` pipeline with a real registered view. The
``tests/test_query_modifiers.py`` suite builds ``QueryParams`` by hand,
which bypasses the schema-validation layer where bugs B2.1 and B2.4
originally lived.

What they pin in place:

* B2.3 — OR-comma works on every column type, not just strings, because
  filters are typed as ``Optional[list[str]]`` and split downstream.
* B2.4 — Repeated query parameters are preserved as multiple AND'd
  predicates rather than silently collapsed (``name__icontains=hi&
  name__icontains=ho``).
"""

from datetime import datetime
from typing import Annotated

import pydantic
import pytest
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr

from ._models import SharedBase
from .conftest import create_tables


class _PersonSchema(fr.IDSchema):
    name: str
    age: int
    created_at: datetime
    deleted_at: datetime | None = None


@pytest.fixture
def people_client(client):
    class Person(fr.IDMixin, SharedBase):
        name: Mapped[str]
        age: Mapped[int]
        created_at: Mapped[datetime] = mapped_column(default=datetime(2024, 1, 1))
        deleted_at: Mapped[datetime | None] = mapped_column(default=None)

    @fr.include_view(client.app)
    class PersonView(fr.AsyncRestView):
        prefix = "/people"
        model = Person
        schema = _PersonSchema

    create_tables()

    rows = [
        {
            "name": "Alice",
            "age": 17,
            "created_at": "2024-06-01T00:00:00",
            "deleted_at": None,
        },
        {
            "name": "Bob",
            "age": 30,
            "created_at": "2024-09-15T00:00:00",
            "deleted_at": "2025-02-01T00:00:00",
        },
        {
            "name": "Carol",
            "age": 65,
            "created_at": "2025-03-20T00:00:00",
            "deleted_at": None,
        },
    ]
    for row in rows:
        client.post("/people/", json=row)

    return client


def _ids(rows):
    return sorted(r["id"] for r in rows)


def _rows(response):
    """The list items out of the paginated ``{"data": [...]}`` envelope."""
    return response.json()["data"]


def test_range_operator_on_int_column(people_client):
    response = people_client.get("/people/?age__gte=18")
    names = sorted(r["name"] for r in _rows(response))
    assert names == ["Bob", "Carol"]


def test_range_operator_on_datetime_column(people_client):
    response = people_client.get("/people/?created_at__gte=2024-09-01")
    names = sorted(r["name"] for r in _rows(response))
    assert names == ["Bob", "Carol"]


def test_in_filter_on_ids(people_client):
    rows_before = _rows(people_client.get("/people/"))
    target_ids = _ids(rows_before)
    csv = ",".join(str(i) for i in target_ids)

    response = people_client.get(f"/people/?id__in={csv}")
    assert _ids(_rows(response)) == target_ids


def test_a_plain_key_takes_the_whole_value(people_client):
    """Only ``__in`` and ``__not_in`` split on commas."""
    people_client.get("/people/?id=1,2,3", assert_status_code=422)
    assert _rows(people_client.get("/people/?name=Alice,Bob")) == []


def test_in_takes_a_repeated_key_with_each_value_as_it_is(people_client):
    response = people_client.get("/people/?name__in=Alice&name__in=Bob")
    assert sorted(r["name"] for r in _rows(response)) == ["Alice", "Bob"]

    # with one key, a comma separates; with several, it is part of the value
    assert _rows(people_client.get("/people/?name__in=Alice,Bob&name__in=x")) == []
    response = people_client.get("/people/?name__in=Alice, Bob")
    assert sorted(r["name"] for r in _rows(response)) == ["Alice", "Bob"]


@pytest.mark.parametrize(
    ("url", "named"),
    [
        ("/people/?name=a&name=b", "name__in"),
        ("/people/?name__ne=a&name__ne=b", "name__not_in"),
        ("/people/?name__icontains=a&name__icontains=b", None),
        ("/people/?sort=name&sort=id", None),
    ],
)
def test_a_key_that_takes_one_value_cannot_repeat(people_client, url, named):
    response = people_client.get(url, assert_status_code=422)
    error = response.json()["detail"][0]
    assert error["type"] == "repeated_key"
    key = url.split("?")[1].split("=")[0]
    assert error["loc"] == ["query", key]
    assert (named in error["msg"]) if named else ("To send" not in error["msg"])


def test_a_bad_value_in_a_list_names_its_position(people_client):
    response = people_client.get("/people/?id__in=1,x,y", assert_status_code=422)
    detail = response.json()["detail"]
    assert [error["loc"] for error in detail] == [
        ["query", "id__in", 1],
        ["query", "id__in", 2],
    ]
    assert detail[0]["input"] == "x"


def test_explicit_in_filter_operator(people_client):
    response = people_client.get("/people/?name__in=Alice,Carol")
    names = sorted(r["name"] for r in _rows(response))
    assert names == ["Alice", "Carol"]


def test_in_filter_on_datetime_column(people_client):
    """Each value of ``__in`` is validated on its own."""
    response = people_client.get(
        "/people/?created_at__in=2024-06-01T00:00:00,2024-09-15T00:00:00"
    )
    names = sorted(r["name"] for r in _rows(response))
    assert names == ["Alice", "Bob"]


def test_isnull_filter(people_client):
    response = people_client.get("/people/?deleted_at__isnull=true")
    names = sorted(r["name"] for r in _rows(response))
    assert names == ["Alice", "Carol"]


def test_ne_filter(people_client):
    response = people_client.get("/people/?name__ne=Bob")
    names = sorted(r["name"] for r in _rows(response))
    assert names == ["Alice", "Carol"]


def test_not_in_filter(people_client):
    response = people_client.get("/people/?name__not_in=Bob,Carol")
    names = sorted(r["name"] for r in _rows(response))
    assert names == ["Alice"]

    response = people_client.get("/people/?name__not_in=Bob&name__not_in=Carol")
    assert sorted(r["name"] for r in _rows(response)) == ["Alice"]

    # ``__ne`` takes the whole value
    response = people_client.get("/people/?name__ne=Bob,Carol")
    assert sorted(r["name"] for r in _rows(response)) == ["Alice", "Bob", "Carol"]


@pytest.mark.parametrize(
    "annotation",
    [bool, Annotated[bool, pydantic.Field(description="Active")] | None],
    ids=["bool", "annotated-inside-optional"],
)
def test_range_operators_not_emitted_for_bool(client, annotation):
    """``__gte``/``__lte``/... must not be generated for boolean fields, and
    sending one is rejected as an unknown query parameter.

    Booleans aren't orderable in SQL — emitting ``WHERE active >= true``
    raises ``sqlalchemy.exc.ArgumentError`` and bubbles up as HTTP 500.
    The schema omits the parameter, and the default list endpoint
    rejects unknown keys with a 422 instead of silently ignoring them
    (which would widen the result set — bad for filters). Pydantic keeps an
    ``Annotated`` inside ``Optional`` on the field. Before the fix, that
    ``Annotated`` layer hid the bool type from the check.
    """

    class FlaggedThing(fr.IDMixin, SharedBase):
        name: Mapped[str]
        active: Mapped[bool] = mapped_column(default=True)

    class FlaggedSchema(fr.IDSchema):
        name: str
        active: annotation

    @fr.include_view(client.app)
    class FlaggedView(fr.AsyncRestView):
        prefix = "/flagged"
        model = FlaggedThing
        schema = FlaggedSchema

    create_tables()

    client.post("/flagged/", json={"name": "x", "active": True})
    client.post("/flagged/", json={"name": "y", "active": False})

    response = client.get("/flagged/?active=true")
    assert sorted(r["name"] for r in _rows(response)) == ["x"]
    response = client.get("/flagged/?active__ne=true")
    assert sorted(r["name"] for r in _rows(response)) == ["y"]

    # ``active__gte`` is not part of the schema for a bool field. The
    # default endpoint rejects unknown query params with 422 rather
    # than ignoring them, which would otherwise widen the result set.
    response = client.get("/flagged/?active__gte=true", assert_status_code=422)
    body = response.json()
    assert any(
        item.get("loc") == ["query", "active__gte"] for item in body.get("detail", [])
    )


def test_a_text_search_takes_one_phrase(people_client):
    """Spaces around the text go, and an empty value is no filter. A space
    inside is part of the text."""
    assert sorted(
        r["name"] for r in _rows(people_client.get("/people/?name__icontains=c"))
    ) == ["Alice", "Carol"]
    assert sorted(
        r["name"] for r in _rows(people_client.get("/people/?name__icontains=%20c%20"))
    ) == ["Alice", "Carol"]
    assert _rows(people_client.get("/people/?name__icontains=a c")) == []
    assert len(_rows(people_client.get("/people/?name__icontains=%20"))) == 3


def test_icontains_uses_case_insensitive_matching(people_client):
    response = people_client.get("/people/?name__icontains=ali")
    names = sorted(r["name"] for r in _rows(response))
    assert names == ["Alice"]


def test_unknown_query_param_rejected_with_422(people_client):
    """A typoed or otherwise unknown filter is rejected, not ignored."""
    response = people_client.get("/people/?nme=Alice", assert_status_code=422)
    body = response.json()
    locs = [item.get("loc") for item in body.get("detail", [])]
    assert ["query", "nme"] in locs


def test_legacy_order_by_param_rejected_with_422(people_client):
    """The standard REST dialect exposes ``sort`` only, not ``order_by``."""
    response = people_client.get("/people/?order_by=name", assert_status_code=422)
    body = response.json()
    locs = [item.get("loc") for item in body.get("detail", [])]
    assert ["query", "order_by"] in locs


def test_python_field_name_rejected_for_aliased_field(client):
    """Aliased fields are reachable only by their alias on the URL surface.

    The Python field name is not declared by the generated schema, so
    sending it is rejected as an unknown parameter — even if the schema
    has ``populate_by_name=True``.
    """
    import pydantic

    class Item(fr.IDMixin, SharedBase):
        display_name: Mapped[str]

    class ItemSchema(fr.IDSchema):
        model_config = pydantic.ConfigDict(populate_by_name=True)
        display_name: str = pydantic.Field(alias="displayName")

    @fr.include_view(client.app)
    class ItemView(fr.AsyncRestView):
        prefix = "/aliased-items"
        model = Item
        schema = ItemSchema

    create_tables()

    client.post("/aliased-items/", json={"displayName": "X"})

    # Alias is accepted.
    response = client.get("/aliased-items/?displayName=X")
    assert response.status_code == 200

    # Python field name is rejected.
    response = client.get("/aliased-items/?display_name=X", assert_status_code=422)
    locs = [item.get("loc") for item in response.json().get("detail", [])]
    assert ["query", "display_name"] in locs


def test_unsupported_operator_rejected(people_client):
    """An operator suffix that isn't valid for the field's type is rejected.

    Contains operators are only emitted for string fields. Sending one on an
    int column lets the schema reject the unknown key rather than running an
    unintended SQL expression.
    """
    response = people_client.get("/people/?age__icontains=2", assert_status_code=422)
    locs = [item.get("loc") for item in response.json().get("detail", [])]
    assert ["query", "age__icontains"] in locs
