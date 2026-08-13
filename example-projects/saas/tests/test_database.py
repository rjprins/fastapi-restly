"""Application-owned database engine contract."""

from app import database, main


def test_database_module_owns_a_pre_ping_asyncpg_engine() -> None:
    assert main.engine is database.engine
    assert database.engine.url.drivername == "postgresql+asyncpg"
    assert database.engine.pool._pre_ping is True
