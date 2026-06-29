from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from _pytest.outcomes import Exit
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

import fastapi_restly._pytest_fixtures as _fixtures
import fastapi_restly.pytest_fixtures as exported_fixtures
import fastapi_restly.testing as testing
from fastapi_restly.db._globals import RestlyContext
from fastapi_restly.testing._client import RestlyTestClient


class FixtureTestBase(DeclarativeBase):
    pass


class FixtureItem(FixtureTestBase):
    __tablename__ = "fixture_item"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str]


def test_restly_project_root_discovers_pyproject(monkeypatch, tmp_path: Path):
    project_root = tmp_path / "project"
    nested = project_root / "src" / "deep"
    nested.mkdir(parents=True)
    (project_root / "pyproject.toml").write_text("[project]\nname='demo'\n")

    monkeypatch.chdir(nested)

    assert _fixtures.restly_project_root.__wrapped__() == project_root


def test_restly_project_root_raises_without_pyproject(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(Exception, match="Could not find a pyproject.toml"):
        _fixtures.restly_project_root.__wrapped__()


def test_run_alembic_upgrade_handles_missing_and_failing_migrations(tmp_path: Path):
    _fixtures._run_alembic_upgrade(tmp_path)

    project_root = tmp_path / "with-alembic"
    alembic_dir = project_root / "alembic"
    alembic_dir.mkdir(parents=True)
    (project_root / "alembic.ini").write_text("[alembic]\n")

    with patch("alembic.command.upgrade", side_effect=RuntimeError("boom")):
        with pytest.raises(Exit, match="Alembic migrations failed: boom"):
            _fixtures._run_alembic_upgrade(project_root)


def test_activate_savepoint_only_mode_sessions_activates_only_configured_sessions():
    with RestlyContext():
        with patch(
            "fastapi_restly._pytest_fixtures.activate_savepoint_only_mode"
        ) as activate:
            _fixtures._activate_savepoint_only_mode_sessions()
            activate.assert_not_called()

    sync_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sync_make_session = sessionmaker(bind=sync_engine, expire_on_commit=False)
    async_make_session = async_sessionmaker(bind=async_engine, expire_on_commit=False)

    try:
        with RestlyContext():
            from fastapi_restly.db._globals import _fr_globals

            _fr_globals.make_session = sync_make_session
            _fr_globals.async_make_session = async_make_session

            with patch(
                "fastapi_restly._pytest_fixtures.activate_savepoint_only_mode"
            ) as activate:
                _fixtures._activate_savepoint_only_mode_sessions()
                assert activate.call_count == 2
    finally:
        sync_engine.dispose()
        import asyncio

        asyncio.run(async_engine.dispose())


def test_shared_connection_yields_none_or_real_connection():
    with RestlyContext():
        gen = _fixtures._shared_connection.__wrapped__()
        assert next(gen) is None
        with pytest.raises(StopIteration):
            next(gen)

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    make_session = sessionmaker(bind=engine, expire_on_commit=False)

    try:
        with RestlyContext():
            from fastapi_restly.db._globals import _fr_globals

            _fr_globals.make_session = make_session

            gen = _fixtures._shared_connection.__wrapped__()
            conn = next(gen)
            assert conn.engine is engine
            gen.close()
    finally:
        engine.dispose()


def test_sync_fixture_wrapper_patches_and_restores_sessionmaker():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    make_session = sessionmaker(bind=engine, expire_on_commit=False)

    try:
        with RestlyContext():
            from fastapi_restly.db._globals import _fr_globals

            _fr_globals.make_session = make_session
            gen = _fixtures.restly_session.__wrapped__(None)
            session = next(gen)

            mocked_make_session = _fr_globals.make_session
            assert mocked_make_session is not make_session
            assert mocked_make_session() is session
            assert mocked_make_session.begin.return_value.__enter__() is session

            with pytest.raises(StopIteration):
                next(gen)

            assert _fr_globals.make_session is make_session
    finally:
        engine.dispose()


def test_sync_fixture_begin_context_flushes_on_successful_exit():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    make_session = sessionmaker(bind=engine, expire_on_commit=False)

    try:
        FixtureTestBase.metadata.create_all(engine)

        with RestlyContext():
            from fastapi_restly.db._globals import _fr_globals

            _fr_globals.make_session = make_session
            with engine.connect() as conn:
                gen = _fixtures.restly_session.__wrapped__(conn)
                next(gen)

                item = FixtureItem(name="sync")
                with _fr_globals.make_session.begin() as session:
                    session.add(item)

                assert item.id is not None

                with pytest.raises(StopIteration):
                    next(gen)
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_async_fixture_wrapper_patches_and_restores_sessionmaker():
    async_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    make_session = async_sessionmaker(bind=async_engine, expire_on_commit=False)

    try:
        with RestlyContext():
            from fastapi_restly.db._globals import _fr_globals

            _fr_globals.async_make_session = make_session
            agen = _fixtures.restly_async_session.__wrapped__(None)
            session = await agen.__anext__()

            mocked_make_session = _fr_globals.async_make_session
            assert mocked_make_session is not make_session
            async with mocked_make_session() as context_session:
                assert context_session is session
            async with mocked_make_session.begin() as context_session:
                assert context_session is session

            await agen.aclose()
            assert _fr_globals.async_make_session is make_session
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_async_fixture_begin_context_flushes_on_successful_exit():
    async_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    make_session = async_sessionmaker(bind=async_engine, expire_on_commit=False)

    try:
        async with async_engine.begin() as conn:
            await conn.run_sync(FixtureTestBase.metadata.create_all)

        with RestlyContext():
            from fastapi_restly.db._globals import _fr_globals

            _fr_globals.async_make_session = make_session
            agen = _fixtures.restly_async_session.__wrapped__(None)
            await agen.__anext__()

            item = FixtureItem(name="async")
            async with _fr_globals.async_make_session.begin() as session:
                session.add(item)

            assert item.id is not None

            await agen.aclose()
    finally:
        await async_engine.dispose()


def test_fixture_exports_and_client_helpers():
    app = _fixtures.restly_app.__wrapped__()
    assert isinstance(app, FastAPI)

    client = _fixtures.restly_client.__wrapped__(app)
    assert isinstance(client, RestlyTestClient)

    assert exported_fixtures.restly_app is _fixtures.restly_app
    assert exported_fixtures.restly_session is _fixtures.restly_session
    assert "restly_app" in exported_fixtures.__all__
    assert "restly_session" in exported_fixtures.__all__
    assert "app" not in exported_fixtures.__all__
    assert "client" not in exported_fixtures.__all__
    assert "session" not in exported_fixtures.__all__
    assert "autouse_alembic_upgrade" not in exported_fixtures.__all__
    assert "autouse_savepoint_only_mode_sessions" not in exported_fixtures.__all__
    assert testing.__all__ == [
        "RestlyTestClient",
        "activate_savepoint_only_mode",
        "deactivate_savepoint_only_mode",
    ]
    assert not hasattr(testing, "app")
    assert not hasattr(testing, "session")


def _run_with_blocked_imports(
    import_statement: str, *blocked_modules: str
) -> subprocess.CompletedProcess[str]:
    blocked = repr(blocked_modules)
    code = f"""
import builtins
import sys
real_import = builtins.__import__
blocked_modules = {blocked}

for module_name in list(sys.modules):
    if any(
        module_name == blocked or module_name.startswith(blocked + ".")
        for blocked in blocked_modules
    ):
        del sys.modules[module_name]

def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
    for blocked in blocked_modules:
        if name == blocked or name.startswith(blocked + "."):
            raise ModuleNotFoundError(
                f"No module named '{{blocked}}'", name=blocked
            )
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = blocked_import
{import_statement}
"""

    return subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True
    )


