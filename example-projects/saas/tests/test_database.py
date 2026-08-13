"""Application-owned database engine contract."""

import fastapi_restly as fr


def test_the_factory_configured_the_asyncpg_engine() -> None:
    """The engine Restly serves requests with is the factory's asyncpg engine."""
    assert fr.db.get_async_engine().url.drivername == "postgresql+asyncpg"
