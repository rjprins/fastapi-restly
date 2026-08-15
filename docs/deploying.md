# Deploying

This page covers the parts of a production deployment that are specific to
FastAPI-Restly. Everything else (uvicorn workers, gunicorn, TLS, reverse
proxies, Docker, behind-a-proxy headers) is already covered well in
[FastAPI's deployment docs](https://fastapi.tiangolo.com/deployment/) and is
not duplicated here.

## Database configuration

In production, drive the engine from environment variables. A small
`pydantic-settings` shim keeps the wiring obvious and 12-factor friendly:

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    db_pool_size: int = 5
    db_max_overflow: int = 10
```

Instantiating `Settings()` reads `DATABASE_URL` and the pool fields from the
environment. [The production template below](#production-main-template) does
that inside `create_app()` and passes the values into
{func}`fr.configure() <fastapi_restly.db.configure>` through an explicit
engine, which is what lets you size the pool.

Sizing is the part Restly leaves to you. Given a PostgreSQL URL it already sets
`pool_pre_ping=True` and `pool_recycle=1800`, so the template below repeats
`pool_pre_ping` only to keep it visible next to the settings it belongs with;
see [Engine Defaults](technical_details.md#engine-defaults) for the full list
and for how to decline it. If your app already has an engine, pass that one
instead; see
[Reuse Your Existing Engine](howto_existing_project.md#reuse-your-existing-engine).

## Migrations with Alembic

In production, never call `metadata.create_all()`; use Alembic instead.
For the recommended `postgresql+asyncpg` setup, initialise Alembic with its
async template once in your project root:

```bash
alembic init -t async alembic
```

The generic template created by `alembic init alembic` builds a synchronous
engine and cannot open an async URL. Use it only when Alembic receives a
separate synchronous database URL. Alembic's
[asyncio recipe](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic)
shows the same template and how to adapt an existing environment.

Point `alembic/env.py` at the metadata of whichever declarative base your
models inherit from (typically {class}`fr.DataclassBase <fastapi_restly.models.DataclassBase>`).
Metadata covers the models that have been imported, so `env.py` needs a module
that has seen all of them. That is `main.py`: it reaches view registration,
which reaches every view, and each view imports its model. Because the factory
builds nothing at import time, this costs the imports and nothing else:

```python
# alembic/env.py
import fastapi_restly as fr
import myapp.main  # noqa: F401  (imports every view, and each view its model)

target_metadata = fr.DataclassBase.metadata
```

Models that no view reaches, such as an outbox or audit table, need importing
wherever they are used, at module level rather than inside a function. Run
`alembic check` in CI to catch one that is missed: it reports the absent model
as a dropped table rather than failing quietly.

Restly's declarative bases map plain `Mapped[datetime]` columns to
`DateTime(timezone=True)`. On PostgreSQL, upgrading an existing naive timestamp
column requires an explicit interpretation of the stored values. If they
represent UTC, use a reviewed migration such as:

```python
import sqlalchemy as sa
from alembic import op

op.alter_column(
    "event",
    "occurred_at",
    type_=sa.DateTime(timezone=True),
    postgresql_using="occurred_at AT TIME ZONE 'UTC'",
)
```

A bare type change can reinterpret values in the server's local timezone. Use
`mapped_column(DateTime())` on the model only when the column intentionally
stores a timezone-free wall-clock value.

Run migrations as part of your release or startup pipeline:

```bash
alembic upgrade head
```

To have tests exercise the same migration path, pass `alembic_upgrade=True` to
{func}`fr.testing.configure_tests() <fastapi_restly.testing.configure_tests>`.
Restly then runs `alembic upgrade head` against the test database before the
first test. See
[Test databases and migrations](howto_testing.md#test-databases-and-migrations).

(production-main-template)=

## A production `main.py` template

With settings and migrations in place, the pieces combine into one small
application factory:

```python
from contextlib import asynccontextmanager

import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from .api import register_views
from .settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    fr.configure(app, async_engine=engine, health="/health")
    register_views(app)
    return app
```

Note four details in this template:

- The factory keeps configuration out of module import: settings are read and
  the engine is built when `create_app()` runs, so a test suite can call the
  same factory with its own settings. See
  [Test APIs with RestlyTestClient and Fixtures](howto_testing.md).
- `register_views(app)` keeps view definitions free of registration side
  effects and makes `api.py` the one application composition boundary; see
  [Structure a Project](howto_project_structure.md).
- {func}`fr.configure(app, ...) <fastapi_restly.db.configure>` installs the default exception handlers
  (currently the translator that turns `IntegrityError` into a 409 response;
  see [Database conflicts](howto_error_responses.md#database-conflicts-integrityerror-to-409)).
  Pass `install_default_exception_handlers=False` to opt out. `health="/health"`
  mounts the [liveness endpoint](#health-checks).
- The engine belongs to the app the factory built: `engine.dispose()` in
  `lifespan` cleans up the connection pool on shutdown so workers exit
  promptly. Restly never disposes an engine itself, so an application that
  configures from a URL instead reaches its engine through
  {func}`fr.db.get_async_engine() <fastapi_restly.db.get_async_engine>`; see
  [Engine Disposal](technical_details.md#engine-disposal).

(running-the-app)=

## Running the app

Use any production ASGI runner. The most common options are
[uvicorn](https://www.uvicorn.org/) and
[gunicorn with uvicorn workers](https://www.uvicorn.org/deployment/#gunicorn).
See [FastAPI's deployment docs](https://fastapi.tiangolo.com/deployment/)
for the full picture, including TLS, reverse proxies, and Docker.

A minimal invocation runs the factory directly:

```bash
uvicorn "myapp.main:create_app" --factory --host 0.0.0.0 --port 8000 --workers 4
```

When your platform expects an application object rather than a factory, put it
in its own module and leave `main.py` alone:

```python
# myapp/asgi.py
from .main import create_app

app = create_app()
```

`uvicorn myapp.asgi:app` then works, while `main.py` stays free to import. The
FastAPI CLI cannot call a factory, so name that module for it and
`fastapi dev` and `fastapi run` work too:

```toml
[tool.fastapi]
entrypoint = "myapp.asgi:app"
```

Do not put `app = create_app()` at the bottom of `main.py` instead: with
required settings that makes importing the module require `DATABASE_URL`
everywhere, including `conftest.py` and `alembic/env.py`. Nothing inside the
package should import `asgi.py`; only the server does. Either way, build the
application once per process: Restly's session configuration is process-wide,
so a later factory call re-points every earlier app's requests at the new
database too. See [how a factory's apps share one
configuration](#factory-apps-share-one-configuration).

Sync {class}`RestView <fastapi_restly.views.RestView>` endpoints run on FastAPI's threadpool, so worker count
still has the usual effect; async {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` endpoints share the
event loop within a worker. Do not use `--reload` in production.

(health-checks)=

## Health checks

Naming a path in {func}`fr.configure() <fastapi_restly.db.configure>` mounts an
endpoint there:

```python
fr.configure(app, async_engine=engine, health="/health")
```

`GET /health` then answers `200` with `{"status": "ok"}`, and appears in `/docs`
like any other route. The path is yours to choose; `/healthz` and `/up` are
common alternatives. Omit the argument and there is no such route, and if your
application already has one at that path, Restly leaves it alone.

This is a liveness check: it reports that the process is up and answering, and
makes no database round-trip. It suits a probe that restarts the container on
failure:

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8000
```

A readiness probe, which takes the instance out of rotation rather than
restarting it, is the place to check the database and other dependencies.
Write that as an ordinary route so its checks and timeouts stay yours.

## See also

- [Use Restly in an Existing Project](howto_existing_project.md): wiring
  Restly into an app that already has an engine, sessions, and models.
- [Examples](examples.md): the production-shaped SaaS example to compare
  against. It includes PostgreSQL Compose services, validated settings, an
  application-owned async engine, migrations, and migration-backed tests.
