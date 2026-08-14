"""Application-owned database engine contract."""

import fastapi_restly as fr


def test_the_factory_configured_the_asyncpg_engine() -> None:
    """Requests run on the engine the factory built, with its pool settings.

    Restly's engine defaults apply only to engines it builds from a URL, and
    this app passes its own, so pre-ping being on proves the factory's engine
    reached Restly rather than a default one built somewhere else.
    """
    # SQLAlchemy has no public accessor for this; _pre_ping is private API.
    assert fr.db.get_async_engine().pool._pre_ping is True
