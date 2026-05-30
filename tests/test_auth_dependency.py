"""Tests for FastAPI dependency-based auth integration with views (I3).

Verifies that a `Depends(...)` raising `HTTPException(401)` propagates cleanly
through both `AsyncRestView` and `RestView` and surfaces as a 401 response.

Also covers:
  - dependency that raises HTTPException(403)
  - per-route dependency vs class-level dependency
  - excluded routes don't fire the dependency (because they don't exist)
"""

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

from .conftest import create_tables

# ---------------------------------------------------------------------------
# Async (default fixture-based)
# ---------------------------------------------------------------------------


def test_async_view_dependency_raising_401_returns_401(client):
    """A class-level dependency that raises HTTPException(401) should propagate
    cleanly to the response — no 500, no broken JSON."""

    def require_auth():
        raise HTTPException(status_code=401, detail="Not authenticated")

    class Item(fr.IDBase):
        name: Mapped[str]

    class ItemSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class ItemView(fr.AsyncRestView):
        prefix = "/items"
        model = Item
        schema = ItemSchema
        dependencies = [Depends(require_auth)]

    create_tables()

    response = client.get("/items/", assert_status_code=401)
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


def test_async_view_dependency_raising_403_returns_403(client):
    """A 403 from a dependency should pass through unchanged."""

    def require_admin():
        raise HTTPException(status_code=403, detail="Forbidden")

    class Doc(fr.IDBase):
        title: Mapped[str]

    class DocSchema(fr.IDSchema):
        title: str

    @fr.include_view(client.app)
    class DocView(fr.AsyncRestView):
        prefix = "/docs"
        model = Doc
        schema = DocSchema
        dependencies = [Depends(require_admin)]

    create_tables()

    response = client.get("/docs/", assert_status_code=403)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


def test_async_view_dependency_401_blocks_post_too(client):
    """The dependency is applied to every route, so POST is blocked too."""

    def require_auth():
        raise HTTPException(status_code=401, detail="Not authenticated")

    class Note(fr.IDBase):
        text: Mapped[str]

    class NoteSchema(fr.IDSchema):
        text: str

    @fr.include_view(client.app)
    class NoteView(fr.AsyncRestView):
        prefix = "/notes"
        model = Note
        schema = NoteSchema
        dependencies = [Depends(require_auth)]

    create_tables()

    response = client.post("/notes/", json={"text": "hello"}, assert_status_code=401)
    assert response.status_code == 401


def test_async_view_dependency_with_authenticated_user_passes(client):
    """Sanity check: when the dependency does NOT raise, requests succeed."""

    def fake_user():
        return {"username": "alice"}

    class Bookmark(fr.IDBase):
        url: Mapped[str]

    class BookmarkSchema(fr.IDSchema):
        url: str

    @fr.include_view(client.app)
    class BookmarkView(fr.AsyncRestView):
        prefix = "/bookmarks"
        model = Bookmark
        schema = BookmarkSchema
        dependencies = [Depends(fake_user)]

    create_tables()

    response = client.post("/bookmarks/", json={"url": "https://example.com"})
    assert response.status_code == 201


# ---------------------------------------------------------------------------
# Sync (RestView)
# ---------------------------------------------------------------------------


def test_sync_view_dependency_raising_401_returns_401(sync_db):
    """Same contract for the sync RestView."""

    engine, _ = sync_db

    def require_auth():
        raise HTTPException(status_code=401, detail="Not authenticated")

    class SyncItem(fr.IDBase):
        name: Mapped[str]

    class SyncItemSchema(fr.IDSchema):
        name: str

    app = FastAPI()

    @fr.include_view(app)
    class SyncItemView(fr.RestView):
        prefix = "/sync-items"
        model = SyncItem
        schema = SyncItemSchema
        dependencies = [Depends(require_auth)]

    fr.DataclassBase.metadata.create_all(engine)

    test_client = TestClient(app)
    response = test_client.get("/sync-items/")
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


def test_sync_view_dependency_raising_403_returns_403(sync_db):
    engine, _ = sync_db

    def require_admin():
        raise HTTPException(status_code=403, detail="Admins only")

    class SyncDoc(fr.IDBase):
        title: Mapped[str]

    class SyncDocSchema(fr.IDSchema):
        title: str

    app = FastAPI()

    @fr.include_view(app)
    class SyncDocView(fr.RestView):
        prefix = "/sync-docs"
        model = SyncDoc
        schema = SyncDocSchema
        dependencies = [Depends(require_admin)]

    fr.DataclassBase.metadata.create_all(engine)

    test_client = TestClient(app)
    response = test_client.get("/sync-docs/")
    assert response.status_code == 403


def test_sync_view_dependency_with_authenticated_user_passes(sync_db):
    engine, _ = sync_db

    def fake_user():
        return {"username": "alice"}

    class SyncMemo(fr.IDBase):
        body: Mapped[str]

    class SyncMemoSchema(fr.IDSchema):
        body: str

    app = FastAPI()

    @fr.include_view(app)
    class SyncMemoView(fr.RestView):
        prefix = "/memos"
        model = SyncMemo
        schema = SyncMemoSchema
        dependencies = [Depends(fake_user)]

    fr.DataclassBase.metadata.create_all(engine)

    test_client = TestClient(app)
    response = test_client.post("/memos/", json={"body": "first"})
    assert response.status_code == 201


# ---------------------------------------------------------------------------
# Excluded routes don't fire the dependency
# ---------------------------------------------------------------------------


def test_async_excluded_route_does_not_trigger_dependency(client):
    """When a route is excluded via exclude_routes, the dependency on the
    view should NOT fire for it (the route does not exist, so 404)."""

    call_log: list[str] = []

    def require_auth():
        call_log.append("auth")
        raise HTTPException(status_code=401, detail="Not authenticated")

    class Tag(fr.IDBase):
        name: Mapped[str]

    class TagSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class TagView(fr.AsyncRestView):
        prefix = "/tags"
        model = Tag
        schema = TagSchema
        dependencies = [Depends(require_auth)]
        exclude_routes = ("delete_endpoint",)

    create_tables()

    # GET still triggers the dep (401)
    client.get("/tags/", assert_status_code=401)
    assert call_log == ["auth"]

    # DELETE was excluded -> the route does not exist, so FastAPI returns
    # 405 (other methods on the same path) without invoking the dependency.
    response = client.delete("/tags/1", assert_status_code=405)
    assert response.status_code == 405
    # Still only the one auth call from the GET above.
    assert call_log == ["auth"]
