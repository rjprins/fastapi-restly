"""Shared helpers for the tests of the write side.

Each test declares its own models and views, then runs them on a real
SQLite file, sync and async. The engine gets a real outer transaction, so
savepoints work. Restly's two session factories are copied: the async one has
``autoflush=False``, the sync one keeps SQLAlchemy's ``autoflush=True``.
"""

from contextlib import asynccontextmanager
from inspect import isawaitable
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session

import fastapi_restly as fr

from ._models import SharedBase


def registered(*views):
    """Register each view on a new app, so it can be built with a session."""
    app = FastAPI()
    for view in views:
        fr.include_view(app)(view)
    return views[0] if len(views) == 1 else views


async def call(value):
    return await value if isawaitable(value) else value


@asynccontextmanager
async def enter(manager):
    if hasattr(manager, "__aenter__"):
        async with manager as value:
            yield value
    else:
        with manager as value:
            yield value


@pytest.fixture(params=["sync", "async"])
async def env(request, tmp_path):
    asynchronous = request.param == "async"
    database = tmp_path / "writes.db"
    if asynchronous:
        engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
        target_engine = engine.sync_engine
        make_session = lambda: AsyncSession(  # noqa: E731
            engine, expire_on_commit=False, autoflush=False
        )
    else:
        engine = target_engine = create_engine(f"sqlite:///{database}")
        make_session = lambda: Session(engine, expire_on_commit=False)  # noqa: E731

    @event.listens_for(target_engine, "connect")
    def connect(connection, record):
        connection.isolation_level = None
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(target_engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    statements: list[str] = []

    @event.listens_for(target_engine, "before_cursor_execute")
    def record(conn, cursor, statement, params, context, executemany):
        statements.append(" ".join(statement.split()))

    async def create_all(metadata=SharedBase.metadata):
        if asynchronous:
            async with engine.begin() as connection:
                await connection.run_sync(metadata.create_all)
        else:
            metadata.create_all(engine)

    try:
        yield SimpleNamespace(
            asynchronous=asynchronous,
            base=fr.AsyncRestView if asynchronous else fr.RestView,
            make_session=make_session,
            create_all=create_all,
            statements=statements,
            pick=lambda sync_fn, async_fn: async_fn if asynchronous else sync_fn,
        )
    finally:
        await call(engine.dispose())
