"""Application-owned database engine contract."""

from fastapi import FastAPI

import fastapi_restly as fr


def test_the_factory_configured_the_asyncpg_engine(restly_app: FastAPI) -> None:
    """Restly serves requests with the exact engine the factory's lifespan owns.

    Not just a same-driver engine: identity, so a factory that configured
    Restly with one engine while disposing a different one at shutdown
    would fail this, even though both engines speak asyncpg.
    """
    assert fr.db.get_async_engine() is restly_app.state.engine
    assert fr.db.get_async_engine().pool._pre_ping is True