def test_testing_namespace_reports_missing_optional_dependencies():
    result = _run_with_blocked_imports(
        "import fastapi_restly.testing", "httpx", "httpx2"
    )

    assert result.returncode != 0
    assert 'pip install "fastapi-restly[testing]"' in result.stderr


def test_pytest_plugin_imports_without_httpx():
    result = _run_with_blocked_imports(
        """
import fastapi_restly.pytest_fixtures as fixtures
assert "restly_client" in fixtures.__all__
""",
        "httpx",
        "httpx2",
    )

    assert result.returncode == 0


def test_restly_client_reports_missing_optional_dependencies():
    result = _run_with_blocked_imports(
        """
from fastapi_restly.pytest_fixtures import restly_app, restly_client
restly_client.__wrapped__(restly_app.__wrapped__())
""",
        "httpx",
        "httpx2",
    )

    assert result.returncode != 0
    assert 'pip install "fastapi-restly[testing]"' in result.stderr


def test_pytest_plugin_reports_missing_pytest():
    """If pytest itself is absent, the plugin surfaces the [testing] extras hint.

    Unlike httpx (imported lazily), pytest is a top-level import of the private
    fixtures module, so blocking it exercises the friendly re-raise in
    ``pytest_fixtures.py``.
    """
    result = _run_with_blocked_imports("import fastapi_restly.pytest_fixtures", "pytest")

    assert result.returncode != 0
    assert 'pip install "fastapi-restly[testing]"' in result.stderr


def test_pytest_plugin_imports_without_pytest_asyncio():
    result = _run_with_blocked_imports(
        """
import fastapi_restly.pytest_fixtures as fixtures
assert "restly_async_session" in fixtures.__all__
""",
        "pytest_asyncio",
    )

    assert result.returncode == 0


def test_restly_async_session_reports_missing_optional_dependencies():
    result = _run_with_blocked_imports(
        """
from fastapi_restly.pytest_fixtures import restly_async_session
restly_async_session.__wrapped__(None)
""",
        "pytest_asyncio",
    )

    assert result.returncode != 0
    assert 'pip install "fastapi-restly[testing]"' in result.stderr
