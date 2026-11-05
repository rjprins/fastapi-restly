"""The tests of tests/test_atomic_write_actions.py, on PostgreSQL with psycopg.

They are imported, so pytest runs them here with the ``tags`` fixture of this
module, sync and async.
"""

import pytest

from ..test_atomic_write_actions import *  # noqa: F403
from ..test_atomic_write_actions import tags_on_new_tables
from .conftest import PG_URL


@pytest.fixture(params=["sync", "async"])
async def tags(request):
    asynchronous = request.param == "async"
    async with tags_on_new_tables(PG_URL, asynchronous=asynchronous) as env:
        yield env
