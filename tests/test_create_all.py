"""Tests for ``fr.db.create_all`` / ``fr.db.async_create_all`` -- the dev/demo
table-bootstrap helpers that wrap ``metadata.create_all`` on the configured
engine (replacing the raw ``get_async_engine()`` + ``engine.begin()`` dance).

Uses a private declarative base (the autouse ``reset_metadata`` fixture clears
``fr.DataclassBase.metadata`` between tests) and ``StaticPool`` engines so the
in-memory SQLite database is shared across connections.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import StaticPool

import fastapi_restly as fr
from fastapi_restly.db._globals import RestlyContext


class _Base(DeclarativeBase):
    pass


class _CreateAllWidget(_Base):
    __tablename__ = "create_all_widget"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column()


def test_create_all_sync_creates_tables_from_base_and_metadata():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    try:
        with RestlyContext():
            fr.configure(engine=engine)
            # Accepts a DeclarativeBase subclass (uses its .metadata) ...
            fr.db.create_all(_Base)
            assert "create_all_widget" in sa_inspect(engine).get_table_names()
            # ... and a MetaData directly (idempotent second call).
            fr.db.create_all(_Base.metadata)
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_async_create_all_creates_tables():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    try:
        with RestlyContext():
            fr.configure(async_engine=engine)
            await fr.db.async_create_all(_Base)
            async with engine.connect() as conn:
                names = await conn.run_sync(lambda c: sa_inspect(c).get_table_names())
            assert "create_all_widget" in names
    finally:
        await engine.dispose()


def test_create_all_rejects_non_base():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    try:
        with RestlyContext():
            fr.configure(engine=engine)
            with pytest.raises(TypeError, match="DeclarativeBase subclass or a MetaData"):
                fr.db.create_all(object())  # type: ignore[arg-type]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_async_create_all_rejects_non_base():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    try:
        with RestlyContext():
            fr.configure(async_engine=engine)
            with pytest.raises(TypeError, match="DeclarativeBase subclass or a MetaData"):
                await fr.db.async_create_all(object())  # type: ignore[arg-type]
    finally:
        await engine.dispose()


def test_create_all_requires_configure():
    with RestlyContext():
        with pytest.raises(fr.exc.RestlyConfigurationError):
            fr.db.create_all(_Base)


@pytest.mark.asyncio
async def test_async_create_all_requires_configure():
    with RestlyContext():
        with pytest.raises(fr.exc.RestlyConfigurationError):
            await fr.db.async_create_all(_Base)
