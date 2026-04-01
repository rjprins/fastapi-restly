# Import app.main to set up database connection before fixtures run
import asyncio
from pathlib import Path

import app.main  # noqa: F401

from fastapi_restly.db import get_async_engine
from fastapi_restly.models import DataclassBase


async def _create_tables():
    """Create all tables asynchronously."""
    async with get_async_engine().begin() as conn:
        await conn.run_sync(DataclassBase.metadata.create_all)


# Delete the database file if it exists and create fresh tables
db_file = Path("saas.db")
if db_file.exists():
    db_file.unlink()

asyncio.run(_create_tables())
