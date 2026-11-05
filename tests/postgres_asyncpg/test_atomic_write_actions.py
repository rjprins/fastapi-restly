"""The tests of tests/test_atomic_write_actions.py, on PostgreSQL with asyncpg.

They are imported, so pytest runs them here with the ``tags`` fixture of this
module.
"""

import pytest

from ..test_atomic_write_actions import *  # noqa: F403
from ..test_atomic_write_actions import tags_on_new_tables
from .conftest import ASYNCPG_URL


@pytest.fixture
async def tags():
    async with tags_on_new_tables(ASYNCPG_URL, asynchronous=True) as env:
        yield env
