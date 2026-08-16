from logging.config import fileConfig

import fastapi_restly as fr
from alembic import context
from sqlalchemy import engine_from_config, pool

import app.main  # noqa: F401  (imports every view, and each view its models)
from app.settings import Settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Restly's test setup injects its own database URL here. A direct Alembic
# command falls back to the same settings the application uses.
if not config.get_main_option("sqlalchemy.url"):
    # Escaped because Alembic reads this through configparser interpolation.
    url = Settings.current.database_url.replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)

target_metadata = fr.DataclassBase.metadata

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


def run_migrations_online() -> None:
    """Open an engine for an online migration run."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=RENDER_AS_BATCH,
        )

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
