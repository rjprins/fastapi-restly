# Import app.main to set up database connection before fixtures run
import asyncio
from pathlib import Path

import app.main  # noqa: F401

from fastapi_restly.db import FRAsyncSession
from fastapi_restly.models import Base


async def _create_tables():
    """Create all tables asynchronously."""
    async_engine = FRAsyncSession.kw["bind"]
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# Delete the database file if it exists and create fresh tables
db_file = Path("saas.db")
if db_file.exists():
    db_file.unlink()

asyncio.run(_create_tables())
