"""Pytest configuration and shared fixtures."""

import asyncio
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.clsregistry import _ModuleMarker, _MultipleClassMarker
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr
from fastapi_restly.db._globals import _fr_globals
from fastapi_restly.pytest_fixtures import restly_client as restly_client

# _cleanup_registry below scrubs SQLAlchemy's private declarative registry
# between tests (see fznb.7 for why the suite depends on these internals).
# Importing the marker types here is the first tripwire: if SA renames either,
# collection fails immediately naming the symbol, instead of the nested walk
# going silently inert and resurrecting the SAWarning flood e4e5623 / 389a800
# were written to kill.
_SA_MODULE_REGISTRY_KEY = "_sa_module_registry"


@pytest.fixture
def client(restly_client):
    """Project-local alias for existing framework tests."""
    return restly_client


def _cleanup_registry(base_cls: type) -> None:
    """Drop test-local model registrations from ``base_cls``'s SQLAlchemy registry.

    Tests declare models in function locals on shared bases, so their
    registrations leak across tests without this scrub (fznb.7). Every access to
    SQLAlchemy's private registry internals is direct: a moved or renamed
    internal raises here, naming what moved, rather than skipping the walk and
    letting the SAWarning flood return.
    """
    class_registry = base_cls.registry._class_registry  # type: ignore[attr-defined]

    module_registry = class_registry.get(_SA_MODULE_REGISTRY_KEY)
    if module_registry is None:
        # SA creates the module registry the first time any class is added, so
        # class entries with no module registry means SA moved the module tree.
        if any(key != _SA_MODULE_REGISTRY_KEY for key in class_registry):
            raise RuntimeError(
                f"class_registry holds classes but no {_SA_MODULE_REGISTRY_KEY!r}; "
                "SQLAlchemy moved the module tree -- update this cleanup (fznb.7)."
            )
    elif not isinstance(module_registry, _ModuleMarker):
        raise RuntimeError(
            f"{_SA_MODULE_REGISTRY_KEY!r} is a {type(module_registry).__name__}, "
            "expected _ModuleMarker; SQLAlchemy internals moved -- update this "
            "cleanup (fznb.7)."
        )

    # Outer pass: drop locally-scoped classes and name-collision markers from the
    # top-level registry.
    for key, value in list(class_registry.items()):
        if key == _SA_MODULE_REGISTRY_KEY:
            continue
        if isinstance(value, _MultipleClassMarker):
            class_registry.pop(key, None)
            continue
        if (
            isinstance(value, type)
            and issubclass(value, base_cls)
            and "<locals>" in getattr(value, "__qualname__", "")
        ):
            class_registry.pop(key, None)

    # Inner pass: SA stores each class a second time under module_name →
    # class_name. Without scrubbing it too, the next test redefining the same
    # model triggers an SAWarning.
    if module_registry is None:
        return
    for module_marker in list(module_registry.contents.values()):
        if not isinstance(module_marker, _ModuleMarker):
            raise RuntimeError(
                f"module tree holds a {type(module_marker).__name__}, expected "
                "_ModuleMarker; SQLAlchemy internals moved (fznb.7)."
            )
        entries = module_marker.contents
        for name, marker in list(entries.items()):
            if isinstance(marker, (_ModuleMarker, _MultipleClassMarker)):
                entries.pop(name, None)
            else:
                raise RuntimeError(
                    f"unexpected {type(marker).__name__} under {module_marker.name!r} "
                    "in SQLAlchemy's module tree -- update this cleanup (fznb.7)."
                )


@pytest.fixture(autouse=True)
def reset_metadata():
    """Reset global framework state between tests to avoid cross-test leakage."""
    framework_bases = (fr.DataclassBase,)
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
    # ``_fr_globals`` and don't tear them down).
    _fr_globals.session_generator = None
    _fr_globals.sync_session_generator = None
    _fr_globals.make_session = None
    fr.configure(async_database_url="sqlite+aiosqlite:///:memory:")
    yield
    # Dispose the per-test async engine so its pooled aiosqlite connection is
    # closed explicitly. aiosqlite >=0.22 warns from Connection.__del__ when a
    # connection is GC'd unclosed, which filterwarnings=error turns into
    # spurious teardown failures attributed to whatever test GC happens to run
    # under.
    async_make_session = _fr_globals.async_make_session
    if async_make_session is not None:
        asyncio.run(async_make_session.kw["bind"].dispose())


@pytest.fixture
def sync_db() -> Iterator[tuple[Engine, sessionmaker[Session]]]:
    """Configure an in-memory SQLite engine for sync RestView tests."""
    original_database_url = _fr_globals.database_url
    original_make_session = _fr_globals.make_session
    original_sync_session_generator = _fr_globals.sync_session_generator

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    make_session = sessionmaker(bind=engine, expire_on_commit=False)
    fr.configure(make_session=make_session)

    try:
        yield engine, make_session
    finally:
        _fr_globals.database_url = original_database_url
        _fr_globals.make_session = original_make_session
        _fr_globals.sync_session_generator = original_sync_session_generator
        engine.dispose()


def create_tables():
    async def create_tables():
        await fr.db.async_create_all(fr.DataclassBase)

    asyncio.run(create_tables())
