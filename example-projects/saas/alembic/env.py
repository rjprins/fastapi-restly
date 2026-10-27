"""Async Alembic environment for the SaaS application."""

import asyncio
from logging.config import fileConfig

import app.main  # noqa: F401  (imports every view, and each view its models)
from alembic import context
from app.models import Base
from app.settings import get_settings
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    # Keep the loggers that exist already: tests that run these migrations in
    # their own process would see nothing from a logger that is off.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Restly's test setup injects its database URL into the Alembic config. Direct
# Alembic commands fall back to the same validated settings as the application.
if not config.get_main_option("sqlalchemy.url"):
    database_url = get_settings().sqlalchemy_database_url.replace("%", "%%")
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without creating an engine."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    if context.get_context().dialect.name == "postgresql":
        # The script runs in UTC too, as an online run does.
        context.execute("SET TIME ZONE 'UTC'")

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations through a synchronous connection adapter."""
    if connection.dialect.name == "postgresql":
        # Run in UTC, as the application's sessions do. PostgreSQL reads a stored time
        # in the zone of the session, for example when a migration gives a column a time
        # zone. The commit ends the transaction that the SET began, so Alembic begins
        # and commits its own.
        connection.exec_driver_sql("SET TIME ZONE 'UTC'")
        connection.commit()
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
