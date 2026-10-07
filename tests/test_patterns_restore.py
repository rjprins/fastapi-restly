"""Restore a soft-deleted row, as docs/patterns.md shows it.

The view scope hides deleted rows; the restore route reads through the
complementary clause and mutates inside ``write_action``. Sync and async.
"""

import asyncio
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient


@pytest.fixture(params=["sync", "async"])
def client(request: pytest.FixtureRequest) -> Iterator[RestlyTestClient]:
    sync = request.param == "sync"
    base = fr.RestView if sync else fr.AsyncRestView

    class Item(fr.IDBase):
        name: Mapped[str]
        deleted_at: Mapped[datetime | None] = mapped_column(default=None)

    class ItemSchema(fr.IDSchema):
        name: str

    authorized: list[str] = []
    is_deleted = fr.where_clause(Item.deleted_at.is_not(None))
    app = FastAPI()

    if sync:

        @fr.include_view(app)
        class ItemView(base):  # type: ignore[misc,valid-type]
            prefix = "/items"
            model = Item
            schema = ItemSchema
            scope = fr.none_of(is_deleted)

            def delete(self, obj):
                obj.deleted_at = datetime.now(timezone.utc)

            def authorize(self, action, *, obj=None, data=None):
                authorized.append(str(action))

            @fr.post("/{id}/restore", response_model=ItemSchema, status_code=200)
            def restore(self, id: int):
                obj = self.get_one(id, scope=is_deleted)
                with self.write_action("restore", obj=obj):
                    obj.deleted_at = None
                return self.to_response(obj)

    else:

        @fr.include_view(app)
        class ItemView(base):  # type: ignore[no-redef,misc,valid-type]
            prefix = "/items"
            model = Item
            schema = ItemSchema
            scope = fr.none_of(is_deleted)

            async def delete(self, obj):
                obj.deleted_at = datetime.now(timezone.utc)

            async def authorize(self, action, *, obj=None, data=None):
                authorized.append(str(action))

            @fr.post("/{id}/restore", response_model=ItemSchema, status_code=200)
            async def restore(self, id: int):
                obj = await self.get_one(id, scope=is_deleted)
                async with self.write_action("restore", obj=obj):
                    obj.deleted_at = None
                return self.to_response(obj)

    if sync:
        engine, _ = request.getfixturevalue("sync_db")
        fr.DataclassBase.metadata.create_all(engine)
    else:
        asyncio.run(fr.db.async_create_all(fr.DataclassBase))

    test_client = RestlyTestClient(app)
    test_client.post("/items/", json={"name": "kept"})
    test_client.post("/items/", json={"name": "binned"})
    test_client.delete("/items/2")
    authorized.clear()
    test_client.authorized = authorized  # type: ignore[attr-defined]
    yield test_client


def test_deleted_row_is_hidden(client):
    client.get("/items/2", assert_status_code=404)
    assert [item["name"] for item in client.get("/items/").json()["data"]] == ["kept"]


def test_restore_brings_the_row_back(client):
    response = client.post("/items/2/restore", assert_status_code=200)
    assert response.json() == {"id": 2, "name": "binned"}
    assert client.get("/items/2").json()["name"] == "binned"
    assert [item["name"] for item in client.get("/items/").json()["data"]] == [
        "kept",
        "binned",
    ]


def test_restore_runs_authorize_with_its_action(client):
    client.post("/items/2/restore", assert_status_code=200)
    assert client.authorized == ["restore"]


def test_only_a_deleted_row_can_be_restored(client):
    client.post("/items/1/restore", assert_status_code=404)
    client.post("/items/99/restore", assert_status_code=404)
