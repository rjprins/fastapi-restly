"""State behind :func:`fastapi_restly.testing.configure`.

Lives at the package root rather than in ``testing/`` so the pytest plugin can
read it without importing ``testing/__init__.py``, which pulls in the HTTP test
client (and with it httpx). Users reach it as ``fr.testing.configure()``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .db import _session
from .db._globals import _fr_globals
from .exc import RestlyConfigurationError

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy import Engine, MetaData
    from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
    from sqlalchemy.orm import DeclarativeBase, sessionmaker


@dataclass(frozen=True)
class _TestSetup:
    """What :func:`configure` recorded, read by the plugin's autouse fixtures."""

    app: Any
    create_all_from: Any
    alembic_upgrade: bool | str | Path


_setup: _TestSetup | None = None


def _current_setup() -> _TestSetup | None:
    """Return the active test setup, or None when ``configure()`` was never called.

    The plugin's autouse fixtures are inert while this is None, so a project that
    does not opt in keeps the fixtures-on-request behaviour.
    """
    return _setup


def _reset_setup() -> None:
    """Drop the recorded setup. For Restly's own tests, not for user suites."""
    global _setup
    _setup = None


def configure_tests(
    *,
    app: FastAPI | None = None,
    database_url: str | None = None,
    async_database_url: str | None = None,
    engine: Engine | None = None,
    async_engine: AsyncEngine | None = None,
    make_session: sessionmaker[Any] | None = None,
    async_make_session: async_sessionmaker[Any] | None = None,
    create_all_from: type[DeclarativeBase] | MetaData | None = None,
    alembic_upgrade: bool | str | Path = False,
) -> None:
    """Configure a Restly test suite in one call, from ``conftest.py``.

    The testing counterpart to :func:`fastapi_restly.configure`. Call it at
    ``conftest.py`` import time::

        import fastapi_restly as fr
        from myapp.main import app
        from myapp.models import Base

        fr.testing.configure_tests(
            app=app,
            async_database_url="sqlite+aiosqlite:///./test.db",
            create_all_from=Base,
        )

    That is the whole setup. Every test then runs against ``app`` on the given
    database, isolated in a transaction that rolls back afterwards, including
    tests that only drive ``restly_client``.

    Four things happen, in this order:

    1. The database arguments are forwarded to :func:`fastapi_restly.configure`,
       replacing whatever your application module configured on import. They are
       the same arguments under the same names.
    2. ``app`` becomes what the ``restly_app`` fixture returns, so ``restly_client``
       wraps your application without an override fixture.
    3. The schema is created once per session, before any test runs, from
       ``create_all_from`` or ``alembic_upgrade`` (see below).
    4. Every test is wrapped in savepoint isolation, on whichever of the sync and
       async legs is configured.

    Schema setup is optional and the two options are mutually exclusive:

    * ``create_all_from=Base`` builds the schema straight from your models, as
      :func:`fastapi_restly.db.create_all` does.
    * ``alembic_upgrade=True`` runs ``alembic upgrade head`` through ``alembic.ini``
      next to your project root; pass a path to point at a different config.
      Restly sets ``sqlalchemy.url`` on the config to the database configured
      here, so a stock ``env.py`` that reads it migrates the test database rather
      than your development one.
    * Passing neither leaves the schema to you, which is the right choice when
      your suite already builds it or your migrations are not Alembic.

    Always name the database. If one is already configured (your application
    module calls :func:`fastapi_restly.configure` on import) and you pass no
    database argument, this raises rather than guess: that database is usually
    the development one, and the schema step would create tables in it.
    """
    global _setup

    if create_all_from is not None and alembic_upgrade:
        raise RestlyConfigurationError(
            "fr.testing.configure_tests() got both create_all_from= and "
            "alembic_upgrade=. They are two ways to build the same schema: pass "
            "create_all_from=<Base> to create the tables from your models, or "
            "alembic_upgrade=True to run your migrations, not both."
        )

    database_arguments = (
        database_url,
        async_database_url,
        engine,
        async_engine,
        make_session,
        async_make_session,
    )
    if any(argument is not None for argument in database_arguments):
        _session.configure(
            app=app,
            database_url=database_url,
            async_database_url=async_database_url,
            engine=engine,
            async_engine=async_engine,
            make_session=make_session,
            async_make_session=async_make_session,
        )
    else:
        _reject_inherited_database()

    _setup = _TestSetup(
        app=app, create_all_from=create_all_from, alembic_upgrade=alembic_upgrade
    )


