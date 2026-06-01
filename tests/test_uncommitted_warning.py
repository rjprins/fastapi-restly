"""Tests for the uncommitted-changes safety net.

When the framework owns the commit (the default), a request that finishes with
flushed-but-uncommitted changes (a custom write route that forgot to commit) --
or added-but-unflushed objects -- emits ``RestlyUncommittedChangesWarning`` just
before the session rolls them back. It stays quiet for normal committed writes,
reads, deliberate dry-runs, failed requests, and when disabled.
"""

import warnings

import pytest
from sqlalchemy.orm import Mapped

import fastapi_restly as fr
from fastapi_restly.db._globals import _fr_globals

from .conftest import create_tables


@pytest.fixture(autouse=True)
def _restore_config():
    """The config lives on a process-wide context; restore it after each test."""
    original_async_gen = _fr_globals.session_generator
    original_sync_gen = _fr_globals.sync_session_generator
    yield
    _fr_globals.warn_on_uncommitted = True
    _fr_globals.session_generator = original_async_gen
    _fr_globals.sync_session_generator = original_sync_gen


def _build(client):
    class Thing(fr.IDBase):
        name: Mapped[str]

    class ThingSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class ThingView(fr.AsyncRestView):
        prefix = "/things"
        model = Thing
        schema = ThingSchema

        @fr.post("/forgot")
        async def forgot(self):
            self.session.add(Thing(name="x"))
            await self.session.flush()  # flush, NO commit
            return {"ok": True}

        @fr.post("/added-not-flushed")
        async def added_not_flushed(self):
            self.session.add(Thing(name="x"))  # no flush, no commit
            return {"ok": True}

        @fr.post("/dryrun")
        async def dryrun(self):
            self.session.add(Thing(name="y"))
            await self.session.flush()
            self.session.info["_fr_suppress_uncommitted"] = True
            return {"ok": True}

        @fr.post("/boom")
        async def boom(self):
            self.session.add(Thing(name="z"))
            await self.session.flush()
            raise fr.Conflict("nope")  # fails -> rollback is correct

    create_tables()


def _warn_count(call) -> int:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        call()
    return sum(
        issubclass(w.category, fr.RestlyUncommittedChangesWarning) for w in caught
    )


def test_forgot_commit_warns(client):
    _build(client)
    assert _warn_count(lambda: client.post("/things/forgot")) >= 1


def test_added_but_not_flushed_warns(client):
    _build(client)
    # new/dirty/deleted path: added to the session but never flushed.
    assert _warn_count(lambda: client.post("/things/added-not-flushed")) >= 1


def test_handle_create_does_not_warn(client):
    _build(client)
    assert _warn_count(lambda: client.post("/things/", json={"name": "a"})) == 0


def test_read_only_does_not_warn(client):
    _build(client)
    assert _warn_count(lambda: client.get("/things/")) == 0


def test_dry_run_suppressed_does_not_warn(client):
    _build(client)
    assert _warn_count(lambda: client.post("/things/dryrun")) == 0


def test_failed_request_does_not_warn(client):
    _build(client)
    # boom flushes then raises 409: the rollback is correct, so no warning.
    assert _warn_count(lambda: client.post("/things/boom", assert_status_code=409)) == 0


def test_custom_generator_forgot_commit_warns(client):
    """The warning fires through the full arm -> flush -> warn path on a custom
    ``session_generator`` too, not only the built-in factory: a custom generator
    constructs/yields/cleans-up (no commit), so a route that forgot to commit is
    still caught."""
    _build(client)

    async def custom_gen():
        async with _fr_globals.async_make_session() as session:
            yield session

    fr.configure(session_generator=custom_gen)
    assert _warn_count(lambda: client.post("/things/forgot")) >= 1


def test_warn_on_uncommitted_false_disables(client):
    _build(client)
    fr.configure(warn_on_uncommitted=False)
    assert _warn_count(lambda: client.post("/things/forgot")) == 0
