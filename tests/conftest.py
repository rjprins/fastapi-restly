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
    framework_bases = (fd.DataclassBase, fd.PlainBase)

    def _cleanup_registry(base_cls: type) -> None:
        class_registry = base_cls.registry._class_registry  # type: ignore[attr-defined]
        for key, value in list(class_registry.items()):
            if key == "_sa_module_registry":
                continue
            if value.__class__.__name__ == "_MultipleClassMarker":
                class_registry.pop(key, None)
                continue
            if (
                isinstance(value, type)
                and issubclass(value, base_cls)
                and "<locals>" in getattr(value, "__qualname__", "")
            ):
                class_registry.pop(key, None)

    for base_cls in framework_bases:
        _cleanup_registry(base_cls)
        base_cls.metadata.clear()
    set_query_modifier_version(QueryModifierVersion.V1)
    yield
    for base_cls in framework_bases:
        _cleanup_registry(base_cls)
        base_cls.metadata.clear()
    set_query_modifier_version(QueryModifierVersion.V1)


@pytest.fixture(autouse=True)
def setup_database_connection():
    fd.configure(async_database_url="sqlite+aiosqlite:///:memory:")


def create_tables():
    async def create_tables():
        engine = fd.get_async_engine()
        async with engine.begin() as conn:
            await conn.run_sync(fd.DataclassBase.metadata.create_all)

    asyncio.run(create_tables())
