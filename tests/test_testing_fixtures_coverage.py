from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from _pytest.outcomes import Exit
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import fastapi_restly.pytest_fixtures as exported_fixtures
from fastapi_restly.db._globals import FRGlobals, use_fr_globals
from fastapi_restly.testing import _fixtures
from fastapi_restly.testing._client import RestlyTestClient


def test_project_root_discovers_pyproject(monkeypatch, tmp_path: Path):
    project_root = tmp_path / "project"
    nested = project_root / "src" / "deep"
    nested.mkdir(parents=True)
    (project_root / "pyproject.toml").write_text("[project]\nname='demo'\n")

    monkeypatch.chdir(nested)

    assert _fixtures.project_root.__wrapped__() == project_root


def test_project_root_raises_without_pyproject(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(Exception, match="Could not find a pyproject.toml"):
        _fixtures.project_root.__wrapped__()


def test_autouse_alembic_upgrade_handles_missing_and_failing_migrations(tmp_path: Path):
    _fixtures.autouse_alembic_upgrade.__wrapped__(tmp_path)

    project_root = tmp_path / "with-alembic"
    alembic_dir = project_root / "alembic"
    alembic_dir.mkdir(parents=True)
    (project_root / "alembic.ini").write_text("[alembic]\n")

    with patch("alembic.command.upgrade", side_effect=RuntimeError("boom")):
        with pytest.raises(Exit, match="Alembic migrations failed: boom"):
            _fixtures.autouse_alembic_upgrade.__wrapped__(project_root)


def test_autouse_savepoint_only_mode_sessions_activates_only_configured_sessions():
    with use_fr_globals(FRGlobals()):
        with patch(
            "fastapi_restly.testing._fixtures.activate_savepoint_only_mode"
        ) as activate:
            _fixtures.autouse_savepoint_only_mode_sessions.__wrapped__()
            activate.assert_not_called()

    sync_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sync_make_session = sessionmaker(bind=sync_engine, expire_on_commit=False)
    async_make_session = async_sessionmaker(bind=async_engine, expire_on_commit=False)

    try:
        with use_fr_globals(FRGlobals()):
            from fastapi_restly.db import fr_globals

            fr_globals.make_session = sync_make_session
            fr_globals.async_make_session = async_make_session

            with patch(
                "fastapi_restly.testing._fixtures.activate_savepoint_only_mode"
            ) as activate:
                _fixtures.autouse_savepoint_only_mode_sessions.__wrapped__()
                assert activate.call_count == 2
    finally:
        sync_engine.dispose()
        import asyncio

        asyncio.run(async_engine.dispose())


def test_shared_connection_yields_none_or_real_connection():
    with use_fr_globals(FRGlobals()):
        gen = _fixtures._shared_connection.__wrapped__()
        assert next(gen) is None
        with pytest.raises(StopIteration):
            next(gen)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    make_session = sessionmaker(bind=engine, expire_on_commit=False)

    try:
        with use_fr_globals(FRGlobals()):
            from fastapi_restly.db import fr_globals

            fr_globals.make_session = make_session

            gen = _fixtures._shared_connection.__wrapped__()
            conn = next(gen)
            assert conn.engine is engine
            gen.close()
    finally:
        engine.dispose()


def test_sync_fixture_wrapper_patches_and_restores_sessionmaker():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    make_session = sessionmaker(bind=engine, expire_on_commit=False)

    try:
        with use_fr_globals(FRGlobals()):
            from fastapi_restly.db import fr_globals

            fr_globals.make_session = make_session
            gen = _fixtures.session.__wrapped__(None)
            session = next(gen)

            mocked_make_session = fr_globals.make_session
            assert mocked_make_session is not make_session
            assert mocked_make_session() is session
            assert mocked_make_session.begin.return_value.__enter__() is session

            with pytest.raises(StopIteration):
                next(gen)

            assert fr_globals.make_session is make_session
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_async_fixture_wrapper_patches_and_restores_sessionmaker():
    async_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    make_session = async_sessionmaker(bind=async_engine, expire_on_commit=False)

    try:
        with use_fr_globals(FRGlobals()):
            from fastapi_restly.db import fr_globals

            fr_globals.async_make_session = make_session
            agen = _fixtures.async_session.__wrapped__(None)
            session = await agen.__anext__()

            mocked_make_session = fr_globals.async_make_session
            assert mocked_make_session is not make_session
            assert await mocked_make_session() is session
            assert (
                await mocked_make_session.begin.return_value.__aenter__() is session
            )

            await agen.aclose()
            assert fr_globals.async_make_session is make_session
    finally:
        await async_engine.dispose()


def test_fixture_exports_and_client_helpers():
    app = _fixtures.app.__wrapped__()
    assert isinstance(app, FastAPI)

    client = _fixtures.client.__wrapped__(app)
    assert isinstance(client, RestlyTestClient)

    assert exported_fixtures.app is _fixtures.app
    assert exported_fixtures.session is _fixtures.session
    assert "app" in exported_fixtures.__all__
    assert "session" in exported_fixtures.__all__