def _reject_inherited_database() -> None:
    """Refuse to run the tests against a database nobody named here.

    Reached when ``configure_tests()`` got no database argument but one is already
    configured, which normally means the application module configured it on
    import. Silently inheriting it would point the schema step at that database,
    and ``create_all``/``alembic upgrade`` is DDL: it survives the per-test
    rollback.
    """
    if _fr_globals.make_session is None and _fr_globals.async_make_session is None:
        return  # No database anywhere: a suite that never touches one is fine.

    configured = _fr_globals.database_url or _fr_globals.async_database_url
    named = f" ({configured})" if configured else ""
    raise RestlyConfigurationError(
        f"fr.testing.configure_tests() got no database argument, but a database"
        f"{named} is already configured -- usually the development one, "
        "configured when your application module was imported. Restly will not "
        "run the tests against it: creating the schema there would leave tables "
        "behind, since DDL survives the per-test rollback. Pass the test "
        "database explicitly, e.g. database_url= or async_database_url=. If the "
        "configured database really is the test one, pass the same URL here."
    )


def _create_schema(setup: _TestSetup, root: Path | None = None) -> None:
    """Build the schema described by ``setup``, once, before the first test.

    ``root`` anchors relative Alembic paths; the plugin passes pytest's rootdir so
    the config is found no matter which directory pytest was invoked from.
    """
    if setup.create_all_from is not None:
        # Prefer the sync leg: it needs no event loop. Either leg creates the
        # tables the other one sees, since both point at the same database.
        if _fr_globals.make_session is not None:
            _session.create_all(setup.create_all_from)
        elif _fr_globals.async_make_session is not None:
            asyncio.run(_session.async_create_all(setup.create_all_from))
        else:
            raise RestlyConfigurationError(
                "fr.testing.configure(create_all_from=...) needs a configured "
                "database. Pass database_url= or async_database_url= to "
                "fr.testing.configure(), or call fr.configure() before it."
            )
    elif setup.alembic_upgrade:
        _run_alembic_upgrade(setup.alembic_upgrade, root)


def _run_alembic_upgrade(
    alembic_upgrade: bool | str | Path, root: Path | None = None
) -> None:
    """Run ``alembic upgrade head`` against the configured test database."""
    try:
        from alembic import command
        from alembic.config import Config
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on the env
        raise ModuleNotFoundError(
            "fr.testing.configure(alembic_upgrade=...) requires Alembic. "
            "Install it with: pip install alembic",
            name="alembic",
        ) from exc

    base = root if root is not None else Path.cwd()
    given = Path("alembic.ini" if alembic_upgrade is True else str(alembic_upgrade))
    # Anchor to the project, not the directory pytest happened to run from, so the
    # suite migrates the same schema wherever it is invoked.
    ini_path = given if given.is_absolute() else base / given
    if not ini_path.exists():
        raise RestlyConfigurationError(
            f"fr.testing.configure(alembic_upgrade=...) found no Alembic config at "
            f"{str(ini_path)!r}. Pass a path relative to your project root, e.g. "
            "alembic_upgrade='backend/alembic.ini'."
        )

    config = Config(str(ini_path))
    # script_location is normally relative to the config file; resolve it here so
    # the migrations are found regardless of the working directory.
    script_location = config.get_main_option("script_location")
    if script_location and not Path(script_location).is_absolute():
        config.set_main_option(
            "script_location", str((ini_path.parent / script_location).resolve())
        )
    # Point Alembic at the database configured for the tests. Without this the
    # upgrade runs against whatever env.py resolves on its own, typically the
    # development database, leaving the test database unmigrated.
    url = _fr_globals.database_url or _fr_globals.async_database_url
    if url is not None:
        config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
