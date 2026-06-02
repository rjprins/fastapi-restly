"""Regression tests for the handle-design commit contract.

`handle_<verb>` owns the only commit for a write request; the request-session
dependency no longer commits on response. So an ORM mutation made inside
`after_commit` (which runs *after* the durable write) must NOT be persisted by
a second commit -- it is discarded when the session closes.
"""

import pytest
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr

from .conftest import create_tables

# These tests deliberately leave writes uncommitted to prove they are discarded,
# which (correctly) trips RestlyUncommittedChangesWarning. That behaviour has its
# own coverage in test_uncommitted_warning.py; silence the noise here.
pytestmark = pytest.mark.filterwarnings(
    "ignore::fastapi_restly.exc.RestlyUncommittedChangesWarning"
)


def test_after_commit_orm_mutation_is_not_persisted(client):
    class Note(fr.IDBase):
        title: Mapped[str]
        body: Mapped[str] = mapped_column(default="")

    class NoteSchema(fr.IDSchema):
        title: str
        body: str

    @fr.include_view(client.app)
    class NoteView(fr.AsyncRestView):
        prefix = "/notes"
        model = Note
        schema = NoteSchema

        async def after_commit(self, action, new, old=None):
            # A mistaken ORM write in after_commit must not become durable.
            if new is not None:
                new.body = "stray-write-in-after-commit"

    create_tables()

    created = client.post("/notes/", json={"title": "t", "body": ""}).json()
    note_id = created["id"]

    # A fresh request reads the committed state: the stray after_commit write
    # was never committed, so it is gone (before the fix, a second dependency
    # commit would have persisted it).
    fetched = client.get(f"/notes/{note_id}").json()
    assert fetched["body"] == ""


def test_custom_route_must_commit_explicitly(client):
    """A custom write route that does not use a handler owns its own commit:
    without ``self.session.commit()`` the write is discarded; with it, it persists."""

    class Counter(fr.IDBase):
        label: Mapped[str]
        value: Mapped[int] = mapped_column(default=0)

    class CounterSchema(fr.IDSchema):
        label: str
        value: int

    @fr.include_view(client.app)
    class CounterView(fr.AsyncRestView):
        prefix = "/counters"
        model = Counter
        schema = CounterSchema

        @fr.post("/{id}/bump-uncommitted")
        async def bump_uncommitted(self, id: int):
            obj = await self.get_one(id)
            obj.value += 1
            await self.save_object(obj)  # flush only -- no commit
            return self.to_response(obj)

        @fr.post("/{id}/bump")
        async def bump(self, id: int):
            obj = await self.get_one(id)
            obj.value += 1
            await self.save_object(obj)
            await self.session.commit()  # the custom route owns the commit
            return self.to_response(obj)

    create_tables()
    created = client.post("/counters/", json={"label": "c", "value": 0}).json()
    cid = created["id"]

    client.post(f"/counters/{cid}/bump-uncommitted")
    assert client.get(f"/counters/{cid}").json()["value"] == 0  # discarded

    client.post(f"/counters/{cid}/bump")
    assert client.get(f"/counters/{cid}").json()["value"] == 1  # persisted
