"""Tests for the handle-design authorization seam.

`authorize(action, obj=None, data=None)` is called by `handle_<verb>` at the
right phase: before the write for `create`, and after the scoped load for
`get_one` / `update` / `delete` (so `obj` is available for row-level checks).
The default `authorize` is a **no-op** -- override it to enforce policy and
raise `fr.Forbidden` / `fr.NotFound` to reject. These tests pin the no-op
default plus custom overrides (including that a rejection happens *before* the
row is written).
"""

from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr

from .conftest import create_tables


def _make_item_view(app, **attrs):
    class Item(fr.IDBase):
        name: Mapped[str]
        hidden: Mapped[bool] = mapped_column(default=False)

    class ItemSchema(fr.IDSchema):
        name: str
        hidden: bool

    namespace = {"prefix": "/items", "model": Item, "schema": ItemSchema, **attrs}
    view_cls = type("ItemView", (fr.AsyncRestView,), namespace)
    fr.include_view(app)(view_cls)
    create_tables()
    return view_cls


# ---------------------------------------------------------------------------
# Default `authorize` is an empty seam (no-op): every verb is open.
# ---------------------------------------------------------------------------


def test_default_authorize_is_noop_allows_all_verbs(client):
    """With no override, `authorize` gates nothing -- create / read / update /
    delete all succeed without any auth wiring."""
    _make_item_view(client.app)

    created = client.post("/items/", json={"name": "x", "hidden": False}).json()
    item_id = created["id"]

    assert client.get("/items/").json()[0]["name"] == "x"
    assert client.get(f"/items/{item_id}").status_code == 200
    assert client.patch(f"/items/{item_id}", json={"name": "y"}).json()["name"] == "y"
    client.delete(f"/items/{item_id}", assert_status_code=204)


# ---------------------------------------------------------------------------
# Custom `authorize` overrides (data-aware and row-level)
# ---------------------------------------------------------------------------


def test_authorize_rejects_by_data_before_write(client):
    """A data-aware reject in `authorize('create', data=...)` runs before the
    domain op + commit, so no row is created."""

    async def authorize(self, action, obj=None, data=None):
        if action == "create" and data is not None and data.name == "blocked":
            raise fr.Forbidden()

    _make_item_view(client.app, authorize=authorize)

    client.post(
        "/items/", json={"name": "blocked", "hidden": False}, assert_status_code=403
    )
    client.post("/items/", json={"name": "fine", "hidden": False})

    names = [row["name"] for row in client.get("/items/").json()]
    assert names == ["fine"]  # the blocked create never persisted


def test_authorize_row_level_get_one_is_404(client):
    """Row-level read policy: `authorize('get_one', obj=...)` can hide an
    existing row as a 404."""

    async def authorize(self, action, obj=None, data=None):
        if action == "get_one" and obj is not None and obj.hidden:
            raise fr.NotFound()

    _make_item_view(client.app, authorize=authorize)

    visible = client.post("/items/", json={"name": "v", "hidden": False}).json()
    hidden = client.post("/items/", json={"name": "h", "hidden": True}).json()

    assert client.get(f"/items/{visible['id']}").status_code == 200
    client.get(f"/items/{hidden['id']}", assert_status_code=404)
