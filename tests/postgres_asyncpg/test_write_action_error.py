"""The tests of tests/test_write_action_error.py, on PostgreSQL with asyncpg.

They are imported, so pytest runs them here with the ``orders`` fixture of
this module.
"""

import pytest

from ..test_write_action_error import *  # noqa: F403
from ..test_write_action_error import orders_on_new_tables
from .conftest import ASYNCPG_URL


@pytest.fixture
async def orders():
    async with orders_on_new_tables(ASYNCPG_URL, asynchronous=True) as env:
        yield env
