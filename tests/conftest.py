"""Pytest configuration and shared fixtures."""

import asyncio
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr
from fastapi_restly.db import fr_globals

pytest_plugins = ["fastapi_restly.testing._fixtures"]


@pytest.fixture(autouse=True)
def reset_metadata():
    """Reset global framework state between tests to avoid cross-test leakage."""
    framework_bases = (fr.DataclassBase,)

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

        # Also walk the nested _sa_module_registry. SA stores the class a
        # second time under module_name → class_name; without this pass the
        # next test redefining the same model triggers an SAWarning.
        module_registry = class_registry.get("_sa_module_registry")
        if module_registry is None:
            return
        for module_marker in list(getattr(module_registry, "contents", {}).values()):
            inner = getattr(module_marker, "contents", None)
            if inner is None:
                continue
            for class_name, marker in list(inner.items()):
                if marker.__class__.__name__ == "_MultipleClassMarker":
                    inner.pop(class_name, None)
                    continue
                cls_ref = getattr(marker, "cls", None)
                cls = cls_ref() if callable(cls_ref) else None
                if cls is None or "<locals>" in getattr(cls, "__qualname__", ""):
                    inner.pop(class_name, None)

    for base_cls in framework_bases:
        _cleanup_registry(base_cls)
        base_cls.metadata.clear()
    yield
    for base_cls in framework_bases:
        _cleanup_registry(base_cls)
        base_cls.metadata.clear()


@pytest.fixture(autouse=True)
def setup_database_connection():
    # Clear any sticky state left behind by previous tests (e.g. tests that
    # plug a custom ``session_generator`` / ``sync_session_generator`` into
    # ``fr_globals`` and don't tear them down).
    fr_globals.session_generator = None
    fr_globals.sync_session_generator = None
    fr_globals.make_session = None
    fr.configure(async_database_url="sqlite+aiosqlite:///:memory:")


@pytest.fixture
def sync_db() -> Iterator[tuple[Engine, sessionmaker[Session]]]:
    """Configure an in-memory SQLite engine for sync RestView tests."""
    original_database_url = fr_globals.database_url
    original_make_session = fr_globals.make_session
    original_sync_session_generator = fr_globals.sync_session_generator

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    make_session = sessionmaker(bind=engine, expire_on_commit=False)
    fr.configure(make_session=make_session)

    try:
        yield engine, make_session
    finally:
        fr_globals.database_url = original_database_url
        fr_globals.make_session = original_make_session
        fr_globals.sync_session_generator = original_sync_session_generator
        engine.dispose()


def create_tables():
    async def create_tables():
        engine = fr.get_async_engine()
        async with engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)

    asyncio.run(create_tables())
