"""Regression (fznb.11): a project that configures both a sync and an async
session source must be able to use ``restly_async_session``.

``restly_async_session`` binds to the connection the sync fixture already opened
(the ``_shared_connection`` fixture). It used to *enter* that pre-bound
connection, which raises ``InvalidRequestError("connection is already
started")`` -- so every test of a hybrid sync+async project errored in setup,
and so did any test requesting both session fixtures.

The suite never caught this because every in-repo test drives the async fixture
as ``restly_async_session.__wrapped__(None)``, where the shared connection is
absent. These tests run the fixtures through a real pytest session with both
sources configured, in both request orders, and show that a write through one
session is visible to the other: the two fixtures share one connection.
"""

from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]


_CONFTEST = """
import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Mapped
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr


class Gadget(fr.IDBase):
    name: Mapped[str]


sync_engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
async_engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)

fr.configure(engine=sync_engine, async_engine=async_engine)
fr.DataclassBase.metadata.create_all(sync_engine)


@pytest.fixture(scope="session", autouse=True)
def _dispose_engine():
    # Close the sync pool's connection explicitly; a GC'd connection warns, and
    # the suite runs under filterwarnings=error. The async engine never opens a
    # connection here (the async session runs over the shared sync connection),
    # so there is nothing to dispose on it.
    yield
    sync_engine.dispose()
"""

_TEST = """
from sqlalchemy import select

from conftest import Gadget


async def test_sync_write_visible_to_async(restly_session, restly_async_session):
    # Both fixtures share one connection and transaction, so a write through the
    # sync session is visible to the async session within the same test.
    restly_session.add(Gadget(name="alpha"))
    restly_session.commit()
    found = await restly_async_session.scalars(
        select(Gadget).where(Gadget.name == "alpha")
    )
    assert found.first() is not None


async def test_async_write_visible_to_sync(restly_async_session, restly_session):
    # Reverse the fixture order and the write direction.
    restly_async_session.add(Gadget(name="beta"))
    await restly_async_session.commit()
    found = restly_session.scalars(select(Gadget).where(Gadget.name == "beta"))
    assert found.first() is not None
"""


def test_shared_connection_is_used_by_both_fixtures(pytester: pytest.Pytester):
    pytester.makefile(
        ".toml",
        pyproject="""
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
""",
    )
    pytester.makeconftest(_CONFTEST)
    pytester.makepyfile(_TEST)

    # Subprocess, not in-process: the inner async tests spin up event loops
    # whose self-pipe sockets would otherwise be GC'd during a later outer test
    # and, under filterwarnings=error, fail it as an unraisable ResourceWarning.
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=2)
