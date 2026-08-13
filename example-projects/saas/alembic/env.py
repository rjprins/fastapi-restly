"""Async Alembic environment for the SaaS application."""

import asyncio
from logging.config import fileConfig

from alembic import context
from app import models  # noqa: F401
from app.settings import Settings
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import fastapi_restly as fr

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Restly's test setup injects its database URL into the Alembic config. Direct
# Alembic commands fall back to the same validated settings as the application.
if not config.get_main_option("sqlalchemy.url"):
    database_url = Settings().sqlalchemy_database_url.replace("%", "%%")
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = fr.DataclassBase.metadata


def run_migrations_offline() -> None:
    """Run migrations without creating an engine."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations through a synchronous connection adapter."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine for an online migration run."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in online mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
