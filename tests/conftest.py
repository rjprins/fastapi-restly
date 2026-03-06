"""Pytest configuration and shared fixtures."""

import asyncio
import pytest

import fastapi_restly as fd
from fastapi_restly.db import fr_globals
from fastapi_restly.query import QueryModifierVersion, set_query_modifier_version

pytest_plugins = ["fastapi_restly.testing._fixtures"]


@pytest.fixture(autouse=True)
def reset_metadata():
    """Reset global framework state between tests to avoid cross-test leakage."""
    class_registry = fd.Base.registry._class_registry  # type: ignore[attr-defined]

    def _cleanup_registry() -> None:
        for key, value in list(class_registry.items()):
            if key == "_sa_module_registry":
                continue
            if value.__class__.__name__ == "_MultipleClassMarker":
                class_registry.pop(key, None)
                continue
            if (
                isinstance(value, type)
                and issubclass(value, fd.Base)
                and "<locals>" in getattr(value, "__qualname__", "")
            ):
                class_registry.pop(key, None)

    _cleanup_registry()
    fd.Base.metadata.clear()
    set_query_modifier_version(QueryModifierVersion.V1)
    yield
    _cleanup_registry()
    fd.Base.metadata.clear()
    set_query_modifier_version(QueryModifierVersion.V1)


@pytest.fixture(autouse=True)
def setup_database_connection():
    fd.setup_async_database_connection("sqlite+aiosqlite:///:memory:")


def create_tables():
    async def create_tables():
        engine = fd.AsyncSession.kw["bind"]
        async with engine.begin() as conn:
            await conn.run_sync(fd.Base.metadata.create_all)

    asyncio.run(create_tables())
