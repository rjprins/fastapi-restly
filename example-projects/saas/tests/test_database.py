"""Application-owned database engine contract."""

from app import database, main


def test_database_module_owns_the_asyncpg_engine() -> None:
    assert main.engine is database.engine
    assert database.engine.url.drivername == "postgresql+asyncpg"
