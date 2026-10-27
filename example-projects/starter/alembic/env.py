import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.main  # noqa: F401  (imports every view, and each view its models)
from app.models import Base
from app.settings import get_settings

config = context.config

if config.config_file_name is not None:
    # Keep the loggers that exist already: the tests run these migrations in
    # their own process, and caplog sees nothing from a logger that is off.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Restly's test setup injects its own database URL here. A direct Alembic
# command falls back to the same settings the application uses.
if not config.get_main_option("sqlalchemy.url"):
    # Escaped because Alembic reads this through configparser interpolation.
    url = get_settings().database_url.replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)

target_metadata = Base.metadata

# SQLite has no real ALTER TABLE, so Alembic rebuilds a table to change one.
# Autogenerate then writes batch_alter_table blocks on every dialect, and only
# SQLite actually does the rebuild. Without this a second migration fails there.
RENDER_AS_BATCH = True


def run_migrations_offline() -> None:
    """Emit SQL without connecting to a database."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=RENDER_AS_BATCH,
    )

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
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=RENDER_AS_BATCH,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open an async engine for an online migration run."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run the async migrations from this synchronous entry point."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
