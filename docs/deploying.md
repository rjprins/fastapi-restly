# Deploying

A Restly application is a FastAPI application, so
[FastAPI's deployment docs](https://fastapi.tiangolo.com/deployment/) cover
most of a deployment: workers, TLS, reverse proxies and Docker. This page
covers only what Restly adds.

In production, the server calls an application factory, `create_app()`, once
in each worker process. The factory reads the settings, builds the engine,
gives it to {func}`fr.configure() <fastapi_restly.db.configure>`, and
registers the views. Importing a module builds nothing, so Alembic and the
test suite can import your code without a configured environment. In
production, Alembic creates and changes the tables, not the application. Its
`env.py` imports `main.py`, which imports every model.

The sections below follow that order: the settings, the factory, the command
that runs it, the migrations, and a health check.
[New & Existing Projects](#compose-in-one-place) shows where these files sit
in the project.

(production-settings)=

## Read the database settings from the environment

In production, take the database URL and the pool size from environment
variables. A small `pydantic-settings` class holds them:

```python
import fastapi_restly as fr
from pydantic_settings import BaseSettings


class Settings(fr.utils.CurrentSettingsMixin, BaseSettings):
    database_url: str
    db_pool_size: int = 5
    db_max_overflow: int = 10
```

{class}`CurrentSettingsMixin <fastapi_restly.utils.CurrentSettingsMixin>`
gives the class one shared instance, but it does not build it when the module
is imported. So importing your package never needs a configured environment.
`Settings.current` reads `DATABASE_URL` and the pool fields the first time
that code asks for them. `Settings.use(...)` installs an instance instead;
a test suite does this to give the application its test settings, as
[Testing](howto_testing.md) shows.

Restly does not choose a pool size, so set it yourself. To do that, build the
engine yourself and pass it to `fr.configure()`, as the template below does.
Restly's [engine defaults](#engine-defaults) apply only to an engine that
Restly builds from a URL. For this reason, the template also sets
`pool_pre_ping=True` itself. If your application already builds an engine,
pass that one; see [Reuse your existing engine](#reuse-existing-engine).

(production-main-template)=

## A production `main.py` template

With these settings, the application factory looks like this:

```python
from contextlib import asynccontextmanager

import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from .settings import Settings
from .tasks.views import TaskView
from .users.views import UserView

VIEWS = (TaskView, UserView)


def create_app() -> FastAPI:
    settings = Settings.current
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
    for view in VIEWS:
        fr.include_view(app, view)
    return app
```

Three details matter in production:

- The factory reads the settings and builds the engine only when it runs. So
  a test suite can install its own settings first, and then call the same
  factory.
- The engine belongs to the app that the factory built. The lifespan disposes
  it, so the connections close and the workers exit quickly; see
  [Close the engine on shutdown](#engine-disposal).
- `fr.configure(app, ...)` also installs the handler that turns an
  `IntegrityError` into a 409 response; see
  [Database conflicts](#default-exception-handling). `health="/health"` adds
  the [health check](#health-checks).

`VIEWS` is the one place where the application registers its views;
[Compose in one place](#compose-in-one-place) explains it. The SaaS example
builds its application this way
([`app/main.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/main.py)).

(running-the-app)=

## Run the app

Use any production ASGI server. [Uvicorn](https://www.uvicorn.org/) is the
most common one. It can call the factory itself:

```bash
uvicorn "app.main:create_app" --factory --host 0.0.0.0 --port 8000 --workers 4
```

Some platforms and tools need an application object, not a factory. Put that
object in its own module, and leave `main.py` alone:

```python
# app/asgi.py
from .main import create_app

app = create_app()
```

Then `uvicorn app.asgi:app` works, and `main.py` can still be imported
without building anything. The FastAPI CLI cannot call a factory, so name
this module in `pyproject.toml`. Then `fastapi dev` and `fastapi run` work
too:

```toml
[tool.fastapi]
entrypoint = "app.asgi:app"
```

Do not put `app = create_app()` at the end of `main.py` instead. With
required settings, importing `main.py` would then need `DATABASE_URL`
everywhere, also in `conftest.py` and in `alembic/env.py`. Nothing in the
package should import `asgi.py`; only the server does.

Build the application only once in each process. Restly's database
configuration belongs to the process, so a second call to the factory points
the requests of every earlier app at the new database too; see
[one configuration per process](#one-configuration-per-process).

Sync {class}`RestView <fastapi_restly.views.RestView>` endpoints run on
FastAPI's thread pool. Async
{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` endpoints share
the event loop of their worker. In both cases, more workers serve more
requests at the same time. Do not use `--reload` in production.

(alembic)=

## Set up migrations with Alembic

In production, do not call `metadata.create_all()`. Use Alembic. For the
recommended `postgresql+asyncpg` setup, create the Alembic environment once,
with Alembic's async template, in the root of your project:

```bash
alembic init -t async alembic
```

The generic template that `alembic init alembic` creates builds a sync engine,
and it cannot open an async URL. Use it only when Alembic gets a separate
sync database URL. Alembic's
[asyncio recipe](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic)
shows the same template, and how to change an existing environment.

Then point `alembic/env.py` at the metadata of your declarative base, the
`Base` that your models inherit from. The metadata knows only the models that
have been imported. So `env.py` imports `main.py`: `main.py` imports every
view, and each view imports its models. Because the factory builds nothing
at import time, this import costs nothing else:

```python
# alembic/env.py
import app.main  # noqa: F401  (imports every view, and each view its model)
from app.models import Base

target_metadata = Base.metadata
```

A model that no view imports, such as an outbox table or an audit table, is
not in the metadata after that import. Import it at module level in the code
that uses it, not inside a function. If only a worker or a script uses it,
import it in `main.py` next to `VIEWS`. Run `alembic check` in CI: it reports
a missing model as a dropped table, so you see the mistake.

The `env.py` files of the
[starter](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/starter/alembic/env.py)
and the
[SaaS example](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/alembic/env.py)
are complete versions of this setup. They read the database URL from the
application's settings when Alembic gets none.

Run the migrations when you release, or when the application starts:

```bash
alembic upgrade head
```

Tests can run the same migrations: pass `alembic_upgrade=True` to
{func}`fr.testing.configure_tests() <fastapi_restly.testing.configure_tests>`;
see [Build the test schema](#test-databases-and-migrations).

(timezone-migration)=

### Give an existing datetime column a time zone

A base with {class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>`
maps a plain `Mapped[datetime]` column to `DateTime(timezone=True)`. On
PostgreSQL, you must say how to read the stored values when you change an
existing column without a time zone. If the values are in UTC, use a
migration such as this one, and review it:

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

A type change without `postgresql_using` can read the values in the local
time zone of the server. Use `mapped_column(DateTime())` on the model only for
a column that stores a time without a time zone on purpose.

(health-checks)=

## Add a health check

When you name a path in {func}`fr.configure() <fastapi_restly.db.configure>`,
Restly adds an endpoint there:

```python
fr.configure(app, async_engine=engine, health="/health")
```

`GET /health` then answers `200` with `{"status": "ok"}`, and it shows in
`/docs` like any other route. You choose the path; `/healthz` and `/up` are
also common. Without the argument, Restly adds no route. If your application
already has a route at that path, Restly leaves it alone.

This is a liveness check: it shows that the process is up and answers. It
does not query the database. It fits a probe that restarts the container
when the check fails:

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8000
```

A readiness probe takes the instance out of service instead of restarting it.
That is the place to check the database and other services. Write it as an
ordinary route, so that you control its checks and its timeouts.

## See also

- [Database & Configuration](database.md): engines, sessions and
  `fr.configure()`.
- [New & Existing Projects](howto_project_structure.md): the project layout,
  and Restly next to an existing app.
- The [SaaS example](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/saas):
  an example in production shape. It has PostgreSQL services in Compose,
  validated settings, an async engine that the application owns, migrations,
  and tests that run the migrations.
