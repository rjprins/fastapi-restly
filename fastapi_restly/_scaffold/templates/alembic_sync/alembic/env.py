"""Alembic environment.

``myapp.main`` is imported for its side effect on the metadata: it reaches every
view, and each view imports its model, so ``fr.DataclassBase.metadata`` is
complete by the time Alembic reads it. A model no view reaches -- an outbox or
audit table -- must be imported wherever it is used, at module level.

Run ``alembic check`` in CI to catch one that is missed: it reports the absent
model as a dropped table rather than failing quietly.
"""

from logging.config import fileConfig

import fastapi_restly as fr
from sqlalchemy import engine_from_config, pool

import myapp.main  # noqa: F401  (imports every view, and each view its models)
from alembic import context
from myapp.settings import Settings

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

# SQLite has no real ALTER TABLE, so Alembic must rebuild a table to change one.
# Safe to leave on everywhere: it only takes effect for SQLite.
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
