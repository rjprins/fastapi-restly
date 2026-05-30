"""Tests for the handle-design authorization seam.

`authorize(action, obj=None, data=None)` is called by `handle_<verb>` at the
right phase: before the write for create, after the scoped load for
update/delete/get_one. The default `authorize` consults the `permissions`
dict and fails *closed*. These tests pin both the declarative `permissions`
path and custom `authorize` overrides (including that a rejection happens
*before* the row is written).
"""

from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr

from .conftest import create_tables


class _PermUser:
    """Minimal authenticated principal exposing ``has_permission``."""

    def __init__(self, perms: set[str]):
        self._perms = perms

    def has_permission(self, perm: str) -> bool:
        return perm in self._perms


class _PermsMiddleware:
    """Pure-ASGI shim: set ``scope['user']`` from an ``X-Perms`` header.

    No header -> no ``user`` in scope, so ``request.user`` raises (as it would
    without ``AuthenticationMiddleware``) and the view must fail closed.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            raw = dict(scope["headers"]).get(b"x-perms")
            if raw is not None:
                perms = set(raw.decode().split(",")) if raw else set()
                scope["user"] = _PermUser(perms)
        await self.app(scope, receive, send)


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
# Declarative `permissions` dict -> default authorize -> _check_permission
# ---------------------------------------------------------------------------


def test_permissions_fails_closed_without_authenticated_user(client):
    """No authenticated user (no AuthenticationMiddleware) -> 403, not 500,
    and nothing is written."""
    client.app.add_middleware(_PermsMiddleware)
    _make_item_view(client.app, permissions={"create": "items:create"})

    client.post(
        "/items/", json={"name": "x", "hidden": False}, assert_status_code=403
    )

    # The reject happened before the write: the table is still empty.
    listed = client.get("/items/", headers={"X-Perms": "items:create"})
    assert listed.json() == []


def test_permissions_denies_user_missing_permission(client):
    client.app.add_middleware(_PermsMiddleware)
    _make_item_view(client.app, permissions={"create": "items:create"})

    client.post(
        "/items/",
        json={"name": "x", "hidden": False},
        headers={"X-Perms": "other"},
        assert_status_code=403,
    )


def test_permissions_allows_user_with_permission(client):
    client.app.add_middleware(_PermsMiddleware)
    _make_item_view(client.app, permissions={"create": "items:create"})

    resp = client.post(
        "/items/",
        json={"name": "x", "hidden": False},
        headers={"X-Perms": "items:create"},
    )
    assert resp.json()["name"] == "x"


def test_unlisted_action_is_unrestricted(client):
    """An action absent from ``permissions`` is a no-op check (open)."""
    client.app.add_middleware(_PermsMiddleware)
    _make_item_view(client.app, permissions={"delete": "items:delete"})

    # create has no entry -> allowed even with no user (default asserts 201).
    client.post("/items/", json={"name": "x", "hidden": False})


# ---------------------------------------------------------------------------
# Custom `authorize` overrides (data-aware and row-level)
# ---------------------------------------------------------------------------


def test_authorize_rejects_by_data_before_write(client):
    """A data-aware reject in ``authorize('create', data=...)`` runs before the
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
    """Row-level read policy: ``authorize('get_one', obj=...)`` can hide an
    existing row as a 404."""

    async def authorize(self, action, obj=None, data=None):
        if action == "get_one" and obj is not None and obj.hidden:
            raise fr.NotFound()

    _make_item_view(client.app, authorize=authorize)

    visible = client.post("/items/", json={"name": "v", "hidden": False}).json()
    hidden = client.post("/items/", json={"name": "h", "hidden": True}).json()

    assert client.get(f"/items/{visible['id']}").status_code == 200
    client.get(f"/items/{hidden['id']}", assert_status_code=404)
