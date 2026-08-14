"""Application-owned database engine contract."""

from fastapi import FastAPI

import fastapi_restly as fr


def test_the_factory_configured_the_asyncpg_engine(restly_app: FastAPI) -> None:
    """Restly serves requests with the same engine object app.state.engine holds.

    Not just a same-driver check: identity between what get_async_engine()
    returns and what the factory stashed on app.state, plus a pool setting the
    factory sets itself. Restly's engine defaults apply only to engines it
    builds from a URL, and this app passes its own, so pre-ping here proves the
    factory's engine is the one serving requests. A factory that configured
    Restly with a different engine than the one it exposed on app.state would
    fail this.
    """
    assert fr.db.get_async_engine() is restly_app.state.engine
    # SQLAlchemy has no public accessor for this; _pre_ping is private API.
    assert fr.db.get_async_engine().pool._pre_ping is True
