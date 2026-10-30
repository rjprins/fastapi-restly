"""The tests of tests/test_write_action_error.py, on PostgreSQL with psycopg.

They are imported, so pytest runs them here with the ``orders`` fixture of
this module, sync and async.
"""

import pytest

from ..test_write_action_error import *  # noqa: F403
from ..test_write_action_error import orders_on_new_tables
from .conftest import PG_URL


@pytest.fixture(params=["sync", "async"])
async def orders(request):
    asynchronous = request.param == "async"
    async with orders_on_new_tables(PG_URL, asynchronous=asynchronous) as env:
        yield env
